# --- Camera pipeline integration for multi-scene engine ---
# Paste/replace this block inside engine/multiscene/multi_scene_engine.py

import logging
from pathlib import Path
import uuid
import os

# MoviePy tools
from moviepy.editor import VideoFileClip, concatenate_videoclips

# Camera engines
try:
    from engine.camera.depth_engine import (
        estimate_depth_local,
        estimate_depth_replicate,
        create_parallax_video,
    )
except Exception:
    logging.warning("depth_engine missing — skipping depth effects")
    estimate_depth_local = estimate_depth_replicate = create_parallax_video = None

try:
    from engine.camera.stabilize_engine import stabilize_video
except Exception:
    logging.warning("stabilize_engine missing — skipping stabilization")
    stabilize_video = None

try:
    from engine.camera.keyframe_engine import keyframe_camera
except Exception:
    logging.warning("keyframe_engine missing — skipping keyframe motion")
    keyframe_camera = None

try:
    from engine.camera.speedblur_engine import speed_ramp, add_motion_blur
except Exception:
    logging.warning("speedblur_engine missing — skipping speed/motion blur")
    speed_ramp = add_motion_blur = None

try:
    from engine.camera.particles_engine import overlay_particles
except Exception:
    logging.warning("particles_engine missing — skipping particles")
    overlay_particles = None

try:
    from engine.camera.lensfx_engine import apply_lens_fx
except Exception:
    logging.warning("lensfx_engine missing — skipping lens FX")
    apply_lens_fx = None


def _ensure_output_dir(base_dir="static/temp"):
    outdir = Path(base_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir


def _unique_path(prefix="clip", ext=".mp4", outdir="static/temp"):
    outdir_p = _ensure_output_dir(outdir)
    return str(outdir_p / f"{prefix}_{uuid.uuid4().hex[:8]}{ext}")


# PIPELINE FOR SINGLE SCENE
def apply_camera_pipeline(
    scene_input_path,
    user_face_image=None,
    depth_mode="local",
    do_stabilize=True,
    do_parallax=True,
    parallax_strength=0.10,
    do_keyframe=True,
    do_speed_ramp=False,
    do_particles=False,
    particle_opts=None,
    do_motion_blur=False,
    do_lens_fx=True,
    temp_out_dir="static/temp",
):
    scene_path = str(scene_input_path)
    logging.info(f"Starting camera pipeline for {scene_path}")

    # 1. Stabilization
    try:
        if do_stabilize and stabilize_video:
            stabilized = stabilize_video(scene_path)
            if stabilized:
                scene_path = stabilized
    except Exception as e:
        logging.exception("Stabilization failed: %s", e)

    # 2. Parallax Depth
    try:
        if do_parallax and create_parallax_video:
            depth = None
            if depth_mode == "replicate" and estimate_depth_replicate:
                try:
                    depth = estimate_depth_replicate(user_face_image or scene_path)
                except:
                    pass
            if not depth and estimate_depth_local:
                try:
                    depth = estimate_depth_local(user_face_image or scene_path)
                except:
                    pass

            if depth is not None:
                outp = _unique_path("parallax", outdir=temp_out_dir)
                newp = create_parallax_video(scene_path, depth, strength=parallax_strength, out_path=outp)
                if newp:
                    scene_path = newp
    except Exception as e:
        logging.exception("Parallax failed: %s", e)

    # 3. Keyframe camera motion
    try:
        if do_keyframe and keyframe_camera:
            outp = keyframe_camera(scene_path)
            if outp:
                scene_path = outp
    except Exception as e:
        logging.exception("Keyframe failed: %s", e)

    # 4. Speed ramp & blur
    try:
        if do_speed_ramp and speed_ramp:
            outp = speed_ramp(scene_path)
            if outp:
                scene_path = outp
        if do_motion_blur and add_motion_blur:
            outp = add_motion_blur(scene_path)
            if outp:
                scene_path = outp
    except Exception as e:
        logging.exception("Speed/Blur failed: %s", e)

    # 5. Particles
    try:
        if do_particles and overlay_particles:
            outp = overlay_particles(scene_path, **(particle_opts or {}))
            if outp:
                scene_path = outp
    except Exception as e:
        logging.exception("Particles failed: %s", e)

    # 6. Lens FX
    try:
        if do_lens_fx and apply_lens_fx:
            outp = apply_lens_fx(scene_path)
            if outp:
                scene_path = outp
    except Exception as e:
        logging.exception("Lens FX failed: %s", e)

    return scene_path


# MULTI SCENE PIPELINE
def generate_multiscene_with_camera_pipeline(scenes, user_face=None, out_dir="static/temp"):
    processed = []

    for i, scene in enumerate(scenes):
        options = dict(
            do_stabilize=True,
            do_parallax=(i == 0),
            parallax_strength=0.10,
            do_keyframe=True,
            do_speed_ramp=(i == 1),
            do_particles=(i == 2),
            particle_opts={"kind": "rain", "density": 80},
            do_motion_blur=(i == 1),
            do_lens_fx=True,
            temp_out_dir=out_dir,
        )
        outp = apply_camera_pipeline(scene, user_face_image=user_face, **options)
        processed.append(outp)

    clips = []
    for p in processed:
        try:
            clips.append(VideoFileClip(p))
        except:
            continue

    final_path = _unique_path("final_multiscene", outdir=out_dir)
    concat = concatenate_videoclips(clips, method="compose")
    concat.write_videofile(final_path, codec="libx264", audio_codec="aac", threads=2, logger=None)

    for c in clips:
        try:
            c.close()
        except:
            pass

    return final_path
