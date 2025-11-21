import replicate
import uuid
import os
import json
import subprocess

# ============ STEP 1: FULL BODY CHARACTER GENERATION ============

def generate_fullbody_avatar(face_image, style="fortnite"):
    style_prompts = {
        "fortnite": "3D fortnite style full body character, cel shading, game style, HD, clean edges",
        "anime": "anime 3D character full body, cel shade, glowing eyes, HD",
        "pixar": "pixar disney style 3D full body character, cute proportions, clean render",
        "realistic": "realistic metahuman style 3D full body character"
    }

    output = replicate.run(
       "readyplayerme/fullbody:latest",
       input={
           "image": open(face_image, "rb"),
           "prompt": style_prompts.get(style, style_prompts["fortnite"])
       }
    )

    model_url = output["model"]
    model_path = f"static/3d/fullbody_{uuid.uuid4().hex[:8]}.fbx"
    os.system(f"wget {model_url} -O {model_path}")

    return model_path


# ============ STEP 2: MOTION CAPTURE (FULL BODY) ============

def generate_motion_sequence(preset="dance"):
    motion_presets = {
        "dance": "dance_motion.fbx",
        "walk": "walk_cycle.fbx",
        "run": "run_cycle.fbx",
        "fight": "fight_combo.fbx",
        "acting": "conversation.fbx",
        "bollywood": "bollywood_move.fbx",
        "southdance": "south_indian_dance.fbx",
        "naruto": "naruto_run.fbx",
    }

    preset_file = motion_presets.get(preset, "dance_motion.fbx")
    return f"engine/fullbody3d/motions/{preset_file}"


# ============ STEP 3: BLENDER RENDER PIPELINE ============

def render_3d_animation(avatar_fbx, motion_fbx):
    """
    Render full body animation using Blender (headless mode).
    Blender auto-applies motion to rig and renders video.
    """

    output_video = f"static/videos/full3d_{uuid.uuid4().hex[:8]}.mp4"

    blender_script = f"""
import bpy

# Clean scene
bpy.ops.wm.read_factory_settings(use_empty=True)

# Import avatar model
bpy.ops.import_scene.fbx(filepath="{avatar_fbx}")

# Import motion
bpy.ops.import_scene.fbx(filepath="{motion_fbx}")

# Assume armature exists
armature = [obj for obj in bpy.data.objects if obj.type == 'ARMATURE'][0]
bpy.context.view_layer.objects.active = armature
armature.animation_data_create()
armature.animation_data.action = bpy.data.actions[-1]

# Set camera
cam = bpy.data.cameras.new("Camera")
cam_obj = bpy.data.objects.new("Camera", cam)
bpy.context.scene.collection.objects.link(cam_obj)
cam_obj.location = (3, -3, 2)
cam_obj.rotation_euler = (1.2, 0, 0.7)
bpy.context.scene.camera = cam_obj

# Lighting
light = bpy.data.lights.new("Light", type='AREA')
light_obj = bpy.data.objects.new("Light", light)
bpy.context.scene.collection.objects.link(light_obj)
light_obj.location = (5, -3, 5)

# Output settings
bpy.context.scene.render.filepath = "{output_video}"
bpy.context.scene.render.fps = 24
bpy.context.scene.render.resolution_x = 1080
bpy.context.scene.render.resolution_y = 1920
bpy.context.scene.render.engine = 'BLENDER_EEVEE'

bpy.ops.render.render(animation=True)
"""

    # Save temp script
    temp_script = f"/tmp/blender_{uuid.uuid4().hex[:8]}.py"
    with open(temp_script, "w") as f:
        f.write(blender_script)

    # Run Blender in headless mode (Render or GPU server will support)
    subprocess.call(["blender", "-b", "-P", temp_script])

    return output_video


# ============ MASTER FUNCTION ============

def generate_fullbody_motion_video(face_img, style, preset):
    avatar = generate_fullbody_avatar(face_img, style=style)
    motion_file = generate_motion_sequence(preset=preset)
    final_video = render_3d_animation(avatar, motion_file)
    return final_video
