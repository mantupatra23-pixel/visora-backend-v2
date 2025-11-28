# engines/blender_render_script.py
"""
Improved Blender render script.
Usage (on Blender host):
blender --background --python engines/blender_render_script.py -- <config_json> <output_path>

Config JSON example:
{
  "script": "Hello from Visora",
  "preset": "reel",
  "bg_image": "/opt/assets/bg.jpg",          # optional background image
  "avatar": "/opt/assets/avatar.fbx",        # optional avatar/character (fbx/obj)
  "transparent": false,                      # if true, renders transparent bg (png with alpha / needs codec support)
  "resolution": [1280,720],
  "fps": 24
}
"""

import sys, json, os
from pathlib import Path

# Blender modules only available when running inside blender
import bpy
import mathutils
import math
import random

def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def import_avatar(path):
    p = str(path)
    if p.lower().endswith('.fbx'):
        bpy.ops.import_scene.fbx(filepath=p)
    elif p.lower().endswith('.obj'):
        bpy.ops.import_scene.obj(filepath=p)
    else:
        print("Unsupported avatar format:", p)
    # assume avatar root at origin; return first object
    objs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    return objs[0] if objs else None

def setup_camera(location=(0, -6, 2), look_at=(0,0,1)):
    cam_data = bpy.data.cameras.new("Camera")
    cam = bpy.data.objects.new("Camera", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = location
    # point camera to look_at
    direction = mathutils.Vector(look_at) - cam.location
    rot_quat = direction.to_track_quat('-Z', 'Y')
    cam.rotation_euler = rot_quat.to_euler()
    bpy.context.scene.camera = cam
    return cam

def setup_light(location=(3, -3, 6)):
    light_data = bpy.data.lights.new(name="KeyLight", type='AREA')
    light = bpy.data.objects.new(name="KeyLight", object_data=light_data)
    bpy.context.collection.objects.link(light)
    light.location = location
    light.data.energy = 1000
    return light

def create_background_plane(bg_image=None):
    # create large plane behind scene and assign image texture if provided
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 6, 0))
    plane = bpy.context.active_object
    plane.rotation_euler[0] = math.radians(90)
    mat = bpy.data.materials.new(name="BGMat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bg_image and Path(bg_image).exists():
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(bg_image)
        links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    else:
        bsdf.inputs['Base Color'].default_value = (0.06, 0.06, 0.06, 1)
    plane.data.materials.append(mat)
    return plane

def add_text_object(text, size=0.6, location=(0,0,1.6)):
    font_curve = bpy.data.curves.new(type="FONT", name="TextCurve")
    font_obj = bpy.data.objects.new(name="TextObj", object_data=font_curve)
    font_curve.body = text
    font_curve.size = size
    bpy.context.collection.objects.link(font_obj)
    font_obj.location = location
    # center origin
    bpy.ops.object.select_all(action='DESELECT')
    font_obj.select_set(True)
    bpy.context.view_layer.objects.active = font_obj
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
    return font_obj

def animate_camera_orbit(cam, center=(0,0,1), radius=6, frames=120):
    for f in range(0, frames+1, 5):
        t = f / frames * 2*math.pi
        cam.location.x = center[0] + radius * math.cos(t)
        cam.location.y = center[1] + radius * math.sin(t)
        cam.location.z = center[2] + 1.5*math.sin(t*0.5)
        cam.keyframe_insert(data_path="location", frame=f)

def animate_text_pop(obj, frame_start=1, frame_mid=12):
    obj.scale = (0.01, 0.01, 0.01)
    obj.keyframe_insert(data_path="scale", frame=frame_start)
    obj.scale = (1.0, 1.0, 1.0)
    obj.keyframe_insert(data_path="scale", frame=frame_mid)

def render_settings(output_path, resolution, fps, frame_end, transparent=False):
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'   # fast for headless; for higher quality use CYCLES
    scene.render.image_settings.file_format = 'FFMPEG'
    scene.render.ffmpeg.format = 'MPEG4'
    scene.render.ffmpeg.codec = 'H264'
    scene.render.ffmpeg.constant_rate_factor = 'MEDIUM'
    if transparent:
        scene.render.film_transparent = True
        scene.render.image_settings.color_mode = 'RGBA'
    scene.render.resolution_x = resolution[0]
    scene.render.resolution_y = resolution[1]
    scene.render.fps = fps
    scene.frame_end = frame_end
    scene.render.filepath = str(output_path)

def main():
    argv = sys.argv
    if "--" in argv:
        idx = argv.index("--")
        argv = argv[idx+1:]
    else:
        argv = []

    if len(argv) < 2:
        print("Usage: blender --background --python engines/blender_render_script.py -- <config.json> <output_path>")
        return

    cfg_path = Path(argv[0])
    out_path = Path(argv[1])
    cfg = {}
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())

    script_text = cfg.get("script", "Hello from Visora")
    preset = cfg.get("preset", "reel")
    bg_image = cfg.get("bg_image")
    avatar_path = cfg.get("avatar")
    transparent = cfg.get("transparent", False)
    res = cfg.get("resolution", [1280,720])
    fps = cfg.get("fps", 24)

    if preset == "short":
        frames = cfg.get("frames", 90)
    else:
        frames = cfg.get("frames", 120)

    clear_scene()
    # background / plane
    create_background_plane(bg_image=bg_image)
    # import avatar if provided
    avatar = None
    if avatar_path:
        if Path(avatar_path).exists():
            try:
                avatar = import_avatar(avatar_path)
                if avatar:
                    avatar.location = (0,0,0.7)
            except Exception as e:
                print("Avatar import failed:", e)

    cam = setup_camera(location=(0, -6, 2.2), look_at=(0,0,1))
    setup_light()
    txt = add_text_object(script_text, size=0.6, location=(0, -0.4, 1.6))
    animate_text_pop(txt, frame_start=1, frame_mid=18)
    animate_camera_orbit(cam, center=(0,0,1), radius=6, frames=frames)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    render_settings(out_path, resolution=res, fps=fps, frame_end=frames, transparent=transparent)
    bpy.ops.render.render(animation=True)
    print("Render completed:", out_path)

if __name__ == "__main__":
    main()
