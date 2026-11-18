"""Talking to ComfyUI: start it, run a graph, time each node, free VRAM."""
import asyncio
import glob
import json
import os
import subprocess
import time
import urllib.request
import uuid

from .config import FORGE_ROOT, load_machine, log

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO", "COLOR", "LOAD_3D", "COMFY_DYNAMICCOMBO_V3", "BOUNDING_BOX"}


class ComfyError(RuntimeError):
    pass


def url():
    return load_machine()["comfy_url"]


def get(path, timeout=15):
    with urllib.request.urlopen(f"{url()}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_up():
    try:
        get("/system_stats", timeout=4)
        return True
    except Exception:
        return False


def ensure_server(wait_s=300):
    if is_up():
        return
    m = load_machine()
    if not m["comfy_root"]:
        raise ComfyError("ComfyUI was not found. Set comfy_root in machine.json.")
    log("ComfyUI is not running - starting it ...")
    cmd = [m["comfy_python"], "-I", "-W", "ignore::FutureWarning", os.path.join(m["comfy_root"], "main.py"),
           "--port", m["comfy_url"].rsplit(":", 1)[-1], *m.get("comfy_launch_args", [])]
    logf = open(os.path.join(FORGE_ROOT, "comfyui.log"), "ab")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(cmd, cwd=os.path.dirname(m["comfy_root"]), stdout=logf, stderr=logf,
                     stdin=subprocess.DEVNULL, creationflags=flags)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(3)
        if is_up():
            return
    raise ComfyError("ComfyUI did not come up in time; see comfyui.log")


_object_info = {}


def object_info():
    if not _object_info:
        _object_info.update(get("/object_info", timeout=90))
    return _object_info


def free_vram():
    """Drop cached weights. Mesh nodes allocate VRAM outside ComfyUI's model
    manager, so leftover weights from another stage make them run out."""
    req = urllib.request.Request(
        f"{url()}/free", data=json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()
    time.sleep(2)


async def _run_ws(prompt):
    import aiohttp

    client_id = uuid.uuid4().hex
    base = url()
    timings = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
        async with session.ws_connect(base.replace("http", "ws", 1) + f"/ws?clientId={client_id}", max_msg_size=0) as ws:
            async with session.post(f"{base}/prompt", json={"prompt": prompt, "client_id": client_id}) as r:
                body = await r.json(content_type=None)
                if r.status != 200:
                    raise ComfyError("ComfyUI rejected the graph: " + json.dumps(body)[:1500])
                prompt_id = body["prompt_id"]
            current, t_last = None, None

            def close(now):
                if current is not None and t_last is not None:
                    timings[current] = timings.get(current, 0.0) + now - t_last

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                m = json.loads(msg.data)
                d = m.get("data") or {}
                if d.get("prompt_id") != prompt_id:
                    continue
                now, kind = time.time(), m.get("type")
                if kind == "execution_start":
                    t_last = now
                elif kind == "executing":
                    close(now)
                    current, t_last = d.get("node"), now
                    if current is None:
                        break
                elif kind == "execution_success":
                    close(now)
                    break
                elif kind == "execution_error":
                    raise ComfyError(f"node {d.get('node_id')} ({d.get('node_type')}) failed: "
                                     f"{d.get('exception_type')}: {str(d.get('exception_message'))[:500]}")
                elif kind == "execution_interrupted":
                    raise ComfyError("the run was interrupted in ComfyUI")
    return prompt_id, timings


def _run_poll(prompt):
    req = urllib.request.Request(f"{url()}/prompt", data=json.dumps({"prompt": prompt}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        prompt_id = json.loads(urllib.request.urlopen(req).read())["prompt_id"]
    except urllib.error.HTTPError as e:
        raise ComfyError("ComfyUI rejected the graph: " + e.read().decode("utf-8", "replace")[:1500])
    return prompt_id, {}


def run_graph(prompt):
    """Run an API-format graph to completion. Returns (seconds, [(node, seconds)...])."""
    t0 = time.time()
    try:
        import aiohttp  # noqa: F401
        prompt_id, timings = asyncio.run(_run_ws(prompt))
    except ImportError:  # plain Python without aiohttp: no per-node timings
        prompt_id, timings = _run_poll(prompt)
    while True:  # the history is the source of truth for completion
        status = get(f"/history/{prompt_id}").get(prompt_id, {}).get("status", {})
        if status.get("completed"):
            break
        if status.get("status_str") == "error":
            msgs = [m[1] for m in status.get("messages", []) if m and m[0] == "execution_error"]
            d = msgs[0] if msgs else {}
            raise ComfyError(f"node {d.get('node_id')} ({d.get('node_type')}) failed: "
                             f"{d.get('exception_type')}: {str(d.get('exception_message'))[:500]}")
        time.sleep(2)
    steps = sorted(((f"{nid}:{prompt.get(nid, {}).get('class_type', '?')}", round(s, 1)) for nid, s in timings.items()),
                   key=lambda kv: -kv[1])
    return round(time.time() - t0, 1), steps


def input_dir():
    return os.path.join(load_machine()["comfy_root"], "input")


def newest_output(prefix, ext):
    out = os.path.join(load_machine()["comfy_root"], "output")
    files = glob.glob(os.path.join(out, f"{prefix}*{ext}"))
    return max(files, key=os.path.getmtime) if files else None


def ui_to_api(wf, obj_info):
    """Convert a workflow saved from the ComfyUI editor (nodes + links) to API format.
    Bypassed/muted nodes and notes are dropped; reroutes are followed."""
    links = {l[0]: (str(l[1]), int(l[2])) for l in wf.get("links", [])}
    nodes = {str(n["id"]): n for n in wf.get("nodes", [])}

    def resolve(link_id):
        seen = set()
        while link_id in links and link_id not in seen:
            seen.add(link_id)
            src_id, slot = links[link_id]
            src = nodes.get(src_id)
            if not src:
                return None
            if src.get("type") == "Reroute":
                ins = src.get("inputs", [])
                if ins and ins[0].get("link") is not None:
                    link_id = ins[0]["link"]
                    continue
                return None
            if src.get("mode", 0) in (2, 4):
                return None
            return [src_id, slot]
        return None

    primitives = {nid: n["widgets_values"][0] for nid, n in nodes.items()
                  if n.get("type") in ("PrimitiveString", "PrimitiveBoolean", "PrimitiveNode")
                  and n.get("type") not in obj_info and n.get("widgets_values")}

    api = {}
    for nid, node in nodes.items():
        ntype = node.get("type", "")
        if node.get("mode", 0) in (2, 4) or ntype in ("Note", "MarkdownNote", "Reroute") or ntype not in obj_info:
            continue
        defs = obj_info[ntype].get("input", {})
        ordered = [(k, v) for sec in ("required", "optional") for k, v in defs.get(sec, {}).items()]
        linked, widget_names = {}, set()
        for inp in node.get("inputs", []):
            if inp.get("widget"):
                widget_names.add(inp["name"])
            if inp.get("link") is not None:
                res = resolve(inp["link"])
                if res:
                    linked[inp["name"]] = primitives.get(res[0], res)
        widgets = list(node.get("widgets_values") or []) if isinstance(node.get("widgets_values"), list) else []
        w, inputs = 0, {}
        for name, spec in ordered:
            stype = spec[0] if isinstance(spec, list) and spec else None
            meta = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
            if not (isinstance(stype, list) or stype in WIDGET_TYPES or name in widget_names):
                if name in linked:
                    inputs[name] = linked[name]
                continue
            val = None
            if w < len(widgets):
                val = widgets[w]
                w += 1
                if name in ("seed", "noise_seed") and w < len(widgets) and widgets[w] in ("fixed", "increment", "decrement", "randomize"):
                    w += 1
            elif "default" in meta:
                val = meta["default"]
            if name in linked:
                inputs[name] = linked[name]
            elif val is not None:
                inputs[name] = val
            # An option menu whose choice brings its own settings: the editor stores those
            # settings' values right after the choice; the API wants them as "menu.setting".
            if stype == "COMFY_DYNAMICCOMBO_V3":
                option = next((o for o in meta.get("options", []) if o.get("key") == inputs.get(name)), None)
                subs = [(k, v) for sec in ("required", "optional") for k, v in ((option or {}).get("inputs", {}).get(sec, {})).items()]
                for sub, sub_spec in subs:
                    sub_type = sub_spec[0] if isinstance(sub_spec, list) and sub_spec else None
                    sub_meta = sub_spec[1] if isinstance(sub_spec, list) and len(sub_spec) > 1 and isinstance(sub_spec[1], dict) else {}
                    key = f"{name}.{sub}"
                    if key in linked:
                        inputs[key] = linked[key]
                    elif isinstance(sub_type, list) or sub_type in WIDGET_TYPES:
                        if w < len(widgets):
                            inputs[key] = widgets[w]
                            w += 1
                        elif "default" in sub_meta:
                            inputs[key] = sub_meta["default"]
        api[nid] = {"class_type": ntype, "inputs": inputs}
    return api
