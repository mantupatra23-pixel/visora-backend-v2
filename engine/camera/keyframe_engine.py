# engine/camera/keyframe_engine.py
from moviepy.editor import VideoFileClip
import uuid

def keyframe_camera(clip_path, keyframes=None):
    """
    keyframes: list of dicts [{'t':0, 'zoom':1.0, 'x':0,'y':0}, ...]
    If None: auto gentle zoom + slight pan
    """
    clip = VideoFileClip(clip_path)
    if keyframes is None:
        # default: start center small zoom -> end slight zoom
        def dynamic_resize(get_frame, t):
            zoom = 1.0 + 0.05 * (t/clip.duration)
            frame = get_frame(t)
            # moviepy supports lambda resize
            return frame
        out = clip.resize(lambda t: 1 + 0.05 * (t/clip.duration))
    else:
        # Complex: build piecewise transforms (simpler approach: interpolation)
        import numpy as np
        times = [kf['t'] for kf in keyframes]
        zooms = [kf.get('zoom',1.0) for kf in keyframes]
        def zoom_func(t):
            return np.interp(t, times, zooms)
        out = clip.resize(lambda t: zoom_func(t))
    out_path = f"static/videos/keyframe_{uuid.uuid4().hex[:8]}.mp4"
    out.write_videofile(out_path, fps=clip.fps, codec="libx264", audio_codec="aac")
    return out_path
