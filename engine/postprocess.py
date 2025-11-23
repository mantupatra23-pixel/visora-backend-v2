# engine/postprocess.py
import os
import subprocess
import logging
from typing import Optional

logger = logging.getLogger("postprocess")
logging.basicConfig(level=logging.INFO)

def ensure_ffmpeg():
    if subprocess.call(["which", "ffmpeg"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        raise RuntimeError("ffmpeg not found in PATH; install ffmpeg to use postprocess features.")

# ----------------
# Upscaling (optional)
# ----------------
def upscale_video_with_realesrgan(in_video_path: str, out_video_path: str, scale: int = 2, model: Optional[str] = None):
    """
    Tries to call an installed realesrgan CLI or python wrapper.
    If not available, raises RuntimeError.
    Example CLI: realesrgan-ncnn-vulkan -i in.png -o out.png -s 2
    For video, we extract frames, upscale, then re-encode.
    """
    # Check presence of realesrgan or torch implementation
    # Minimal approach: use ffmpeg + external realesrgan CLI if present
    if shutil.which("realesrgan-ncnn-vulkan") is not None:
        # Extract frames
        tmp_dir = in_video_path + "_frames"
        os.makedirs(tmp_dir, exist_ok=True)
        cmd = ["ffmpeg", "-i", in_video_path, os.path.join(tmp_dir, "%06d.png")]
        subprocess.check_call(cmd)
        # Upscale each frame using realesrgan-ncnn-vulkan
        for f in sorted(os.listdir(tmp_dir)):
            src = os.path.join(tmp_dir, f)
            dst = src  # overwrite
            cmd2 = ["realesrgan-ncnn-vulkan", "-i", src, "-o", dst, "-s", str(scale)]
            subprocess.check_call(cmd2)
        # Re-encode frames to video
        cmd3 = ["ffmpeg", "-y", "-framerate", "24", "-i", os.path.join(tmp_dir, "%06d.png"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", out_video_path]
        subprocess.check_call(cmd3)
        # cleanup
        shutil.rmtree(tmp_dir)
        return out_video_path
    else:
        raise RuntimeError("No Real-ESRGAN CLI found (realesrgan-ncnn-vulkan). Install or use another upscaler.")

# ----------------
# Audio merge
# ----------------
def merge_audio_to_video(video_path: str, audio_path: str, out_path: str, audio_bitrate: str = "192k"):
    """
    Merges supplied audio (wav/mp3) into the video using ffmpeg.
    Keeps original video encoding and replaces/adds audio track.
    """
    ensure_ffmpeg()
    # build ffmpeg cmd
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-map", "0:v:0",
        "-map", "1:a:0",
        out_path
    ]
    logger.info("Merging audio: %s", " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        logger.error("ffmpeg merge failed: %s", proc.stderr.decode("utf-8", errors="ignore"))
        raise RuntimeError("ffmpeg audio merge failed")
    return out_path

# ----------------
# Seed helper
# ----------------
def derive_scene_seed(base_seed: int, scene_index: int, frame_index: int = 0):
    # deterministic unique seed for scene/frame
    return int((base_seed + scene_index * 10000 + frame_index) % 2**31)
