---
name: asset-forge
description: Generate game art locally and for free - concept images, UI icons, illustrations, and game-ready 3D models (.glb with PBR textures) from a text prompt or a picture. Use whenever the user asks to create, generate, regenerate or fix a game asset, 3D model, prop, character model, icon or concept image, or to check a .glb.
---

# Asset Forge

Asset Forge turns text into images and into game-ready 3D models on this machine's GPU
(ComfyUI underneath; it is started automatically). Nothing is sent to a paid service.

## How to call it

Prefer the **`asset-forge` MCP tools** when they are available:
`list_profiles`, `generate_image`, `generate_asset`, `get_job`, `check_mesh`, `models`, `forge_status`.

Otherwise use the command line from the Asset Forge folder; every command prints one JSON object:

```
python forge.py profiles
python forge.py image  <name> "<prompt>" -p icon --project <game folder>
python forge.py asset  <name> "<prompt>" -p prop_lowpoly --project <game folder>
python forge.py asset  <name> --image <picture> -p prop_lowpoly --project <game folder>
python forge.py check  <model.glb>
```

Exit code 0 = ok, 2 = finished but the mesh failed its check, 1 = error.

## Workflow

1. **Pick a profile** with `list_profiles`. Image profiles make pictures (`concept_prop`,
   `concept_character`, `icon`, `illustration`); asset profiles make 3D models
   (`prop_lowpoly`, `character_lowpoly`), each with three presets (see below). Profiles marked `experimental`
   have not been verified on this machine - say so if you use one.
2. **Always pass the game project folder** (`project_dir` / `--project`). Its
   `assetforge.json` supplies the art style, the output folder and per-profile settings,
   so assets stay consistent. If the project has none, offer to create one (`forge.py init <dir>`).
3. **For 3D, approve the picture first.** Call `generate_asset` with `image_only: true`
   (about a minute), look at the image, and only then call it again without `image_only`
   (3-8 minutes). The second call reuses the approved image; no prompt is needed.
4. **3D jobs are asynchronous.** `generate_asset` returns a job id; call `get_job` with
   `wait_seconds: 50` repeatedly until the status is not `queued` or `running`. Run one
   asset at a time - there is one GPU.
5. **Look at the result.** A finished job returns a rendered preview (front and
   three-quarter view) and a mesh check. Do not report success without looking at the preview.

## Presets

Pass `preset` to `generate_asset` (`--preset` on the command line). Pick by what the
asset is for, and say which one you used.

| Preset | Use it for | Cost |
| :--- | :--- | :--- |
| `fast` | Blocking out, background clutter, distant or mobile assets: the lowest triangle count (about 800 for a prop, 4,000 for a character). | about 2 minutes |
| `standard` | The default. 3,000-triangle props, 12,000-triangle characters. | 4-5 minutes |
| `max` | Hero assets the camera gets close to. Props get full resolution and 8,000 triangles. Characters use split generation: the head is generated as its own model and fitted onto the body, which makes the face much sharper. | 10-15 minutes |

`max` on a character is experimental: check the preview for a lumpy hat or hair, texture
speckles, or a misplaced head. The result reports `split.assembled` and `split.skipped`;
if the head was skipped, you got the one-piece model. The one-piece body is always kept
in `<name>_parts/base_one_piece.glb`.

A different preset for the same name regenerates the model but reuses the concept image.

## Writing prompts

- Describe **only the subject**: what it is, materials, colours, wear, proportions. The
  profile adds framing, lighting and background.
- One object per asset. Pairs, or an object plus loose debris, are separate assets.
- Chunky beats delicate. Thin wires, cages, lace, real holes and hair-thin spikes break
  in the 3D step - ask for thick handles, a solid rod instead of a chain, engraved marks
  instead of holes.
- Characters: person and outfit only, no held items. Make weapons as props.

## When the result says `failed_check`

The file is written but should not be used as is. `problems` explains why.

| Problem | Fix |
| :--- | :--- |
| `shattered mesh` | Thin parts broke apart. Re-run with `force: true` and `params: {"thin": 0.9}`. If it fails again, regenerate the image with thicker, solid parts. |
| `spiky mesh` | Re-run with `force: true` and a new `seed`. |
| `decimation failed` / `mesh collapsed` | Re-run with `force: true`; if it repeats, raise `tris`. |
| `flat sheet` | The picture read as flat. Regenerate the image with "bulky, deep, solid" in the prompt. |

`force: true` together with a `prompt` regenerates the image too; `force: true` without
a prompt keeps the image and only redoes the 3D step.

## Customising

- Per project: edit `assetforge.json` (style, output folder, `profiles.<id>.params`).
- Per call: `params`, e.g. `{"tris": 5000}`. `list_profiles` shows what each profile accepts.
- New profile: a small JSON file in the project's `assetforge_profiles/` folder that
  `extends` an existing one. See the README in the Asset Forge folder.
- Models and hardware: the `models` tool reports what each profile needs, what is
  missing and what suits this GPU. Downloads are large - ask the user before installing.
