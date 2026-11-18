"""MCP server over stdio (JSON-RPC 2.0, one message per line). No dependencies.

Generation takes minutes, which is longer than most clients wait for a tool call,
so generate_* start a background job and wait only `wait_seconds`; `get_job`
picks the result up later. One job runs at a time (one GPU).
"""
import base64
import io
import json
import os
import queue
import sys
import threading
import time
import traceback
import uuid

from . import comfy, meshcheck, models, pipeline, profiles
from .config import load_machine

PROTOCOL = "2025-06-18"
_out_lock = threading.Lock()
_jobs, _queue = {}, queue.Queue()


def _send(msg):
    with _out_lock:
        sys.__stdout__.write(json.dumps(msg) + "\n")
        sys.__stdout__.flush()


def _worker():
    while True:
        job = _jobs[_queue.get()]
        job["status"], job["started"] = "running", time.time()
        try:
            job["result"] = job["fn"]()
            job["status"] = job["result"].get("status", "ok")
        except Exception as e:
            job.update(status="error", error=f"{type(e).__name__}: {e}")
        job["finished"] = time.time()


def _submit(kind, name, fn, wait_seconds):
    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {"id": job_id, "kind": kind, "name": name, "status": "queued", "fn": fn, "created": time.time()}
    _queue.put(job_id)
    return _job_view(job_id, wait_seconds)


def _thumb(path, max_px=640):
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return {"type": "image", "data": base64.b64encode(buf.getvalue()).decode("ascii"), "mimeType": "image/jpeg"}
    except Exception:
        return None


def _job_view(job_id, wait_seconds=0):
    if job_id not in _jobs:
        raise ValueError(f"no job '{job_id}' (jobs are forgotten when the server restarts; the files and <name>.json stay on disk)")
    job = _jobs[job_id]
    deadline = time.time() + max(0, min(float(wait_seconds or 0), 55))
    while job["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(1)
    view = {k: job[k] for k in ("id", "kind", "name", "status") if k in job}
    images = []
    if job["status"] in ("queued", "running"):
        view["elapsed_s"] = round(time.time() - job.get("started", job["created"]))
        view["queue_position"] = sum(1 for j in _jobs.values() if j["status"] == "queued" and j["created"] <= job["created"])
        view["next"] = f"call get_job with id '{job_id}' and wait_seconds 50 until status is no longer queued/running"
    elif "error" in job:
        view["error"] = job["error"]
    else:
        view["result"] = job["result"]
        for key in ("preview", "image"):  # show the model render if there is one, else the picture
            path = job["result"].get(key)
            if path and os.path.isfile(path):
                img = _thumb(path)
                if img:
                    images.append(img)
                    break
    return view, images


# ----------------------------------------------------------------------------- tools

def t_status(a):
    m = load_machine()
    return {"gpu": m["gpu"], "ram_gb": m["ram_gb"], "vram_tier": m["tier"], "comfyui_running": comfy.is_up(),
            "comfyui": m["comfy_root"], "blender": m["blender"],
            "jobs": [{k: j[k] for k in ("id", "kind", "name", "status")} for j in _jobs.values()][-10:],
            "project": profiles.load_project(a.get("project_dir")) if a.get("project_dir") else None}, []


def t_list_profiles(a):
    return {"profiles": profiles.list_profiles(a.get("project_dir"))}, []


def t_generate_image(a):
    fn = lambda: pipeline.generate_image(a.get("profile", "illustration"), a["name"], a["prompt"], a.get("project_dir"),
                                         a.get("out_dir"), a.get("seed"), a.get("style", ""), a.get("params"), a.get("force", False))
    return _submit("image", a["name"], fn, a.get("wait_seconds", 50))


def t_generate_asset(a):
    if not a.get("prompt") and not a.get("image"):
        raise ValueError("give a prompt or an image path")
    fn = lambda: pipeline.generate_asset(a.get("profile", "prop_lowpoly"), a["name"], a.get("prompt"), a.get("image"),
                                         a.get("project_dir"), a.get("out_dir"), a.get("seed"), a.get("style", ""),
                                         a.get("params"), a.get("image_only", False), a.get("force", False),
                                         preset=a.get("preset", "standard"))
    return _submit("asset", a["name"], fn, a.get("wait_seconds", 50))


def t_get_job(a):
    return _job_view(a["id"], a.get("wait_seconds", 50))


def t_check_mesh(a):
    stats, problems = meshcheck.check_mesh(a["path"], a.get("target_triangles"))
    out = {"status": "ok" if not problems else "failed_check", "mesh": stats, "problems": problems}
    images = []
    if a.get("render", True):
        try:
            png = meshcheck.render_preview(a["path"], os.path.splitext(a["path"])[0] + "_preview.png")
            out["preview"] = png
            img = _thumb(png)
            images = [img] if img else []
        except Exception as e:
            out["preview_error"] = str(e)
    return out, images


def t_models(a):
    if a.get("install"):
        return {"installed": models.install(a["install"])}, []
    return models.report(a.get("project_dir")), []


_PROJECT = {"type": "string", "description": "Absolute path of the game project. Its assetforge.json (style, output folder, per-profile settings) is applied and files are written into its output folder."}
_WAIT = {"type": "number", "description": "Seconds to wait for the result before returning a job id (max 55, default 50)."}
_COMMON = {
    "name": {"type": "string", "description": "Asset id; becomes the file names."},
    "project_dir": _PROJECT,
    "out_dir": {"type": "string", "description": "Output folder; overrides the project's."},
    "style": {"type": "string", "description": "Art direction added to the prompt; overrides the project's style."},
    "seed": {"type": "integer", "description": "Fix the result; change it for a different take."},
    "params": {"type": "object", "description": "Profile parameters to override, e.g. {\"tris\": 5000, \"thin\": 0.9}. list_profiles shows what each profile accepts."},
    "force": {"type": "boolean", "description": "Regenerate even if the files exist."},
    "wait_seconds": _WAIT,
}

TOOLS = {
    "forge_status": (t_status, "Hardware, whether ComfyUI is running, recent jobs, and a project's settings.",
                     {"project_dir": _PROJECT}, []),
    "list_profiles": (t_list_profiles, "The available image and asset profiles with their parameters. Call this first to pick a profile.",
                      {"project_dir": _PROJECT}, []),
    "generate_image": (t_generate_image,
                       "Text -> image with an image profile (concept_prop, concept_character, icon, illustration). About 20-60 s. Returns the picture, or a job id to poll with get_job.",
                       {"profile": {"type": "string", "description": "Image profile id (default illustration)."},
                        "prompt": {"type": "string", "description": "What to draw. For concept_* profiles describe only the subject; framing and background are added."},
                        **_COMMON}, ["name", "prompt"]),
    "generate_asset": (t_generate_asset,
                       "Text or image -> game-ready .glb (low-poly mesh, normal map, PBR textures) with an asset profile, followed by an automatic mesh check and a rendered preview. Takes 3-8 minutes: this returns a job id, then poll get_job. Use image_only=true first to approve the concept picture cheaply.",
                       {"profile": {"type": "string", "description": "Asset profile id (default prop_lowpoly)."},
                        "prompt": {"type": "string", "description": "What the asset is: materials, colours, wear, proportions. Not needed when `image` is given or a concept image already exists for this name."},
                        "image": {"type": "string", "description": "Absolute path of a concept image to build from instead of generating one."},
                        "image_only": {"type": "boolean", "description": "Stop after the concept image."},
                        "preset": {"type": "string", "enum": ["fast", "standard", "max"],
                                   "description": "fast = lowest triangle count and quickest; standard = the profile's tuned default; max = highest quality (characters: the head is generated separately and fitted on, which takes several times longer)."},
                        **_COMMON}, ["name"]),
    "get_job": (t_get_job, "Status or result of a generate_* job. Finished asset jobs include the mesh check and a preview render.",
                {"id": {"type": "string"}, "wait_seconds": _WAIT}, ["id"]),
    "check_mesh": (t_check_mesh, "Run the mesh check on any .glb and render a front + three-quarter preview.",
                   {"path": {"type": "string", "description": "Absolute path of the .glb."},
                    "target_triangles": {"type": "integer"}, "render": {"type": "boolean", "description": "Also render a preview (default true)."}}, ["path"]),
    "models": (t_models, "Which model files each profile needs, what is missing, and advice for this GPU. Pass install=[file names] to download catalog files (large; ask the user first).",
               {"project_dir": _PROJECT, "install": {"type": "array", "items": {"type": "string"}, "description": "Catalog file names to download."}}, []),
}


def _handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        return {"protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "asset-forge", "version": "1.0.0"},
                "instructions": "Local text-to-image and text-to-3D game asset generation. Start with list_profiles. "
                                "Pass project_dir so the project's style and output folder apply. Asset jobs take minutes: poll get_job."}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props, "required": req}}
                          for n, (_f, d, props, req) in TOOLS.items()]}
    if method == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in TOOLS:
            raise LookupError(f"unknown tool {name}")
        try:
            data, images = TOOLS[name][0](args)
            return {"content": [{"type": "text", "text": json.dumps(data, indent=1)}, *images], "isError": False}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    if mid is None:
        return None  # a notification we do not need
    raise LookupError(f"method not found: {method}")


def serve():
    sys.stdout = sys.stderr  # nothing but protocol messages may reach the real stdout
    threading.Thread(target=_worker, daemon=True).start()
    pending = []
    for line in sys.stdin:
        line = line[line.find("{"):].strip() if "{" in line else ""  # tolerate a byte-order mark
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "method" not in msg:
            continue
        mid = msg.get("id")

        def run(msg=msg, mid=mid):
            try:
                result = _handle(msg)
                if mid is not None:
                    _send({"jsonrpc": "2.0", "id": mid, "result": result})
            except LookupError as e:
                if mid is not None:
                    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": str(e)}})
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                if mid is not None:
                    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})

        if msg["method"] == "tools/call":
            # tool calls may block while waiting on a job; keep the reader loop free
            t = threading.Thread(target=run, daemon=True)
            t.start()
            pending.append(t)
        else:
            run()
    for t in pending:  # stdin closed: let calls in flight answer before exiting
        t.join(timeout=60)
