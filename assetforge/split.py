"""Split generation: build a part of the subject on its own, then fit it onto the base model.

A part that fills the whole picture gets the full resolution of the 3D model and of
the texture, so it comes out much sharper than it does as a small region of a
one-shot model. The face of a character gains the most.

  concept image -> find the part -> crop -> image model isolates it -> part model
  base model + part model -> scale and place the part, cut the base, join -> one .glb

Region finding and fitting are geometric (no extra models): a character's neck is
the narrowest row between the shoulders and the top of the head, in the picture and
in the mesh alike.
"""
import os

import numpy as np


# ----------------------------------------------------------------------------- 2D: where is the part

def _foreground(img):
    """Mask of everything that is not the plain light background."""
    a = np.asarray(img.convert("RGB"), dtype=np.int16)
    corners = np.concatenate([a[:8, :8].reshape(-1, 3), a[:8, -8:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    return np.abs(a - bg).sum(axis=2) > 40


def _narrowest(widths, lo, hi):
    """Index of the narrowest non-empty entry in widths[lo:hi]."""
    seg = np.where(widths[lo:hi] > 0, widths[lo:hi], np.inf)
    return lo + int(np.argmin(seg)) if np.isfinite(seg).any() else None


def head_crop_box(img, margin=0.18):
    """Square crop around the head, hair/hat, beard and neck of a front-view character."""
    mask = _foreground(img)
    ys, xs = np.where(mask)
    if len(ys) < 500:
        raise ValueError("no subject found on a plain background in the concept image")
    top, bottom = ys.min(), ys.max()
    height = bottom - top
    cx = int(np.median(xs))
    band = slice(max(0, cx - int(0.3 * height)), cx + int(0.3 * height))  # ignore outstretched arms
    widths = mask[:, band].sum(axis=1)
    neck = _narrowest(widths, top + int(0.08 * height), top + int(0.40 * height))
    if neck is None:
        neck = top + int(0.16 * height)
    head_h = neck - top
    head_w = int(widths[top:neck].max())
    side = int(max(head_w, head_h * 1.35) * (1 + 2 * margin))
    y0 = int(top - margin * side * 0.6)
    x0 = cx - side // 2
    x0 = max(0, min(x0, img.width - side))
    y0 = max(0, min(y0, img.height - side))
    side = min(side, img.width, img.height)
    return (x0, y0, x0 + side, y0 + side), {"neck_row": int(neck), "head_px": [int(head_w), int(head_h)]}


REGION_FINDERS = {"head": head_crop_box}


# ----------------------------------------------------------------------------- 3D: fit and join

def _load(path):
    import trimesh
    scene = trimesh.load(path, process=False)
    meshes = scene.dump(concatenate=False) if hasattr(scene, "dump") else [scene]
    if len(meshes) != 1:
        raise ValueError(f"{os.path.basename(path)} has {len(meshes)} meshes; expected one")
    return meshes[0]


def _mesh_neck(v):
    """Height of the neck of an upright front-facing character mesh (Y up)."""
    ymin, ymax = v[:, 1].min(), v[:, 1].max()
    h = ymax - ymin
    cx = float(np.median(v[:, 0]))
    central = np.abs(v[:, 0] - cx) < 0.3 * h
    edges = np.linspace(ymin + 0.60 * h, ymin + 0.92 * h, 41)
    widths = np.zeros(40)
    for i in range(40):
        sel = central & (v[:, 1] >= edges[i]) & (v[:, 1] < edges[i + 1])
        if sel.sum() >= 3:
            widths[i] = np.ptp(v[sel, 0])
    i = _narrowest(widths, 0, 40)
    neck_y = (edges[i] + edges[i + 1]) / 2 if i is not None else ymin + 0.84 * h
    return float(neck_y), cx, float(h)


def attach_head(base_glb, part_glb, out_glb):
    """Scale and place a separately generated head on the base, remove the base's own
    head, and write both as one .glb (two materials)."""
    import trimesh

    base, part = _load(base_glb), _load(part_glb)
    bv = np.asarray(base.vertices, dtype=np.float64)
    neck_y, cx, h = _mesh_neck(bv)
    head_sel = (bv[:, 1] > neck_y) & (np.abs(bv[:, 0] - cx) < 0.3 * h)
    if head_sel.sum() < 10:
        raise ValueError("could not find a head on the base model")
    hv = bv[head_sel]
    head_h = hv[:, 1].max() - neck_y
    head_w = np.ptp(hv[:, 0])

    pv = np.asarray(part.vertices, dtype=np.float64)
    p_h = np.ptp(pv[:, 1])
    upper = pv[:, 1] > pv[:, 1].min() + 0.30 * p_h   # the head itself, above its neck stump
    scale = head_w / max(np.ptp(pv[upper, 0]), 1e-9)
    fitted_h = p_h * scale
    method = "width"
    if not (0.8 * head_h <= fitted_h <= 2.6 * head_h):  # width match is off (wide hat, hair): go by height
        scale, method = head_h * 1.3 / p_h, "height"

    pv = pv * scale
    upper_v = pv[upper]
    shift = np.array([
        (hv[:, 0].min() + hv[:, 0].max()) / 2 - (upper_v[:, 0].min() + upper_v[:, 0].max()) / 2,
        hv[:, 1].max() - pv[:, 1].max(),
        (hv[:, 2].min() + hv[:, 2].max()) / 2 - (upper_v[:, 2].min() + upper_v[:, 2].max()) / 2,
    ])
    part.vertices = pv + shift

    # Cut the base just above the neck line; the new head's neck overlaps into the body.
    cut_y = neck_y + 0.15 * head_h
    faces = np.asarray(base.faces)
    fv = bv[faces]
    drop = (fv[:, :, 1] > cut_y).all(axis=1) & (np.abs(fv[:, :, 0] - cx) < 0.3 * h).all(axis=1)
    base.update_faces(~drop)
    base.remove_unreferenced_vertices()

    scene = trimesh.Scene()
    scene.add_geometry(base, node_name="body", geom_name="body")
    scene.add_geometry(part, node_name="head", geom_name="head")
    scene.export(out_glb)
    return {"part": "head", "scale": round(float(scale), 4), "scaled_by": method,
            "neck_height_ratio": round(float((neck_y - bv[:, 1].min()) / h), 3),
            "base_faces_removed": int(drop.sum()), "part_triangles": int(len(part.faces)),
            "body_triangles": int(len(base.faces))}


ATTACHERS = {"head": attach_head}
