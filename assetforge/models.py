"""Which model files each profile needs, which are present, and how to fetch the rest.

Needs are read straight from a profile's graph (every *.safetensors / *.gguf / *.pt
name in it), so a new workflow reports its own requirements with no extra listing.
"""
import json
import os
import urllib.request

from . import profiles
from .config import FORGE_ROOT, load_machine, log

MODEL_EXTS = (".safetensors", ".gguf", ".pt", ".pth", ".ckpt", ".bin")


def catalog():
    return json.load(open(os.path.join(FORGE_ROOT, "models", "catalog.json"), encoding="utf-8-sig"))


def models_dir():
    return os.path.join(load_machine()["comfy_root"], "models")


def _installed_index():
    index = {}
    for root, _dirs, files in os.walk(models_dir()):
        for f in files:
            if f.lower().endswith(MODEL_EXTS):
                index.setdefault(f, os.path.join(root, f))
    return index


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def files_for_graph(path):
    g = json.load(open(path, encoding="utf-8-sig"))
    nodes = [n for n in g["nodes"] if n.get("mode", 0) not in (2, 4)] if "nodes" in g else list(g.values())
    names = set()
    for n in nodes:
        for s in _strings(n.get("widgets_values", n.get("inputs"))):
            if s.lower().endswith(MODEL_EXTS) and len(s) < 200:
                names.add(os.path.basename(s.replace("\\", "/")))
    return sorted(names)


def profile_status(profile_id, project_dir=None, index=None):
    """Files a profile needs (including its concept-image profile) and which are missing."""
    index = _installed_index() if index is None else index
    cat = catalog()["files"]
    p = profiles.resolve(profile_id, project_dir)
    needed = files_for_graph(profiles.graph_path(p))
    if p.get("image_profile"):
        needed = sorted(set(needed) | set(files_for_graph(profiles.graph_path(profiles.resolve(p["image_profile"], project_dir)))))
    missing = [{"file": f, **{k: cat.get(f, {}).get(k) for k in ("folder", "size_gb", "url", "note", "role")}}
               for f in needed if f not in index]
    m = load_machine()
    return {"profile": profile_id, "ready": not missing, "needs": needed, "missing": missing,
            "fits_this_gpu": m["gpu"]["vram_gb"] + 0.5 >= p.get("min_vram_gb", 0), "min_vram_gb": p.get("min_vram_gb", 0)}


def report(project_dir=None):
    index = _installed_index()
    m = load_machine()
    cat = catalog()
    return {
        "gpu": m["gpu"], "ram_gb": m["ram_gb"], "tier": m["tier"],
        "guide_for_this_tier": cat["hardware_guide"].get(str(m["tier"]), {}),
        "profiles": [profile_status(p["id"], project_dir, index) for p in profiles.list_profiles(project_dir)],
        "optional_not_installed": [{"file": f, **meta} for f, meta in cat["files"].items() if f not in index],
    }


def _install_from_modelscope(name, meta):
    """Some weights are only on ModelScope, under a generic file name: fetch with its
    CLI (installed into ComfyUI's Python on first use) and store under the right name."""
    import shutil
    import subprocess
    import tempfile

    dst = os.path.join(models_dir(), meta["folder"], name)
    if os.path.isfile(dst):
        return {"file": name, "status": "already_installed", "path": dst}
    py, src = load_machine()["comfy_python"], meta["modelscope"]
    log(f"downloading {name} from ModelScope ({src['model']}) ...")
    try:
        if subprocess.run([py, "-c", "import modelscope"], capture_output=True).returncode != 0:
            subprocess.run([py, "-m", "pip", "install", "modelscope"], capture_output=True, check=True, timeout=900)
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([py, "-m", "modelscope.cli.cli", "download", "--model", src["model"], src["file"],
                                "--local_dir", tmp], capture_output=True, text=True, timeout=7200)
            got = os.path.join(tmp, *src["file"].split("/"))
            if not os.path.isfile(got):
                return {"file": name, "status": "error", "error": "ModelScope download failed: " + (r.stderr or r.stdout)[-400:],
                        "manual": f"pip install modelscope; modelscope download --model {src['model']} {src['file']} "
                                  f"--local_dir tmp; then copy tmp/{src['file']} to models/{meta['folder']}/{name}"}
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(got, dst)
        return {"file": name, "status": "installed", "path": dst}
    except Exception as e:
        return {"file": name, "status": "error", "error": str(e)}


def install(file_names):
    """Download catalog files into the ComfyUI models folder. Returns one result per file."""
    cat = catalog()["files"]
    results = []
    for name in file_names:
        meta = cat.get(name)
        if meta and not meta.get("url") and meta.get("modelscope"):
            results.append(_install_from_modelscope(name, meta))
            continue
        if not meta or not meta.get("url"):
            results.append({"file": name, "status": "no_url", "note": (meta or {}).get("note", "not in the catalog")})
            continue
        dst = os.path.join(models_dir(), meta["folder"], name)
        if os.path.isfile(dst):
            results.append({"file": name, "status": "already_installed", "path": dst})
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        log(f"downloading {name} ({meta.get('size_gb')} GB) ...")
        try:
            req = urllib.request.Request(meta["url"], headers={"User-Agent": "asset-forge"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(dst + ".part", "wb") as out:
                while True:
                    chunk = resp.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            os.replace(dst + ".part", dst)
            results.append({"file": name, "status": "installed", "path": dst})
        except Exception as e:
            results.append({"file": name, "status": "error", "error": str(e)})
    return results
