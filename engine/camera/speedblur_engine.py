# engine/camera/speedblur_engine.py
from moviepy.editor import VideoFileClip, vfx
import numpy as np
import uuid

def speed_ramp(clip_path, ramp_points=None):
    """
    ramp_points: list of tuples (t, speed) e.g. [(0,1.0),(2,0.4),(4,1.2)]
    If None: simple ramp: slow middle part
    """
    clip = VideoFileClip(clip_path)
    if ramp_points is None:
        # middle slow
        dur = clip.duration
        def speed(t):
            # triangular slow at middle
            return 1.0 if t<dur*0.3 or t>dur*0.7 else 0.5
        out = clip.fx(vfx.speedx, 1)  # we'll implement naive method: split and concat
        # split into three
        a = clip.subclip(0, dur*0.3).fx(vfx.speedx,1.0)
        b = clip.subclip(dur*0.3, dur*0.7).fx(vfx.speedx,0.5)
        c = clip.subclip(dur*0.7, dur).fx(vfx.speedx,1.0)
        final = a.concat(b).concat(c)
    else:
        # implement piecewise
        parts = []
        for i in range(len(ramp_points)-1):
            t0, s0 = ramp_points[i]
            t1, s1 = ramp_points[i+1]
            sub = clip.subclip(t0, t1).fx(vfx.speedx, s0)
            parts.append(sub)
        final = parts[0]
        for p in parts[1:]:
            final = final.concat(p)
    out_path = f"static/videos/speed_{uuid.uuid4().hex[:8]}.mp4"
    final.write_videofile(out_path, fps=clip.fps, codec="libx264", audio_codec="aac")
    return out_path

def add_motion_blur(clip_path, intensity=5):
    # naive motion blur: apply cv2 blur to frames (slow but works)
    import cv2, tempfile, os
    clip = VideoFileClip(clip_path)
    out_path = f"static/videos/blur_{uuid.uuid4().hex[:8]}.mp4"
    tmp = "/tmp/blur_tmp.mp4"
    # write with frame processing
    w,h = clip.w, clip.h
    fps = clip.fps
    import moviepy.video.io.ffmpeg_writer as ffmpeg_writer
    writer = ffmpeg_writer.FFMPEG_VideoWriter(tmp, (w,h), fps)
    t = 0
    dt = 1.0/fps
    while t < clip.duration:
        frame = clip.get_frame(t)
        k = max(1, int(intensity))
        frame_blur = cv2.GaussianBlur(frame, (k|1, k|1), 0)
        writer.write_frame(frame_blur)
        t += dt
    writer.close()
    os.system(f"ffmpeg -y -i {tmp} -c:v libx264 -preset fast {out_path}")
    os.remove(tmp)
    return out_path
