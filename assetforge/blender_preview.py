"""Run inside Blender: blender -b -P blender_preview.py -- model.glb out.png [size]

Renders a front view and a three-quarter view next to each other in one image.
"""
import math
import os
import sys

import bpy
import mathutils

glb, out_png = sys.argv[sys.argv.index("--") + 1:][:2]
size = int(sys.argv[sys.argv.index("--") + 3]) if len(sys.argv) > sys.argv.index("--") + 3 else 512

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = size
scene.render.resolution_y = size

world = bpy.data.worlds.new("World")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
bg.inputs[0].default_value = (0.14, 0.15, 0.18, 1.0)
bg.inputs[1].default_value = 2.0

sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
sun.data.energy = 4.0
sun.rotation_euler = (math.radians(55), math.radians(-20), math.radians(25))
scene.collection.objects.link(sun)

bpy.ops.import_scene.gltf(filepath=glb)
pts = [o.matrix_world @ v.co for o in bpy.data.objects if o.type == "MESH" for v in o.data.vertices]
lo = mathutils.Vector([min(p[i] for p in pts) for i in range(3)])
hi = mathutils.Vector([max(p[i] for p in pts) for i in range(3)])
center, diag = (lo + hi) * 0.5, (hi - lo).length

cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
cam.data.type = "ORTHO"
cam.data.ortho_scale = diag * 1.1
scene.collection.objects.link(cam)
scene.camera = cam

tiles = []
for tag, yaw, pitch in (("front", 0.0, 0.0), ("34", 40.0, 20.0)):
    y, p = math.radians(yaw), math.radians(pitch)
    d = mathutils.Vector((math.sin(y) * math.cos(p), -math.cos(y) * math.cos(p), math.sin(p)))
    cam.location = center + d * (diag * 3.0)
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{out_png}.{tag}.png"
    bpy.ops.render.render(write_still=True)
    tiles.append(scene.render.filepath)

# Join the two tiles with Blender's own image API (no extra Python packages needed).
imgs = [bpy.data.images.load(t) for t in tiles]
sheet = bpy.data.images.new("sheet", width=size * 2, height=size, alpha=False)
px = [0.0] * (size * 2 * size * 4)
for i, img in enumerate(imgs):
    src = list(img.pixels)
    for row in range(size):
        a = row * size * 4
        b = (row * size * 2 + i * size) * 4
        px[b:b + size * 4] = src[a:a + size * 4]
sheet.pixels = px
sheet.filepath_raw = out_png
sheet.file_format = "PNG"
sheet.save()
for t in tiles:
    os.remove(t)
