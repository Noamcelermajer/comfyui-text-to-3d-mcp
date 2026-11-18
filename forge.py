r"""Asset Forge command line. Every command prints one JSON object on stdout.

  forge.py doctor                              detect hardware, check ComfyUI, Blender and model files
  forge.py profiles                            list profiles and their parameters
  forge.py image  NAME "prompt" [-p icon]      text -> image
  forge.py asset  NAME "prompt" [-p prop_lowpoly] [--image-only] [--image FILE] [--set tris=5000]
  forge.py batch  jobs.json                    many assets; images first, then models
  forge.py check  model.glb                    mesh check + preview render
  forge.py models [--install FILE ...]         what each profile needs / download catalog files
  forge.py init   PROJECT_DIR                  write a starter assetforge.json into a game project
  forge.py connect [--claude] [--codex] [--project DIR] [--remove]   register the MCP server + skill
  forge.py bundle                              build connectors/asset-forge.mcpb (Claude Desktop extension)
  forge.py mcp                                 run the MCP server (used by the connectors)

Common options: --project DIR  --out DIR  --style "..."  --seed N  --force
Exit code: 0 ok, 2 finished but a mesh failed its check, 1 error.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _params(pairs):
    out = {}
    for pair in pairs or []:
        k, _, v = pair.partition("=")
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out or None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--project", help="game project folder (uses its assetforge.json)")
        p.add_argument("--out", help="output folder")
        p.add_argument("--style", default="")
        p.add_argument("--seed", type=int)
        p.add_argument("--force", action="store_true")
        p.add_argument("--set", action="append", metavar="KEY=VALUE", help="override a profile parameter")

    sub.add_parser("doctor")
    p = sub.add_parser("profiles"); p.add_argument("--project")
    p = sub.add_parser("image"); p.add_argument("name"); p.add_argument("prompt"); p.add_argument("-p", "--profile", default="illustration"); common(p)
    p = sub.add_parser("asset"); p.add_argument("name"); p.add_argument("prompt", nargs="?"); p.add_argument("-p", "--profile", default="prop_lowpoly")
    p.add_argument("--image"); p.add_argument("--image-only", action="store_true"); p.add_argument("--no-preview", action="store_true"); common(p)
    p = sub.add_parser("batch"); p.add_argument("jobs"); common(p)
    p = sub.add_parser("check"); p.add_argument("glb"); p.add_argument("--tris", type=int); p.add_argument("--no-preview", action="store_true")
    p = sub.add_parser("models"); p.add_argument("--project"); p.add_argument("--install", nargs="+")
    p = sub.add_parser("init"); p.add_argument("project_dir")
    p = sub.add_parser("connect"); p.add_argument("--claude", action="store_true"); p.add_argument("--codex", action="store_true")
    p.add_argument("--project"); p.add_argument("--remove", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("bundle"); p.add_argument("--out")
    sub.add_parser("mcp")
    a = ap.parse_args()

    # Mesh checks need numpy/trimesh, which ComfyUI's Python has. When started with a
    # Python that lacks them (e.g. as a plugin via plain `python`), hand over to it.
    try:
        import numpy, trimesh  # noqa: F401,E401
    except ImportError:
        from assetforge.config import load_machine
        comfy_py = load_machine().get("comfy_python", "")
        if comfy_py and os.path.isfile(comfy_py) and os.path.normcase(comfy_py) != os.path.normcase(sys.executable):
            import subprocess
            sys.exit(subprocess.call([comfy_py, os.path.abspath(__file__), *sys.argv[1:]]))

    if a.cmd == "mcp":
        from assetforge import mcp_server
        return mcp_server.serve()

    try:
        from assetforge import comfy, config, connect, meshcheck, models, pipeline, profiles
        if a.cmd == "doctor":
            m = config.load_machine(refresh=True)
            up = comfy.is_up()
            rep = models.report()
            out = {"status": "ok", "machine": m, "comfyui_found": bool(m["comfy_root"]), "comfyui_running": up,
                   "blender_found": bool(m["blender"]), "guide_for_this_tier": rep["guide_for_this_tier"],
                   "profiles": [{k: p[k] for k in ("profile", "ready", "fits_this_gpu", "missing")} for p in rep["profiles"]]}
            if up:  # with ComfyUI running we can also confirm every node type a profile uses is installed
                info = comfy.object_info()
                for entry in out["profiles"]:
                    g = json.load(open(profiles.graph_path(profiles.resolve(entry["profile"])), encoding="utf-8-sig"))
                    types = {n["type"] for n in g["nodes"] if n.get("mode", 0) not in (2, 4)} if "nodes" in g else {n["class_type"] for n in g.values()}
                    entry["missing_nodes"] = sorted(t for t in types if t not in info and t not in
                                                    ("Note", "MarkdownNote", "Reroute", "PrimitiveNode") and len(t) != 36)
                    entry["ready"] = entry["ready"] and not entry["missing_nodes"]
        elif a.cmd == "profiles":
            out = {"status": "ok", "profiles": profiles.list_profiles(a.project)}
        elif a.cmd == "image":
            out = pipeline.generate_image(a.profile, a.name, a.prompt, a.project, a.out, a.seed, a.style, _params(a.set), a.force)
        elif a.cmd == "asset":
            out = pipeline.generate_asset(a.profile, a.name, a.prompt, a.image, a.project, a.out, a.seed, a.style,
                                          _params(a.set), a.image_only, a.force, preview=not a.no_preview)
        elif a.cmd == "batch":
            jobs = json.load(open(a.jobs, encoding="utf-8-sig"))
            results = pipeline.generate_batch(jobs, a.project, a.out, a.style, a.force)
            bad = [r["name"] for r in results if r.get("status") != "ok"]
            out = {"status": "ok" if not bad else "failed_check", "count": len(results), "not_ok": bad, "assets": results}
        elif a.cmd == "check":
            stats, problems = meshcheck.check_mesh(a.glb, a.tris)
            out = {"status": "ok" if not problems else "failed_check", "mesh": stats, "problems": problems}
            if not a.no_preview:
                out["preview"] = meshcheck.render_preview(a.glb, os.path.splitext(a.glb)[0] + "_preview.png")
        elif a.cmd == "models":
            out = {"status": "ok", "installed": models.install(a.install)} if a.install else {"status": "ok", **models.report(a.project)}
        elif a.cmd == "init":
            out = connect.init_project(a.project_dir)
        elif a.cmd == "bundle":
            out = connect.make_bundle(a.out)
        elif a.cmd == "connect":
            out = connect.connect(claude=a.claude, codex=a.codex, project=a.project, remove=a.remove, dry_run=a.dry_run)
    except Exception as e:
        print(json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"}))
        sys.exit(1)

    print(json.dumps(out, indent=1))
    sys.exit(0 if out.get("status") in ("ok", "image_only") else 2)


if __name__ == "__main__":
    main()
