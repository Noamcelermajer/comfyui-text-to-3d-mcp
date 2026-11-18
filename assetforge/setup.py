"""`forge.py setup` - install everything the bundled profiles need, without questions.

It is safe to run repeatedly: anything already present is skipped. It never removes
or downgrades anything and never touches the system Python - packages go into
ComfyUI's own interpreter.

What it does not install is the base software (ComfyUI, Blender); when those are
missing it stops and returns the exact commands to run instead.
"""
import os
import shutil
import subprocess
import sys

from . import comfy, models, profiles
from .config import load_machine, log


def _run(cmd, cwd=None, timeout=3600):
    log("  $ " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)[-1500:]


def base_software_commands():
    win = os.name == "nt"
    activate = r"venv\Scripts\python.exe" if win else "venv/bin/python"
    return {
        "comfyui": [
            "git clone https://github.com/comfyanonymous/ComfyUI",
            "cd ComfyUI",
            ("py -3.12 -m venv venv" if win else "python3.12 -m venv venv"),
            f"{activate} -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128",
            f"{activate} -m pip install -r requirements.txt",
            "then set the environment variable ASSETFORGE_COMFY to this ComfyUI folder and run: python forge.py setup",
        ],
        "blender": (["winget install --id BlenderFoundation.Blender -e --accept-source-agreements --accept-package-agreements"]
                    if win else ["sudo snap install blender --classic", "(macOS: brew install --cask blender)"])
                   + ["then run: python forge.py doctor   (or set \"blender\" in machine.json to the executable)"],
    }


def install_node_packs(pinned=False, dry_run=False):
    m = load_machine()
    custom = os.path.join(m["comfy_root"], "custom_nodes")
    py, results = m["comfy_python"], []
    for pack in models.catalog().get("node_packs", []):
        dst = os.path.join(custom, pack["folder"])
        if os.path.isdir(dst):
            results.append({"pack": pack["folder"], "status": "already_installed"})
            continue
        if dry_run:
            results.append({"pack": pack["folder"], "status": "would_install", "url": pack["url"]})
            continue
        log(f"installing node pack {pack['folder']} ...")
        code, out = _run(["git", "clone", "--depth", "1" if not pinned else "50", pack["url"], dst])
        if code != 0:
            results.append({"pack": pack["folder"], "status": "error", "step": "git clone", "output": out})
            continue
        if pinned and pack.get("tested_commit"):
            _run(["git", "-C", dst, "checkout", "-q", pack["tested_commit"]])
        entry = {"pack": pack["folder"], "status": "installed", "steps": []}
        # A pack's own installer knows about its compiled wheels; prefer it over requirements.txt.
        if os.path.isfile(os.path.join(dst, "install.py")):
            code, out = _run([py, "install.py"], cwd=dst)
            entry["steps"].append({"install.py": "ok" if code == 0 else out})
        elif os.path.isfile(os.path.join(dst, "requirements.txt")):
            code, out = _run([py, "-m", "pip", "install", "-r", "requirements.txt"], cwd=dst)
            entry["steps"].append({"requirements.txt": "ok" if code == 0 else out})
        if pack.get("extra_pip"):
            code, out = _run([py, "-m", "pip", "install", *pack["extra_pip"]])
            entry["steps"].append({"extra_pip": "ok" if code == 0 else out})
        if any(v != "ok" for s in entry["steps"] for v in s.values()):
            entry["status"] = "installed_with_errors"
        results.append(entry)
    return results


def install_models(dry_run=False, include_unfit=False):
    """Download every missing file needed by the profiles that fit this GPU."""
    rep = models.report()
    wanted, skipped = {}, []
    for p in rep["profiles"]:
        if not p["fits_this_gpu"] and not include_unfit:
            if p["missing"]:
                skipped.append({"profile": p["profile"], "reason": f"needs {p['min_vram_gb']} GB VRAM"})
            continue
        for f in p["missing"]:
            wanted[f["file"]] = f
    total = round(sum(f.get("size_gb") or 0 for f in wanted.values()), 1)
    if dry_run or not wanted:
        return {"missing_files": sorted(wanted), "download_gb": total, "skipped_profiles": skipped,
                "results": [{"file": f, "status": "would_download", "size_gb": v.get("size_gb")} for f, v in wanted.items()]}
    free = shutil.disk_usage(models.models_dir()).free / 2**30
    if free < total + 5:
        return {"missing_files": sorted(wanted), "download_gb": total, "skipped_profiles": skipped,
                "results": [], "error": f"not enough disk space: {free:.0f} GB free, {total} GB needed"}
    return {"missing_files": sorted(wanted), "download_gb": total, "skipped_profiles": skipped,
            "results": models.install(sorted(wanted))}


def setup(dry_run=False, skip_models=False, skip_nodes=False, pinned=False, include_unfit=False):
    m = load_machine(refresh=True)
    out = {"status": "ok", "dry_run": dry_run, "machine": {"gpu": m["gpu"], "ram_gb": m["ram_gb"], "tier": m["tier"]},
           "manual_steps": []}
    cmds = base_software_commands()
    if not m["comfy_root"]:
        out.update(status="blocked", reason="ComfyUI was not found")
        out["manual_steps"].append({"what": "Install ComfyUI (or point Asset Forge at an existing one with the "
                                            "ASSETFORGE_COMFY environment variable), then run setup again.",
                                    "commands": cmds["comfyui"]})
        return out
    if not m["blender"]:
        out["manual_steps"].append({"what": "Install Blender; it is needed for low-poly clean-up, baking and previews.",
                                    "commands": cmds["blender"]})
    if m["gpu"]["vram_gb"] < 7.5:
        out["manual_steps"].append({"what": f"The GPU reports {m['gpu']['vram_gb']} GB of VRAM; 8 GB is the minimum. "
                                            "Generation will probably fail."})

    if not skip_nodes:
        out["node_packs"] = install_node_packs(pinned, dry_run)
    if not skip_models:
        out["models"] = install_models(dry_run, include_unfit)
        for r in out["models"].get("results", []):
            if r.get("status") in ("no_url", "error"):
                out["manual_steps"].append({"what": f"Could not fetch {r['file']}: {r.get('note') or r.get('error')}"})

    installed_packs = [p for p in out.get("node_packs", []) if p["status"].startswith("installed")]
    if installed_packs and comfy.is_up():
        out["manual_steps"].append({"what": "ComfyUI is running and new node packs were installed: restart ComfyUI so it loads them."})
    bad = [p for p in out.get("node_packs", []) if p["status"] in ("error", "installed_with_errors")]
    if bad or out.get("models", {}).get("error") or any(r.get("status") == "error" for r in out.get("models", {}).get("results", [])):
        out["status"] = "partial"

    if not dry_run:  # what is still missing after this run
        rep = models.report()
        out["profiles_ready"] = {p["profile"]: p["ready"] for p in rep["profiles"]}
    out["next"] = ("Run `python forge.py doctor` (start ComfyUI first so node types are verified too); every profile should "
                   "show \"ready\": true. Then `python forge.py connect` to register the MCP server and skill, and "
                   "`python forge.py asset setup_test \"a chunky wooden treasure chest with iron bands\"` to prove it works.")
    return out
