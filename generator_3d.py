"""
engine/generator_3d.py (UPDATED)
- Integrated: face_lock.apply_face_lock
- Integrated: postprocess.derive_scene_seed, merge_audio_to_video, optional upscaler
- Uses Diffusers renderer by default, but pluggable
- generate_scene_video(scenes, output_path, options={}) -> metadata dict
"""

import os
import shutil
import json
import math
import subprocess
import uuid
import time
import logging
from typing import List, Dict, Any

from PIL import Image
import numpy as np
from tqdm import tqdm

import torch

# diffusers
try:
    from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler
    DIFFUSERS_AVAILABLE = True
except Exception:
    DIFFUSERS_AVAILABLE = False

# local helpers we added
try:
    from engine.face_lock import apply_face_lock
except Exception:
    # fallback no-op
    def apply_face_lock(prev_frame, curr_frame, strength=0.9):
        return curr_frame

try:
    from engine.postprocess import derive_scene_seed, merge_audio_to_video, upscale_video_with_realesrgan
except Exception:
    def derive_scene_seed(base, sidx, fidx=0):
        return int((base + sidx * 10000 + fidx) % 2**31)
    def merge_audio_to_video(video_path, audio_path, out_path, audio_bitrate="192k"):
        raise RuntimeError("postprocess.merge_audio_to_video not available")
    def upscale_video_with_realesrgan(in_video_path, out_video_path, scale=2, model=None):
        raise RuntimeError("upscaler not available")

logger = logging.getLogger("generator_3d")
logging.basicConfig(level=logging.INFO)


def get_device():
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p

def cleanup_dir(path):
    try:
        if os.path.exists(path):
            shutil.rmtree(path)
    except Exception as e:
        logger.warning("cleanup failed %s: %s", path, str(e))

def run_ffmpeg_make_video(frames_dir: str, out_path: str, fps: int = 24, crf: int = 18):
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH.")
    input_pattern = os.path.join(frames_dir, "%06d.png")
    tmp_out = out_path + ".tmp.mp4"
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", input_pattern,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", str(crf),
        "-preset", "medium",
        tmp_out
    ]
    logger.info("Running ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        logger.error("ffmpeg failed: %s", proc.stderr.decode("utf-8", errors="ignore"))
        raise RuntimeError("ffmpeg failed to assemble video")
    shutil.move(tmp_out, out_path)
    return out_path


class DiffusionRenderer:
    def __init__(self, model_id: str = "runwayml/stable-diffusion-v1-5", device=None):
        if not DIFFUSERS_AVAILABLE:
            raise RuntimeError("diffusers not installed.")
        self.model_id = model_id
        self.device = device or get_device()
        logger.info("Initializing pipeline on device: %s", self.device)

        self.pipe = StableDiffusionPipeline.from_pretrained(
            model_id,
            safety_checker=None,
            torch_dtype=torch.float16 if self.device.type == "cuda" else torch.float32,
            revision="fp16" if self.device.type == "cuda" else None
        )
        self.pipe.scheduler = DPMSolverMultistepScheduler.from_config(self.pipe.scheduler.config)
        self.pipe = self.pipe.to(self.device)

    def render_image(self, prompt: str, seed: int = None, guidance_scale: float = 7.5, width: int = 560, height: int = 320, num_inference_steps: int = 20):
        generator = None
        if seed is not None:
            generator = torch.Generator(self.device).manual_seed(int(seed))
        # use autocast for cuda else no_grad
        if self.device.type == "cuda":
            with torch.autocast(self.device.type):
                res = self.pipe(prompt, guidance_scale=guidance_scale, width=width, height=height, num_inference_steps=num_inference_steps, generator=generator)
        else:
            with torch.no_grad():
                res = self.pipe(prompt, guidance_scale=guidance_scale, width=width, height=height, num_inference_steps=num_inference_steps, generator=generator)
        image = res.images[0]
        return image


def _compose_prompt_from_scene(scene: Dict[str, Any]) -> str:
    parts = []
    chars = scene.get("characters", [])
    if chars:
        parts.append(", ".join(chars))
    if scene.get("action"):
        parts.append(scene["action"])
    if scene.get("emotion"):
        parts.append("feeling " + scene["emotion"])
    if scene.get("location"):
        parts.append("in the " + scene["location"])
    if scene.get("background"):
        parts.append(scene["background"])
    if scene.get("weather") and scene["weather"] != "none":
        parts.append(scene["weather"])
    if scene.get("time"):
        parts.append(scene["time"])
    parts.append(scene.get("style", "cinematic"))
    prompt = ", ".join(parts)
    prompt += ", ultra-detailed, 3d look, cinematic lighting, film grain, high detail, realistic camera"
    return prompt


def generate_scene_video(scenes: List[Dict[str, Any]], output_path: str, options: Dict[str, Any] = None) -> Dict[str, Any]:
    options = options or {}
    fps = int(options.get("fps", 24))
    frames_per_scene = int(options.get("frames_per_scene", 24))
    width = int(options.get("width", 560))
    height = int(options.get("height", 320))
    model_id = options.get("model_id", "runwayml/stable-diffusion-v1-5")
    guidance_scale = float(options.get("guidance_scale", 7.5))
    steps = int(options.get("steps", 20))
    seed_base = int(options.get("seed", int(time.time()) % 1000000))
    tmp_dir_root = ensure_dir(options.get("tmp_dir", f"/tmp/visora_gen_{uuid.uuid4().hex}"))
    frames_dir = os.path.join(tmp_dir_root, "frames")
    ensure_dir(frames_dir)

    meta = {
        "output_path": output_path,
        "tmp_dir": tmp_dir_root,
        "status": "started",
        "scenes": scenes,
        "frames_rendered": 0,
        "fps": fps,
        "error": None
    }

    logger.info("generate_scene_video start. model=%s device=%s fps=%d frames_per_scene=%d", model_id, get_device(), fps, frames_per_scene)

    renderer = DiffusionRenderer(model_id=model_id, device=get_device())

    frame_index = 0
    prev_frame = None

    try:
        for s_index, scene in enumerate(scenes):
            prompt_base = _compose_prompt_from_scene(scene)
            logger.info("Scene %d prompt: %s", s_index, prompt_base)
            scene_seed = derive_scene_seed(seed_base, s_index, 0)

            for f in range(frames_per_scene):
                cur_seed = derive_scene_seed(seed_base, s_index, f)
                # Slight prompt jitter for motion
                prompt = prompt_base + f" --frame {f+1}/{frames_per_scene}"
                img = renderer.render_image(
                    prompt=prompt,
                    seed=cur_seed,
                    guidance_scale=guidance_scale,
                    width=width,
                    height=height,
                    num_inference_steps=steps
                )
                # Apply face locking if available
                if prev_frame is not None:
                    try:
                        img = apply_face_lock(prev_frame, img, strength=0.92)
                    except Exception as e:
                        logger.warning("face_lock error: %s", str(e))
                # Save frame
                frame_filename = os.path.join(frames_dir, f"{frame_index:06d}.png")
                img.save(frame_filename)
                prev_frame = img
                frame_index += 1
                meta["frames_rendered"] = frame_index

        # assemble
        ensure_dir(os.path.dirname(output_path))
        video_file = run_ffmpeg_make_video(frames_dir, output_path, fps=fps, crf=int(options.get("crf", 18)))
        meta["status"] = "generated"
        meta["output"] = video_file
        meta["frames_dir"] = frames_dir
        logger.info("Video generated at %s (%d frames)", video_file, frame_index)

        # optional upscaling
        if options.get("upscale", False):
            try:
                up_path = output_path.replace(".mp4", f".up{options.get('upscale_factor',2)}.mp4")
                upscale_video_with_realesrgan(video_file, up_path, scale=options.get("upscale_factor",2))
                video_file = up_path
                meta["output"] = video_file
                logger.info("Upscaled video saved to %s", video_file)
            except Exception as e:
                logger.warning("Upscale skipped: %s", str(e))

        # optional audio merge
        audio_file = options.get("audio_file")
        if audio_file:
            try:
                merged = output_path.replace(".mp4", ".audio.mp4")
                merge_audio_to_video(video_file, audio_file, merged)
                video_file = merged
                meta["output"] = video_file
                logger.info("Audio merged to %s", video_file)
            except Exception as e:
                logger.warning("Audio merge failed: %s", str(e))

        meta["status"] = "done"
        return meta

    except Exception as e:
        logger.error("Generation failed: %s", str(e))
        meta["status"] = "error"
        meta["error"] = str(e)
        return meta

    finally:
        # keep frames for debug — comment cleanup if you want to remove frames automatically
        # cleanup_dir(tmp_dir_root)
        pass
