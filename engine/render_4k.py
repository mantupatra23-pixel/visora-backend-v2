# engine/render_4k.py
"""
Simple 4K render_scene() implementation with two modes:
 - Blender headless (preferred): uses BLENDER_EXEC env to call a .blend or python script
 - Fallback: uses ffmpeg to generate a 4K placeholder video (static background + text or upscales primary_video)
Returns dict: { "status": "ok", "video": "<path to mp4>" } or raises RuntimeError on failure.
"""

import os
import subprocess
from pathlib import Path
import json
import shutil
import tempfile

def _run(cmd, env=None, cwd=None, timeout=3600):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc.stdout

def render_with_blender(scene_spec: dict, out_dir: Path, render_params: dict) -> Path:
    """
    Call Blender in background with a small python script to set resolution & render.
    Requires BLENDER_EXEC env var pointing to blender binary.
    Expects Blender to handle scene creation or use a template .blend file.
    """
    BLENDER_EXEC = os.environ.get("BLENDER_EXEC", "/usr/bin/blender")
    if not Path(BLENDER_EXEC).exists():
        raise RuntimeError("Blender binary not found at BLENDER_EXEC: " + BLENDER_EXEC)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "render_4k.mp4"

    # Write a small Blender Python script to configure render (the script can be extended for actual scene)
    blender_script = out_dir / "blender_render_script.py"
    width = int(render_params.get("resolution", {}).get("w", 3840))
    height = int(render_params.get("resolution", {}).get("h", 2160))
    samples = int(render_params.get("samples", 128))
    engine = render_params.get("engine", "CYCLES").upper()  # CYCLES or BLENDER_EEVEE

    blender_py = f"""
import bpy, sys, os
# minimal: set cycles + resolution + render animation from frames if available
bpy.context.scene.render.resolution_x = {width}
bpy.context.scene.render.resolution_y = {height}
bpy.context.scene.render.resolution_percentage = 100
bpy.context.scene.render.engine = '{'CYCLES' if engine=='CYCLES' else 'BLENDER_EEVEE'}'
# set samples if cycles
try:
    bpy.context.scene.cycles.samples = {samples}
except Exception:
    pass

# Use existing scene, render a single frame to PNG then ffmpeg to mp4 externally
out_png = os.path.join(r"{out_dir}", "frame.png")
bpy.context.scene.frame_set(1)
bpy.ops.render.render(write_still=True)
bpy.data.images['Render Result'].save_render(out_png)
"""

    blender_script.write_text(blender_py)

    # call blender in background to run script on default .blend (or empty)
    cmd = [BLENDER_EXEC, "--background", "--python", str(blender_script)]
    _run(cmd)

    # Convert PNG -> mp4 with ffmpeg (still image -> 3s video)
    png = out_dir / "frame.png"
    if not png.exists():
        raise RuntimeError("Blender failed to produce frame.png")

    fps = int(render_params.get("fps", 30))
    duration = int(render_params.get("duration_seconds", 3))
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(png),
        "-c:v", "libx264", "-t", str(duration), "-pix_fmt", "yuv420p",
        "-r", str(fps), "-vf", f"scale={width}:{height}", str(out_file)
    ]
    _run(ffmpeg_cmd)
    return out_file

def render_placeholder_4k(scene_spec: dict, out_dir: Path, render_params: dict) -> Path:
    """
    Fallback placeholder: generate a 4K MP4 with ffmpeg (solid bg + text),
    or upscale an existing 'primary_video' in scene_spec.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "render_4k_placeholder.mp4"

    width = int(render_params.get("resolution", {}).get("w", 3840))
    height = int(render_params.get("resolution", {}).get("h", 2160))
    fps = int(render_params.get("fps", 30))
    duration = int(render_params.get("duration_seconds", 3))

    # if user provided a primary_video, upscale it
    primary = scene_spec.get("primary_video")
    if primary:
        primary = Path(primary)
        if primary.exists():
            # upscale using ffmpeg
            cmd = [
                "ffmpeg", "-y", "-i", str(primary),
                "-vf", f"scale={width}:{height}:flags=lanczos",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-r", str(fps), str(out_file)
            ]
            _run(cmd)
            return out_file

    # else create a colored background with text
    title = scene_spec.get("title", scene_spec.get("script", "Visora 4K Render"))
    # draw text overlay via ffmpeg drawtext (requires libfreetype)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=size={width}x{height}:duration={duration}:rate={fps}:color=#0a0a0a",
        "-vf", f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:fontsize=120:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:text='{title}'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        str(out_file)
    ]
    _run(cmd)
    return out_file

def render_scene(scene_spec: dict, out_dir: str, render_params: dict) -> dict:
    """
    Public entrypoint expected by app.py
    scene_spec: parsed scene dict
    out_dir: path (string) where to write render outputs (job's render_out)
    render_params: optional params forwarded from API
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # merge default resolution if not provided
    defaults = {"resolution": {"w": 3840, "h": 2160}, "fps": 30, "duration_seconds": 3, "samples": 128}
    merged = defaults.copy()
    merged.update(render_params or {})
    # ensure nested resolution merge
    if "resolution" in render_params:
        merged["resolution"] = {**defaults["resolution"], **render_params.get("resolution", {})}

    try:
        BLENDER_EXEC = os.environ.get("BLENDER_EXEC")
        if BLENDER_EXEC and Path(BLENDER_EXEC).exists():
            video_path = render_with_blender(scene_spec, out_dir, merged)
        else:
            video_path = render_placeholder_4k(scene_spec, out_dir, merged)
    except Exception as e:
        raise

    return {"status": "ok", "video": str(video_path)}
