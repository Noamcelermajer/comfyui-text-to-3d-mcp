"""Build the A-pose reference picture used by the character concept profile.

    blender -b --factory-startup -P assets/make_apose_reference.py -- assets/apose_reference.png

A plain grey mannequin, front view, orthographic, on white: arms straight and about
45 degrees down from horizontal, legs slightly apart. The image model is shown it as
a pose reference, because a written "A-pose" alone is ignored and a T-pose drawn.
"""
import math
import sys

import bpy
from mathutils import Vector

out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 1024
scene.render.film_transparent = False
world = bpy.data.worlds.new("w")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
scene.view_settings.view_transform = "Standard"

mat = bpy.data.materials.new("grey")
mat.use_nodes = True
mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.42, 0.42, 0.44, 1)
mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.8


def limb(a, b, r):
    a, b = Vector(a), Vector(b)
    d = b - a
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=d.length, location=(a + b) / 2)
    o = bpy.context.object
    o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    o.data.materials.append(mat)
    for p in (a, b):  # rounded joints
        bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=p)
        bpy.context.object.data.materials.append(mat)


def ball(c, r, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=c)
    o = bpy.context.object
    o.scale = scale
    o.data.materials.append(mat)


# Proportions of a roughly 7.5-head-tall figure, 1 unit = 1 head.
ball((0, 0, 7.0), 0.5, (0.82, 0.9, 1.05))                 # head
limb((0, 0, 6.35), (0, 0, 6.05), 0.2)                     # neck
ball((0, 0, 5.15), 1.0, (1.0, 0.55, 1.05))                # chest
ball((0, 0, 3.95), 0.85, (0.95, 0.5, 0.75))               # pelvis
down = math.radians(45)
for s in (-1, 1):
    sh = Vector((s * 1.0, 0, 5.85))
    arm_dir = Vector((s * math.cos(down), 0, -math.sin(down)))
    el = sh + arm_dir * 1.55
    wr = el + arm_dir * 1.35
    limb(sh, el, 0.24)
    limb(el, wr, 0.2)
    ball(wr + arm_dir * 0.35, 0.22, (1, 0.6, 1.3))       # hand
    hip = Vector((s * 0.48, 0, 3.6))
    knee = Vector((s * 0.62, 0, 2.0))
    ank = Vector((s * 0.72, 0, 0.45))
    limb(hip, knee, 0.33)
    limb(knee, ank, 0.26)
    ball(ank + Vector((0, -0.25, -0.2)), 0.25, (1.0, 1.9, 0.6))  # foot

sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 3.0
sun.rotation_euler = (math.radians(30), 0, math.radians(15))
scene.collection.objects.link(sun)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = "ORTHO"
cam.data.ortho_scale = 8.6
cam.location = (0, -20, 3.85)
cam.rotation_euler = (math.radians(90), 0, 0)
scene.collection.objects.link(cam)
scene.camera = cam
scene.render.filepath = out
bpy.ops.render.render(write_still=True)
