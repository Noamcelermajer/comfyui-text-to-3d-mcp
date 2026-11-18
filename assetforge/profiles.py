"""Profiles: data files that describe one way of making an image or a 3D asset.

Settings are layered, later layers win:
  1. the profile file (and whatever it `extends`)
  2. the profile's block for this machine's VRAM tier      ("tiers": {"8": {...}})
  3. the project's assetforge.json                          ("profiles": {"<id>": {...}})
  4. the arguments of the call

Profiles are looked up in:  AssetForge/profiles, AssetForge/profiles/user,
and <project>/assetforge_profiles (a project profile with the same id wins).
"""
import glob
import json
import os

from .config import FORGE_ROOT, deep_merge, load_machine

PROJECT_FILE = "assetforge.json"


def load_project(project_dir):
    if not project_dir:
        return {}
    path = os.path.join(project_dir, PROJECT_FILE)
    return json.load(open(path, encoding="utf-8-sig")) if os.path.isfile(path) else {}


def _raw_profiles(project_dir=None):
    dirs = [os.path.join(FORGE_ROOT, "profiles"), os.path.join(FORGE_ROOT, "profiles", "user")]
    if project_dir:
        dirs.append(os.path.join(project_dir, "assetforge_profiles"))
    found = {}
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(d, "*.json"))):
            p = json.load(open(path, encoding="utf-8-sig"))
            p["_dir"] = os.path.dirname(path)
            found[p.get("id") or os.path.splitext(os.path.basename(path))[0]] = p
    return found


def _extended(pid, raw, seen=()):
    if pid not in raw:
        raise KeyError(f"unknown profile '{pid}'. Known: {', '.join(sorted(raw))}")
    if pid in seen:
        raise ValueError(f"profile '{pid}' extends itself")
    p = raw[pid]
    if p.get("extends"):
        base = _extended(p["extends"], raw, seen + (pid,))
        merged = deep_merge(base, {k: v for k, v in p.items() if k != "extends"})
        if "graph" not in p:  # an inherited graph path stays relative to the parent's folder
            merged["_dir"] = base["_dir"]
        return merged
    return dict(p)


def resolve(pid, project_dir=None, params=None):
    """Return the profile with every layer applied; `params` are the call's overrides."""
    raw = _raw_profiles(project_dir)
    p = _extended(pid, raw)
    p["id"] = pid
    tier_block = (p.get("tiers") or {}).get(str(load_machine()["tier"]), {})
    project = load_project(project_dir)
    proj_block = (project.get("profiles") or {}).get(pid, {})
    for layer in (tier_block, proj_block):
        p["params"] = {**p.get("params", {}), **layer.get("params", {})}
        p["set"] = {**p.get("set", {}), **layer.get("set", {})}
    unknown = [k for k in (params or {}) if k not in p["params"]]
    if unknown:
        raise ValueError(f"profile '{pid}' has no parameter {unknown}. It accepts: {', '.join(sorted(p['params']))}")
    p["params"] = {**p["params"], **(params or {})}
    p["style"] = project.get("style", "")
    return p


def graph_path(profile):
    g = profile["graph"]
    for base in (profile.get("_dir", ""), FORGE_ROOT):
        cand = os.path.normpath(os.path.join(base, g))
        if os.path.isfile(cand):
            return cand
    raise FileNotFoundError(f"graph '{g}' of profile '{profile['id']}' was not found")


def list_profiles(project_dir=None):
    raw = _raw_profiles(project_dir)
    tier = str(load_machine()["tier"])
    out = []
    for pid in sorted(raw):
        if raw[pid].get("abstract"):
            continue
        p = _extended(pid, raw)
        params = {**p.get("params", {}), **(p.get("tiers") or {}).get(tier, {}).get("params", {})}
        out.append({"id": pid, "kind": p.get("kind"), "title": p.get("title", ""), "description": p.get("description", ""),
                    "status": p.get("status", "experimental"), "min_vram_gb": p.get("min_vram_gb", 0),
                    "params": params, "requires": p.get("requires", []),
                    "image_profile": p.get("image_profile")})
    return out
