"""The engine: run a profile, and the two public operations built on it -
generate_image (text -> picture) and generate_asset (text or picture -> checked .glb)."""
import json
import os
import random
import re
import shutil
import uuid

from . import comfy, meshcheck, profiles, split
from .config import load_machine, log

OUT_SUBFOLDER = "assetforge"
_graph_cache = {}
_vram = {"group": None}  # which profile family's weights are in VRAM right now


def safe_name(name):
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(name).strip()).strip("_")
    if not name:
        raise ValueError("name must contain letters or digits")
    return name


def out_dir_for(project_dir=None, out_dir=None):
    if out_dir:
        return os.path.abspath(out_dir)
    if project_dir:
        return os.path.abspath(os.path.join(project_dir, profiles.load_project(project_dir).get("output_dir", "assets/generated")))
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")


def _load_graph(profile):
    path = profiles.graph_path(profile)
    key = (path, os.path.getmtime(path))
    if key not in _graph_cache:
        g = json.load(open(path, encoding="utf-8-sig"))
        _graph_cache[key] = comfy.ui_to_api(g, comfy.object_info()) if "nodes" in g else g
    return json.loads(json.dumps(_graph_cache[key]))


def _fill(value, ctx):
    """'{name}' alone keeps the value's type; inside a longer string it is formatted in."""
    if not isinstance(value, str):
        return value
    m = re.fullmatch(r"\{(\w+)\}", value)
    if m:
        if m.group(1) not in ctx:
            raise KeyError(f"profile uses {{{m.group(1)}}} but no such value exists")
        return ctx[m.group(1)]
    return re.sub(r"\{(\w+)\}", lambda k: str(ctx.get(k.group(1), k.group(0))), value)


def run_profile(profile, ctx, run_tag):
    """Apply the profile's `set` map to its graph, run it, and collect the outputs."""
    g = _load_graph(profile)
    assignments = dict(profile.get("set", {}))
    for target in profile.get("seed_inputs", []):
        assignments[target] = "{seed}"
    for target, value in assignments.items():
        node, _, key = target.partition(".")
        if node in g:  # a profile may name nodes that a variant graph does not have
            g[node]["inputs"][key] = _fill(value, ctx)
    blender = load_machine().get("blender") or "blender"
    for node in g.values():  # shipped workflows carry a neutral path
        if "blender_path" in node["inputs"]:
            node["inputs"]["blender_path"] = blender
    # Whole-node swaps (e.g. a GPU node in place of a CPU one). null keeps the original.
    for node, spec in (profile.get("replace_nodes") or {}).items():
        if spec and node in g:
            g[node] = {"class_type": spec["class_type"],
                       "inputs": {k: _fill(v, ctx) for k, v in spec["inputs"].items()}}
    outputs = profile.get("outputs", {})
    for tag, spec in outputs.items():
        if spec["node"] in g:
            g[spec["node"]]["inputs"][spec.get("input", "filename_prefix")] = f"{OUT_SUBFOLDER}/{run_tag}_{tag}"

    group = profile.get("vram_group")
    if group != _vram["group"]:
        comfy.free_vram()
    try:
        seconds, steps = comfy.run_graph(g)
    except comfy.ComfyError as e:
        if "OutOfMemory" not in str(e):
            raise
        log("out of GPU memory - freeing VRAM and retrying once ...")
        comfy.free_vram()
        seconds, steps = comfy.run_graph(g)
    _vram["group"] = group

    files = {tag: comfy.newest_output(f"{OUT_SUBFOLDER}/{run_tag}_{tag}", spec["ext"])
             for tag, spec in outputs.items() if spec.get("keep", True)}
    return files, seconds, steps


def build_prompt(profile, prompt, style=""):
    style = (style or profile.get("style") or "").strip()
    text = profile.get("prompt_template", "{prompt}").replace("{prompt}", prompt.strip().rstrip("."))
    text = text.replace("{style}", (style.rstrip(".") + ".") if style else "")
    return re.sub(r"\s{2,}", " ", text).strip()


def generate_image(profile_id, name, prompt, project_dir=None, out_dir=None, seed=None, style="",
                   params=None, force=False, source_image=None):
    """Text -> image with an image profile. Returns a manifest dict."""
    name = safe_name(name)
    p = profiles.resolve(profile_id, project_dir, params)
    if p.get("kind") not in ("image", "image_edit"):
        raise ValueError(f"'{profile_id}' is not an image profile")
    if p["kind"] == "image_edit" and not source_image:
        raise ValueError(f"'{profile_id}' edits a picture: give source_image")
    out = out_dir_for(project_dir, out_dir)
    os.makedirs(out, exist_ok=True)
    dst = os.path.join(out, f"{name}.png")
    if os.path.isfile(dst) and not force:
        return {"name": name, "status": "ok", "cached": True, "image": dst, "profile": profile_id}
    comfy.ensure_server()
    seed = random.randrange(1, 2**31) if seed is None else int(seed)
    full = build_prompt(p, prompt, style)
    ctx = {**p["params"], "prompt": prompt, "full_prompt": full, "seed": seed, "name": name}
    run_tag = f"{name}_{uuid.uuid4().hex[:6]}"
    if source_image:
        ctx["image_file"] = f"{run_tag}_src{os.path.splitext(source_image)[1].lower()}"
        os.makedirs(comfy.input_dir(), exist_ok=True)
        shutil.copyfile(source_image, os.path.join(comfy.input_dir(), ctx["image_file"]))
    log(f"[{name}] image via {profile_id} ...")
    files, seconds, steps = run_profile(p, ctx, run_tag)
    if not files.get("image"):
        raise comfy.ComfyError("the image run finished but wrote no image")
    shutil.copyfile(files["image"], dst)
    return {"name": name, "status": "ok", "profile": profile_id, "image": dst, "seed": seed,
            "prompt": prompt, "image_prompt": full, "seconds": seconds}


def generate_asset(profile_id, name, prompt=None, image=None, project_dir=None, out_dir=None, seed=None,
                   style="", params=None, image_only=False, force=False, preview=True, preset="standard"):
    """Text or image -> checked .glb with an asset profile. Returns a manifest dict."""
    name = safe_name(name)
    p = profiles.resolve(profile_id, project_dir, params, preset)
    if p.get("kind") != "asset":
        raise ValueError(f"'{profile_id}' is not an asset profile")
    out = out_dir_for(project_dir, out_dir)
    os.makedirs(out, exist_ok=True)
    manifest_path = os.path.join(out, f"{name}.json")
    final_glb, concept = os.path.join(out, f"{name}.glb"), os.path.join(out, f"{name}.png")

    if not force and not image_only and os.path.isfile(final_glb) and os.path.isfile(manifest_path):
        old = json.load(open(manifest_path, encoding="utf-8-sig"))
        if old.get("preset", "standard") == p["preset"]:  # a different preset is a different asset
            return {**old, "cached": True}

    comfy.ensure_server()
    seed = random.randrange(1, 2**31) if seed is None else int(seed)
    result = {"name": name, "profile": profile_id, "preset": p["preset"], "status": "error", "seed": seed,
              "timings": {}, "attempts": []}

    # Stage 1 - concept image: supplied, reused from an earlier image-only run, or generated.
    if image:
        if not os.path.isfile(image):
            raise FileNotFoundError(f"image not found: {image}")
        if os.path.abspath(image) != os.path.abspath(concept):
            shutil.copyfile(image, concept)
        result["image_source"] = "supplied"
    elif os.path.isfile(concept) and not (force and prompt) and not image_only:
        result["image_source"] = "reused"
    else:
        if not prompt:
            raise ValueError("give a prompt or an image")
        img = generate_image(p["image_profile"], name, prompt, project_dir, out, seed, style,
                             params=p.get("image_params"), force=True)
        result.update(image_source="generated", prompt=prompt, image_prompt=img["image_prompt"])
        result["timings"]["image_s"] = img["seconds"]
    result["image"] = concept
    if image_only:
        result["status"] = "image_only"
        return result

    # Stage 2 - 3D, re-running with adjusted parameters when the mesh check fails.
    retry = p.get("retry", {})
    target = p["params"].get(p.get("check", {}).get("target_param", "tris"))
    run_tag = f"{name}_{uuid.uuid4().hex[:6]}"
    in_name = f"{run_tag}{os.path.splitext(concept)[1].lower()}"
    os.makedirs(comfy.input_dir(), exist_ok=True)
    shutil.copyfile(concept, os.path.join(comfy.input_dir(), in_name))
    for attempt in range(int(retry.get("attempts", 0)) + 1):
        vals = dict(p["params"])
        for key, step in retry.get("add", {}).items():
            vals[key] = round(vals[key] + step * attempt, 4)
        a_seed = seed + attempt * 7919 if retry.get("reseed", True) else seed
        ctx = {**vals, "seed": a_seed, "image_file": in_name, "name": name, "blender": load_machine().get("blender", "blender")}
        log(f"[{name}] 3D via {profile_id}, attempt {attempt + 1} ...")
        files, seconds, steps = run_profile(p, ctx, f"{run_tag}_a{attempt}")
        primary = files.get(p.get("primary_output", "glb"))
        if not primary:
            raise comfy.ComfyError("the 3D run finished but wrote no .glb")
        stats, problems = (meshcheck.check_mesh(primary, target, p.get("check", {}).get("limits"))
                           if p.get("check", {}).get("enabled", True) else ({}, []))
        result["attempts"].append({"seed": a_seed, "seconds": seconds, "problems": problems, "slowest_steps": steps[:6],
                                   "params": {k: vals[k] for k in retry.get("add", {})}})
        result["timings"]["model_s"] = round(result["timings"].get("model_s", 0) + seconds, 1)
        result.update(mesh=stats, problems=problems, params=vals)
        shutil.copyfile(primary, final_glb)
        result["files"] = {}
        for tag, src in files.items():
            if src and tag != p.get("primary_output", "glb"):
                dst = os.path.join(out, f"{name}_{tag}{os.path.splitext(src)[1]}")
                shutil.copyfile(src, dst)
                result["files"][tag] = dst
        if not problems:
            break
        log(f"[{name}] mesh check failed: {'; '.join(problems)}")

    result["glb"] = final_glb
    result["status"] = "ok" if not result["problems"] else "failed_check"

    # Stage 3 (max preset) - build parts on their own and fit them onto the base model.
    if p.get("split") and result["status"] == "ok":
        result["split"] = _split_stage(p, name, concept, final_glb, out, seed, project_dir, result)
        if result["split"].get("assembled"):
            stats, problems = meshcheck.check_mesh(final_glb, None, p.get("check", {}).get("limits"))
            result.update(mesh=stats, problems=problems, status="ok" if not problems else "failed_check")
    if preview:
        try:
            result["preview"] = meshcheck.render_preview(final_glb, os.path.join(out, f"{name}_preview.png"))
        except Exception as e:
            result["preview_error"] = str(e)
    json.dump(result, open(manifest_path, "w", encoding="utf-8"), indent=2)
    return result


def _split_stage(p, name, concept, base_glb, out, seed, project_dir, result):
    """Isolate each configured part from the concept image, build it as its own model,
    and attach it to the base. A part that fails leaves the one-piece model in place."""
    from PIL import Image

    parts_dir = os.path.join(out, f"{name}_parts")
    os.makedirs(parts_dir, exist_ok=True)
    shutil.copyfile(base_glb, os.path.join(parts_dir, "base_one_piece.glb"))
    report = {"assembled": [], "skipped": [], "folder": parts_dir}
    for part in p["split"]["parts"]:
        pid = part["id"]
        try:
            img = Image.open(concept)
            box, info = split.REGION_FINDERS[pid](img)
            crop_path = os.path.join(parts_dir, f"{pid}_crop.png")
            img.crop(box).resize((1024, 1024), Image.LANCZOS).save(crop_path)
            iso = generate_image(part["isolate_profile"], f"{pid}", part.get("prompt", ""), project_dir, parts_dir,
                                 seed, force=True, source_image=crop_path)
            model = generate_asset(part["part_profile"], pid, image=iso["image"], project_dir=project_dir,
                                   out_dir=parts_dir, seed=seed, params=part.get("params"), force=True, preview=False)
            result["timings"][f"{pid}_s"] = round(iso["seconds"] + model["timings"].get("model_s", 0), 1)
            if model["status"] != "ok":
                report["skipped"].append({"part": pid, "reason": "; ".join(model.get("problems", [])) or model["status"]})
                continue
            fit = split.ATTACHERS[pid](base_glb, model["glb"], base_glb)
            report["assembled"].append({**fit, "crop_box": list(box), **info})
        except Exception as e:  # keep the one-piece result rather than fail the whole asset
            report["skipped"].append({"part": pid, "reason": f"{type(e).__name__}: {e}"})
            log(f"[{name}] part '{pid}' skipped: {e}")
    return report


def generate_batch(jobs, project_dir=None, out_dir=None, style="", force=False):
    """Every concept image first, then every model, so each family of weights is
    loaded once instead of being swapped for each asset. Safe to re-run: finished
    assets are skipped."""
    results = {}
    for job in jobs:
        name = safe_name(job["name"])
        if job.get("image") or job.get("kind") == "image":
            continue
        try:
            generate_asset(job["profile"], name, job.get("prompt"), None, project_dir, out_dir, job.get("seed"),
                           job.get("style", style), job.get("params"), image_only=True, force=force,
                           preset=job.get("preset", "standard"))
        except Exception as e:
            results[name] = {"name": name, "status": "error", "error": f"{type(e).__name__}: {e}"}
    for job in jobs:
        name = safe_name(job["name"])
        if name in results:
            continue
        try:
            if job.get("kind") == "image":
                results[name] = generate_image(job["profile"], name, job["prompt"], project_dir, out_dir, job.get("seed"),
                                               job.get("style", style), job.get("params"), force)
            else:
                # force only affects stage 1 above; here the fresh concept image is reused
                results[name] = generate_asset(job["profile"], name, None if not job.get("image") else job.get("prompt"),
                                               job.get("image"), project_dir, out_dir, job.get("seed"),
                                               job.get("style", style), job.get("params"), force=force,
                                               preset=job.get("preset", "standard"))
        except Exception as e:
            results[name] = {"name": name, "status": "error", "error": f"{type(e).__name__}: {e}"}
    return list(results.values())
