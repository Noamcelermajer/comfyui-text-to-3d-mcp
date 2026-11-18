# Asset Forge - instructions for AI agents

This file is for an AI agent (Claude, Codex, or any other) that has been asked to
install or use Asset Forge. Everything here can be done without asking a human.
Every `forge.py` command prints one JSON object on stdout; read it to decide the next step.

- **Installing?** Follow [Install](#install-unattended) from top to bottom.
- **Already installed and asked to make an asset?** Read [`skills/asset-forge/SKILL.md`](skills/asset-forge/SKILL.md).
- **Changing profiles or adding a model?** Read the "Customising" section of [`README.md`](README.md).

## What gets installed

| Layer | What | Installed by |
| :--- | :--- | :--- |
| Base | ComfyUI 0.45 or newer with CUDA PyTorch; Blender 4.0 or newer; git | you, with the commands below (skipped when present) |
| Node packs | 9 ComfyUI custom-node packs, listed in `models/catalog.json` under `node_packs` | `forge.py setup` |
| Python packages | each pack's requirements and bundled wheels, into ComfyUI's own Python | `forge.py setup` |
| Models | about 45 GB of weights, listed in `models/catalog.json` under `files` | `forge.py setup` |
| Connector | MCP server entry and skill for Claude and Codex | `forge.py connect` |

Requirements: an NVIDIA GPU with at least 8 GB of VRAM (16 GB recommended), about 70 GB of
free disk space, Windows or Linux.

## Install (unattended)

Run the steps in order. Each one is safe to repeat; anything already present is skipped.

### 1. Clone

```
git clone https://github.com/Noamcelermajer/comfyui-text-to-3d-mcp
cd comfyui-text-to-3d-mcp
```

Use any Python 3.10+ to run `forge.py`. It hands over to ComfyUI's own Python where needed.

### 2. Run setup and act on its answer

```
python forge.py setup
```

| `status` | Exit code | Meaning and what to do |
| :--- | :--- | :--- |
| `ok` | 0 | Node packs and models are in place. Go to step 3. |
| `blocked` | 3 | Base software is missing. Run the commands in `manual_steps[].commands`, then run `setup` again. |
| `partial` | 3 | Something failed to install. Each failed item carries its own error and, where one exists, a `manual` command. Fix those and run `setup` again. |

Things `setup` may ask of you through `manual_steps`:

- **ComfyUI not found.** If ComfyUI is already installed somewhere, set the environment
  variable `ASSETFORGE_COMFY` to its folder (the one containing `main.py`) and run
  `setup` again - do not install a second copy. Search the usual places first
  (`~/ComfyUI`, `~/Documents`, drive roots, a `ComfyUI_windows_portable` folder). Only if
  none exists, install it:

  ```
  git clone https://github.com/comfyanonymous/ComfyUI
  cd ComfyUI
  python -m venv venv
  venv/bin/python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  venv/bin/python -m pip install -r requirements.txt
  ```

  (On Windows the interpreter is `venv\Scripts\python.exe`. Use Python 3.12 for the venv;
  the compiled wheels the node packs ship are built for it.)
- **Blender not found.** Windows: `winget install --id BlenderFoundation.Blender -e --accept-source-agreements --accept-package-agreements`.
  Linux: `sudo snap install blender --classic`. Then run `python forge.py doctor`, which
  re-detects it; if it is installed somewhere unusual, set `"blender"` in `machine.json`.
- **Restart ComfyUI.** New node packs load only at start-up. If ComfyUI is running, stop
  it; Asset Forge starts it again by itself on the next generation command.

Useful flags: `--dry-run` lists what would be installed and the download size without
changing anything; `--skip-models` and `--skip-nodes` do one half; `--pinned` checks out
the exact node-pack commits the workflows were tested with (use it if the newest
versions fail to load).

### 3. Verify

Start ComfyUI if it is not running (any generation command starts it; or run its
`main.py` yourself), then:

```
python forge.py doctor
```

Every entry under `profiles` must show `"ready": true`, an empty `missing` list and an
empty `missing_nodes` list.

- `missing` not empty: run `python forge.py setup` again, or
  `python forge.py models --install <file> ...` for the files named.
- `missing_nodes` not empty: a node pack failed to load. Read the import error in
  ComfyUI's start-up log (`comfyui.log` in this folder when Asset Forge started it). The
  usual causes are a compiled wheel that does not match the Python or CUDA version (run
  that pack's `install.py` with ComfyUI's Python) and a ComfyUI older than 0.45 (update
  ComfyUI - the TRELLIS.2, MoGe, Qwen-Image 2.1 and 3D-save nodes are built in from that
  version).

### 4. Connect the agent

```
python forge.py connect
```

This registers the MCP server and installs the skill for both Claude and Codex at user
level, backing up each config file it edits. Use `--claude` or `--codex` for one,
`--project <dir>` to register for a single project instead, `--dry-run` to preview. A
new agent session is needed before the tools appear.

Any other MCP client: add a stdio server with command `python` and arguments
`["<this folder>/forge.py", "mcp"]`.

### 5. Prove it works

```
python forge.py asset setup_test "a chunky wooden treasure chest with iron bands" -p prop_lowpoly
```

This takes several minutes (longer on the first run, while weights load). Success is
`"status": "ok"` with an empty `problems` list. Look at the `preview` image it names: it
must show a recognisable textured chest from two angles.

- `"status": "failed_check"`: the pipeline works but this mesh was bad. That is a
  generation matter, not a setup failure - see the table in `skills/asset-forge/SKILL.md`.
- `"status": "error"` naming a node that failed: that node's pack or model is the
  problem; go back to step 3.
- An out-of-memory error: close other GPU programs and retry; on 8-12 GB cards the
  profile's lower-memory settings are applied automatically from the detected VRAM tier.

### 6. Report

Tell the user: what was already present, what was installed, how many GB were
downloaded, which config files `connect` edited, the result and preview path of the test
asset, and anything still missing.

## Rules while installing

- Never delete, replace or downgrade existing models, node packs or Python packages.
- Never change the installed PyTorch version of an existing ComfyUI. If a package
  install wants to, stop and report it.
- Install Python packages only into ComfyUI's interpreter (`comfy_python` in
  `machine.json`), never the system Python.
- Do not edit the files in `workflows/` to get past an error; settings belong in profiles.
- `setup` checks free disk space before downloading and stops if there is not enough.
  Do not work around that by deleting the user's files.

## Notes on specific dependencies

- **UltraTex weights** (`ultratex_flux2.safetensors`, `ultratex_flux2_mr.safetensors`,
  `ultratex_flux2_decoder.pt`) exist only on ModelScope, under generic names. `setup`
  installs the `modelscope` package into ComfyUI's Python, downloads them and renames
  them. By hand:
  `modelscope download --model Yibo-Zhang/UltraTex flux2/lora/dit_lora.safetensors --local_dir tmp`
  then copy to `models/loras/ultratex_flux2.safetensors`; likewise
  `flux2_mr/lora/dit_lora.safetensors` to `models/loras/ultratex_flux2_mr.safetensors`
  and `flux2/decoder.pt` to `models/ultratex/ultratex_flux2_decoder.pt`.
- **cumesh and o_voxel** are compiled GPU libraries. The node packs ship wheels and an
  `install.py` that picks the right one; `setup` runs it. Do not build them from source.
- **8 GB cards** cannot run the Qwen-Image 2.1 text-to-image profiles; `setup` skips
  those downloads. Asset generation still works from a supplied picture
  (`forge.py asset <name> --image <file>`).
- Only the 16 GB tier has been measured. This install path has been exercised on a
  machine that already had everything; a from-scratch install has not been observed, so
  report any step that does not behave as described here.
