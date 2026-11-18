"""Steps that run on the finished .glb, after generation and before the preview.

voxelize  - rebuild the model as coloured cubes (the "voxel" style). Each cube takes
            the colour of the nearest point on the textured surface.
"""
import numpy as np


def _surface_colours(mesh, count=200_000):
    """Sample the textured surface: points and their colours (RGBA 0-255)."""
    import trimesh

    pts, faces = trimesh.sample.sample_surface(mesh, count)
    vis = mesh.visual
    if hasattr(vis, "uv") and vis.uv is not None and getattr(vis, "material", None) is not None:
        tri = mesh.triangles[faces]
        bary = trimesh.triangles.points_to_barycentric(tri, pts)
        uv = (vis.uv[mesh.faces[faces]] * bary[:, :, None]).sum(axis=1)
        img = getattr(vis.material, "baseColorTexture", None) or getattr(vis.material, "image", None)
        if img is not None:
            a = np.asarray(img.convert("RGBA"))
            h, w = a.shape[:2]
            x = np.clip((uv[:, 0] % 1.0) * (w - 1), 0, w - 1).astype(int)
            y = np.clip((1.0 - uv[:, 1] % 1.0) * (h - 1), 0, h - 1).astype(int)
            return pts, a[y, x]
    vc = vis.to_color().vertex_colors if hasattr(vis, "to_color") else None
    if vc is not None and len(vc) == len(mesh.vertices):
        return pts, vc[mesh.faces[faces][:, 0]]
    return pts, np.tile([200, 200, 200, 255], (len(pts), 1))


def voxelize(src_glb, dst_glb, resolution=32):
    """Replace a model with coloured cubes, `resolution` cubes along its longest side."""
    import trimesh
    from scipy.spatial import cKDTree

    mesh = trimesh.load(src_glb, force="mesh")
    pitch = float(mesh.extents.max()) / resolution
    grid = mesh.voxelized(pitch).fill()
    filled = grid.matrix.astype(bool)
    idx3 = np.argwhere(filled)
    centres = grid.indices_to_points(idx3)
    pts, cols = _surface_colours(mesh)
    _, near = cKDTree(pts).query(centres)
    colour = cols[near][:, :4].astype(np.uint8)

    # Only faces between a filled cube and empty space are emitted: a hollow shell of
    # quads, far fewer triangles than whole cubes, and no hidden interior faces.
    padded = np.pad(filled, 1)
    corners = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float) - 0.5
    sides = {(-1, 0, 0): [0, 4, 7, 3], (1, 0, 0): [1, 2, 6, 5], (0, -1, 0): [0, 1, 5, 4],
             (0, 1, 0): [3, 7, 6, 2], (0, 0, -1): [0, 3, 2, 1], (0, 0, 1): [4, 5, 6, 7]}
    verts, faces, fcol = [], [], []
    for d, quad in sides.items():
        nb = idx3 + 1 + np.array(d)
        open_side = ~padded[nb[:, 0], nb[:, 1], nb[:, 2]]
        for i in np.nonzero(open_side)[0]:
            base = len(verts)
            verts.extend(centres[i] + corners[quad] * pitch)
            faces.extend([[base, base + 1, base + 2], [base, base + 2, base + 3]])
            fcol.extend([colour[i], colour[i]])
    out = trimesh.Trimesh(vertices=np.array(verts), faces=np.array(faces), process=False)
    out.visual.face_colors = np.array(fcol)
    out.merge_vertices(merge_tex=True, merge_norm=True)
    out.export(dst_glb)
    return {"step": "voxelize", "resolution": resolution, "cubes": int(len(centres)), "triangles": int(len(out.faces))}


STEPS = {"voxelize": voxelize}
