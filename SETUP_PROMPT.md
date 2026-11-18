# Setup prompt

Paste everything in the block below into a coding agent (Claude Code, Codex, or any agent
that can run commands) on the machine that has the GPU. It installs what Asset Forge
needs, connects the MCP server, and proves it works by generating one asset.

````text
Set up "Asset Forge" (https://github.com/Noamcelermajer/text-to-3d-game-asset-mcp) on this
machine so its MCP server can generate images and game-ready 3D models locally. Work
through the stages in order. `python forge.py doctor` is the source of truth: it reads the
bundled workflows and lists every missing node type and model file, so run it after each
stage and let its output, not this prompt, decide what is still missing.

GROUND RULES
- Before downloading anything, show me the list with sizes and the total, and wait for my
  yes. The full model set is about 45 GB; ComfyUI and Blender are extra.
- Reuse what is already installed. If ComfyUI or Blender exists, ask me for the path
  instead of installing a second copy. Never delete or downgrade existing models, node
  packs or Python packages; if an install would change the torch version, stop and ask.
- Install Python packages only into ComfyUI's own Python (portable build:
  `python_embeded\python.exe -m pip ...`; venv build: that venv), never the system Python.
- If a step fails twice, stop and show me the error. Do not work around it by disabling
  nodes or editing the workflows.

STAGE 1 - CHECK THE MACHINE
- Report OS, GPU model and VRAM (`nvidia-smi`), RAM, and free disk space on the drive
  ComfyUI is or will be on. An NVIDIA GPU with at least 8 GB VRAM is required (16 GB
  recommended; text-to-image needs about 12 GB). If there is less than 70 GB free, tell me.

STAGE 2 - BASE SOFTWARE
- ComfyUI, version 0.45 or newer, with a CUDA build of PyTorch (reference setup: Python
  3.12, torch 2.8 + cu128). The bundled workflows use ComfyUI's built-in TRELLIS.2, MoGe,
  Qwen-Image 2.1, background-removal and 3D save nodes, so an older ComfyUI will report
  those as missing - update ComfyUI rather than hunting for node packs.
- Blender 4.x or newer (used for low-poly clean-up, normal baking and preview renders).
- git.

STAGE 3 - ASSET FORGE
- Clone the repository. Putting it inside or next to the ComfyUI folder lets it find
  ComfyUI automatically; otherwise set the environment variable ASSETFORGE_COMFY to the
  ComfyUI folder (the one containing main.py) before the first run.
- Run `python forge.py doctor` and confirm `comfyui_found` and `blender_found` are true.
  If not, fix `comfy_root`, `comfy_python` or `blender` in the `machine.json` it wrote.

STAGE 4 - CUSTOM NODE PACKS
Clone these into `ComfyUI/custom_nodes` (skip any already present) and install each
pack's `requirements.txt`, or run its `install.py` / install script if it has one:
- https://github.com/city96/ComfyUI-GGUF
- https://github.com/Mstafa-awad/UltraTex-ComfyUI-nodes   (folder: comfyui_ultratex_modular)
- https://github.com/Mstafa-awad/WTiVo-WatertightVoxel-ComfyuiNode
- https://github.com/Mstafa-awad/WTiVo-FastMergeByDistance
- https://github.com/Mstafa-awad/LODTailor-The-Mesh-Trimmer-ComfyuiNode
- https://github.com/Mstafa-awad/LODTailor-Bake-Forger
- https://github.com/Mstafa-awad/ComfyUI-Mesh-Quad-Reconstruct
- https://github.com/Mstafa-awad/ComfyUI-CuMesh-Decimate
- https://github.com/Mstafa-awad/ComfyUI-Memory-Cleaner
These packs need the Python packages cumesh, o_voxel, xatlas, moderngl, glcontext, gguf,
trimesh and pymeshlab. cumesh and o_voxel are compiled GPU libraries: use the wheels or
install scripts the packs provide for this Python and CUDA version, and do not try to
build them from source without asking me. The one-click installer in
https://github.com/pixelartistry/PixelArtistry-Animated-Characters sets up most of this
on Windows and is an acceptable shortcut - tell me if you use it.
Then start ComfyUI, wait until it answers on http://127.0.0.1:8188, and run
`python forge.py doctor` again: every profile's `missing_nodes` must be empty.

STAGE 5 - MODELS
- Run `python forge.py models`. For each profile it lists the files it needs, which are
  missing, their size and download link, plus advice for this GPU's VRAM tier.
- Show me the missing files and total size, wait for my yes, then download them with
  `python forge.py models --install <file> <file> ...`.
- Three UltraTex files have no direct link in the catalog (`ultratex_flux2.safetensors`,
  `ultratex_flux2_mr.safetensors`, `ultratex_flux2_decoder.pt`). They come from the
  UltraTex node pack's own setup (ModelScope repository Yibo-Zhang/UltraTex); follow that
  pack's README, and tell me if they cannot be obtained.
- On 8 GB cards skip the Qwen-Image 2.1 files unless I say otherwise: text-to-image will
  not fit, and I can supply concept images instead.

STAGE 6 - CONNECT THE AGENTS
- Show me `python forge.py connect --dry-run` (it lists the config files it would edit for
  Claude and Codex), wait for my yes, then run `python forge.py connect`.
  Use `--claude` or `--codex` for one agent, or `--project <dir>` to register it for a
  single project only.

STAGE 7 - PROVE IT WORKS
- `python forge.py doctor` - every profile shows `"ready": true` with nothing missing.
- `python forge.py asset setup_test "a chunky wooden treasure chest with iron bands" -p prop_lowpoly`
  This takes several minutes. It must end with `"status": "ok"` and an empty `problems`
  list. Open the `setup_test_preview.png` it writes and look at it: it should show a
  recognisable textured chest from two angles.
- If the status is `failed_check`, report the `problems` text; that means the pipeline
  runs but the mesh was bad, which is a tuning matter, not a setup failure.

REPORT BACK
One short summary: what was already installed, what you installed (with versions),
what was downloaded and how many GB, which files were edited by `connect`, the result of
the test asset with its preview path, and anything that is still missing or that you
could not verify.
````

## What the prompt installs

| Layer | Items |
| :--- | :--- |
| Base | ComfyUI 0.45+ (CUDA PyTorch), Blender 4.x+, git |
| Node packs | ComfyUI-GGUF, UltraTex, WTiVo Watertight Voxel, WTiVo Fast Merge, LODTailor Mesh Trimmer, LODTailor Bake Forger, Mesh Quad Reconstruct, CuMesh Decimate, Memory Cleaner |
| Python packages | cumesh, o_voxel, xatlas, moderngl, glcontext, gguf, trimesh, pymeshlab (into ComfyUI's Python) |
| Models, about 45 GB | Qwen-Image 2.1 + text encoder + VAE (image); TRELLIS.2 + VAEs, Pixal3D, DINOv3, MoGe-2, BiRefNet (shape); FLUX.2 Klein 9B GGUF, Qwen3-8B GGUF, FLUX.2 VAE, UltraTex LoRAs + decoder (textures) |

The lists above reflect the setup the bundled workflows were verified on (ComfyUI 0.45,
Python 3.12, torch 2.8 + cu128, RTX 5080). `forge.py doctor` and `forge.py models` are
always the up-to-date answer for your machine and for any profiles you add.
