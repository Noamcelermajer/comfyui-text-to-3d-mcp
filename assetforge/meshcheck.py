"""Geometric checks and Blender previews for generated models.

The thresholds were calibrated on real failures: shattered meshes, failed
decimation, needle-spike meshes and flat sheets all fail; every good model passes.
"""
import os
import subprocess

from .config import FORGE_ROOT, load_machine

DEFAULTS = {
    "max_tris_factor": 1.5,       # more than target x this = decimation failed
    "min_tris_factor": 0.25,      # fewer than target x this = mesh collapsed
    "max_spike_ratio": 0.01,      # share of triangles that are long needles
    "max_pieces": 400,            # small floaters are normal; hundreds are not
    "min_largest_piece": 0.5,     # the biggest piece must hold most triangles
    "min_thin_axis": 0.02,        # thinnest extent / longest extent
}


def check_mesh(glb_path, target_tris=None, limits=None):
    import numpy as np
    import trimesh

    lim = {**DEFAULTS, **(limits or {})}
    m = trimesh.load(glb_path, force="mesh", process=False)
    v, f = np.asarray(m.vertices, dtype=np.float64), np.asarray(m.faces)
    ext = v.max(axis=0) - v.min(axis=0)
    diag = float(np.linalg.norm(ext)) or 1e-9

    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    longest = np.maximum.reduce([np.linalg.norm(a - b, axis=1), np.linalg.norm(b - c, axis=1), np.linalg.norm(c - a, axis=1)])
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    spike_ratio = float(((longest > 0.25 * diag) & (longest ** 2 > 40.0 * np.maximum(area, 1e-12))).mean())

    welded = trimesh.Trimesh(vertices=v, faces=f, process=False)
    welded.merge_vertices(merge_tex=True, merge_norm=True)  # UV seams split render vertices
    comps = trimesh.graph.connected_components(welded.face_adjacency, nodes=np.arange(len(welded.faces)), min_len=1)
    pieces = len(comps)
    largest = max(len(c) for c in comps) / max(len(welded.faces), 1)

    stats = {
        "triangles": int(len(f)), "vertices": int(len(v)),
        "extents": [round(float(x), 3) for x in ext],
        "longest_edge_ratio": round(float(longest.max() / diag), 3),
        "spike_triangle_ratio": round(spike_ratio, 4),
        "loose_pieces": pieces, "largest_piece_ratio": round(float(largest), 3),
        "thinnest_axis_ratio": round(float(ext.min() / ext.max()), 3),
    }
    problems = []
    if target_tris:
        if len(f) > lim["max_tris_factor"] * target_tris:
            problems.append(f"decimation failed: {len(f)} triangles for a target of {target_tris}")
        if len(f) < lim["min_tris_factor"] * target_tris:
            problems.append(f"mesh collapsed: only {len(f)} triangles for a target of {target_tris}")
    if spike_ratio > lim["max_spike_ratio"]:
        problems.append(f"spiky mesh: {spike_ratio:.1%} of triangles are long needles")
    if pieces > lim["max_pieces"] or largest < lim["min_largest_piece"]:
        problems.append(f"shattered mesh: {pieces} disconnected pieces, the largest holds {largest:.0%} of the triangles")
    if stats["thinnest_axis_ratio"] < lim["min_thin_axis"]:
        problems.append("flat sheet: the mesh has almost no depth")
    return stats, problems


def render_preview(glb_path, png_path, size=512):
    """Render front and three-quarter views side by side with Blender."""
    blender = load_machine().get("blender")
    if not blender or not os.path.isfile(blender):
        raise RuntimeError("Blender was not found. Set blender in machine.json.")
    glb_path, png_path = os.path.abspath(glb_path), os.path.abspath(png_path)
    script = os.path.join(FORGE_ROOT, "assetforge", "blender_preview.py")
    r = subprocess.run([blender, "-b", "--factory-startup", "-P", script, "--", glb_path, png_path, str(size)],
                       capture_output=True, text=True, timeout=300)
    if not os.path.isfile(png_path):
        raise RuntimeError("Blender preview failed: " + (r.stderr or r.stdout)[-600:])
    return png_path
