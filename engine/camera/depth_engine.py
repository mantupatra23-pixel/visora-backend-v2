# engine/camera/depth_engine.py
import os
import numpy as np
import cv2
import uuid

# Optional: Replicate cloud model usage (higher quality). Requires REPLICATE_API_TOKEN env var.
try:
    import replicate
    _HAS_REPLICATE = True
except:
    _HAS_REPLICATE = False

def estimate_depth_local(image_path):
    """
    Local fast depth-ish using single-image gradient heuristic (fallback).
    Not as good as MiDaS but runs without heavy deps.
    Returns depth map path (grayscale PNG).
    """
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # simple edge-based 'depth' proxy
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    depth = cv2.normalize(np.abs(lap), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    out = f"static/temp/depth_{uuid.uuid4().hex[:8]}.png"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cv2.imwrite(out, depth)
    return out

def estimate_depth_replicate(image_path):
    if not _HAS_REPLICATE:
        raise RuntimeError("replicate not installed or available")
    # Example model key — change to a depth model you prefer
    model = "midas/mi-dpt-large"  # placeholder; change if you have specific replicate model
    output = replicate.run(model, input={"image": open(image_path, "rb")})
    # output is expected URL - download
    out_url = output[0] if isinstance(output, list) else output
    out_path = f"static/temp/depth_{uuid.uuid4().hex[:8]}.png"
    os.system(f"wget {out_url} -O {out_path}")
    return out_path

def create_parallax_video(foreground_video, depth_map_path, strength=0.15):
    """
    Generate a parallax background effect using depth map.
    This returns path to processed video (same duration).
    """
    from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
    clip = VideoFileClip(foreground_video)
    depth_img = cv2.imread(depth_map_path, cv2.IMREAD_GRAYSCALE)
    h, w = depth_img.shape
    # prepare image clip for displacement (we'll use simple left-right shift per frame)
    frames = []
    out_path = f"static/videos/parallax_{uuid.uuid4().hex[:8]}.mp4"
    # faster: produce a simple composite by shifting a blurred bg extracted from the clip
    bg = clip.resize(width=clip.w).fx(lambda c: c)  # keep same
    # create small shift animation with depth weighting
    def make_frame(t):
        frame = bg.get_frame(t).copy()
        # use a normalized depth map to shift x positions proportionally (quick method)
        nx = (depth_img.astype(np.float32) / 255.0) * strength * clip.w
        # create shifted frame by rolling horizontally by small amount depending on mean depth
        shift = int((np.mean(nx) - strength*clip.w/2) * 0.5)
        return np.roll(frame, shift, axis=1)
    # write using moviepy generator
    import moviepy.video.io.ffmpeg_writer as ffmpeg_writer
    writer = ffmpeg_writer.FFMPEG_VideoWriter(out_path, (clip.w, clip.h), clip.fps)
    duration = clip.duration
    t = 0.0
    dt = 1.0/clip.fps
    while t < duration:
        writer.write_frame(make_frame(t))
        t += dt
    writer.close()
    return out_path
