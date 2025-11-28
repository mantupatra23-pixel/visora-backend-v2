import bpy
import sys

args = sys.argv
args = args[args.index("--") + 1:] if "--" in args else []

scene_json = args[0]
lip_video = args[1]
out_file = args[2]

# TODO: load scene, character rig, camera, lip animation, assets

bpy.context.scene.render.filepath = out_file
bpy.context.scene.render.image_settings.file_format = "FFMPEG"

bpy.ops.render.render(animation=True)
