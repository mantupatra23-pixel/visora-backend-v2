# engine/camera/lensfx_engine.py
from moviepy.editor import VideoFileClip, vfx
import numpy as np
import cv2
import os, uuid

def add_vignette(frame, strength=0.5):
    h,w = frame.shape[:2]
    # create vignette mask using gaussian kernels
    kernel_x = cv2.getGaussianKernel(w, w*strength)
    kernel_y = cv2.getGaussianKernel(h, h*strength)
    kernel = kernel_y * kernel_x.T
    mask = kernel / kernel.max()
    vignette = (frame * mask[:,:,None]).astype(np.uint8)
    return vignette

def apply_lens_fx(video_path, flare_strength=0.3, ca_amount=2):
    clip = VideoFileClip(video_path)
    out_path = f"static/videos/lens_{uuid.uuid4().hex[:8]}.mp4"
    import moviepy.video.io.ffmpeg_writer as ffmpeg_writer
    writer = ffmpeg_writer.FFMPEG_VideoWriter('/tmp/lens_tmp.mp4', (clip.w, clip.h), clip.fps)
    t = 0
    dt = 1.0/clip.fps
    while t < clip.duration:
        frame = clip.get_frame(t)
        # vignette
        frame = add_vignette(frame, strength=0.8*flare_strength+0.2)
        # chromatic aberration (split channels and offset)
        b,g,r = cv2.split(frame)
        shift = int(ca_amount)
        b = np.roll(b, shift, axis=1)
        r = np.roll(r, -shift, axis=1)
        merged = cv2.merge([b,g,r])
        writer.write_frame(merged)
        t += dt
    writer.close()
    os.system(f"ffmpeg -y -i /tmp/lens_tmp.mp4 -c:v libx264 -preset fast -pix_fmt yuv420p {out_path}")
    os.remove('/tmp/lens_tmp.mp4')
    return out_path
