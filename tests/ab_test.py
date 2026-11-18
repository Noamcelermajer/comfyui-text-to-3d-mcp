r"""A/B test: the same subjects through each 3D pipeline, compared on one sheet.

    python tests/ab_test.py               run everything (about an hour on a 16 GB card)
    python tests/ab_test.py --only sword  run the subjects whose name contains "sword"
    python tests/ab_test.py --dry-run     list what would run

Each subject gets one fixed seed, so every pipeline starts from the same idea. A
pipeline makes its own concept image when it needs a different one (Pixal3D gets a
straight-on view, TRELLIS.2 the three-quarter view it was tuned for), because the
concept image is part of the pipeline being tested.

Output: tests/ab_results/<run>/ with every model, its preview, results.json (mesh
check, timings, problems) and sheet.png (one row per subject, one column per pipeline).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from assetforge import pipeline  # noqa: E402

SUBJECTS = [
    ("sword", "prop", "a chunky weathered greatsword with a wide slab blade, brass crossguard and leather-wrapped grip"),
    ("chain", "prop", "a short heavy iron chain of five thick links ending in a big padlock"),
    ("pole", "prop", "a wooden lantern pole with a thick shaft and a square iron lantern hanging from the top"),
    ("crate", "prop", "a sturdy wooden supply crate with iron corner brackets and a rope handle"),
    ("keeper", "character", "an old grave keeper with a long grey beard, a patched grey wool coat, a wide-brim leather hat and heavy boots"),
    ("smith", "character", "a young female blacksmith with a short red braid, a scorched leather apron over a green tunic and thick gloves"),
]

PIPELINES = {
    "prop": [
        ("current", "prop_lowpoly", {}),                     # TRELLIS.2 + WTiVo + LODTailor + UltraTex
        ("trellis2_native", "prop_native_trellis2", {}),
        ("pixal3d_front", "prop_native", {}),               # Pixal3D with a straight-on concept
    ],
    "character": [
        ("current", "character_lowpoly", {}),
        ("pixal3d_front", "character_native", {}),
        ("pixal3d_turnaround", "character_turnaround", {}),
    ],
}


def run(only=None, dry=False, preset="standard"):
    stamp = time.strftime("%Y%m%d_%H%M")
    out_root = os.path.join(HERE, "ab_results", stamp)
    rows = []
    for i, (name, kind, prompt) in enumerate(SUBJECTS):
        if only and only not in name:
            continue
        seed = 1000 + i
        for label, profile, params in PIPELINES[kind]:
            job = f"{name}__{label}"
            if dry:
                print(f"{job}: {profile} params={params} seed={seed}")
                continue
            out = os.path.join(out_root, label)
            t0 = time.time()
            try:
                r = pipeline.generate_asset(profile, name, prompt, out_dir=out, seed=seed, params=params or None,
                                            preset=preset, force=True)
            except Exception as e:
                r = {"status": "error", "error": f"{type(e).__name__}: {e}"}
            r["wall_s"] = round(time.time() - t0, 1)
            rows.append({"subject": name, "pipeline": label, "profile": profile, **{k: r.get(k) for k in (
                "status", "error", "problems", "mesh", "timings", "wall_s", "preview", "glb")}})
            print(json.dumps({"job": job, "status": r.get("status"), "wall_s": r["wall_s"],
                              "triangles": (r.get("mesh") or {}).get("triangles"), "problems": r.get("problems")}), flush=True)
            os.makedirs(out_root, exist_ok=True)
            json.dump(rows, open(os.path.join(out_root, "results.json"), "w", encoding="utf-8"), indent=2)
    if rows:
        _sheet(rows, os.path.join(out_root, "sheet.png"))
        print(json.dumps({"status": "ok", "results": os.path.join(out_root, "results.json"),
                          "sheet": os.path.join(out_root, "sheet.png")}))


def _sheet(rows, path):
    from PIL import Image, ImageDraw

    subjects = list(dict.fromkeys(r["subject"] for r in rows))
    labels = list(dict.fromkeys(r["pipeline"] for r in rows))
    W, H = 512, 300
    sheet = Image.new("RGB", (160 + W * len(labels), 40 + H * len(subjects)), (24, 26, 32))
    d = ImageDraw.Draw(sheet)
    for j, lab in enumerate(labels):
        d.text((160 + j * W + 8, 12), lab, fill=(230, 230, 230))
    for i, sub in enumerate(subjects):
        d.text((8, 40 + i * H + 8), sub, fill=(230, 230, 230))
        for j, lab in enumerate(labels):
            r = next((x for x in rows if x["subject"] == sub and x["pipeline"] == lab), None)
            x, y = 160 + j * W, 40 + i * H
            if r and r.get("preview") and os.path.isfile(r["preview"]):
                im = Image.open(r["preview"]).convert("RGB")
                im.thumbnail((W, H - 40))
                sheet.paste(im, (x, y))
            if r:
                tris = (r.get("mesh") or {}).get("triangles")
                note = f"{r['status']}  {tris or '-'} tris  {r.get('wall_s')} s"
                if r.get("problems"):
                    note += "  " + "; ".join(r["problems"])[:60]
                d.text((x + 8, y + H - 32), note, fill=(245, 190, 90) if r["status"] != "ok" else (140, 210, 150))
    sheet.save(path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--preset", default="standard", choices=["fast", "standard", "max"])
    a = ap.parse_args()
    run(a.only, a.dry_run, a.preset)
