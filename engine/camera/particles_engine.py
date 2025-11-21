# engine/camera/particles_engine.py
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
import numpy as np
import os, uuid
from PIL import Image, ImageDraw
import random

def generate_particle_frame(w,h,num=100,kind='spark'):
    img = Image.new("RGBA", (w,h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    for i in range(num):
        x = random.randint(0,w)
        y = random.randint(0,h)
        if kind=='spark':
            r = random.randint(1,3)
            color = (255,200,100, random.randint(120,200))
            draw.ellipse((x-r,y-r,x+r,y+r), fill=color)
        elif kind=='rain':
            x2 = x+random.randint(-2,2)
            y2 = y+random.randint(10,30)
            draw.line((x,y,x2,y2), fill=(180,180,255,150), width=1)
        elif kind=='snow':
            r = random.randint(1,4)
            draw.ellipse((x-r,y-r,x+r,y+r), fill=(255,255,255,200))
    return np.array(img)

def overlay_particles(video_path, kind='spark', density=100):
    clip = VideoFileClip(video_path)
    w,h = clip.w, clip.h
    # create short animated particle clip by reusing frames
    frames = []
    duration = clip.duration
    fps = clip.fps
    out_path = f"static/videos/particles_{uuid.uuid4().hex[:8]}.mp4"
    # generate per-frame overlay (may be heavy). Instead generate looped 1s overlay
    one_sec_frames = []
    for t in np.linspace(0,1, int(fps)):
        frame = generate_particle_frame(w,h,density,kind)
        one_sec_frames.append(ImageClip(frame).set_duration(1.0/len(one_sec_frames)))
    overlay = CompositeVideoClip(one_sec_frames).loop(duration=clip.duration)
    final = CompositeVideoClip([clip, overlay.set_position(('center','center'))])
    final.write_videofile(out_path, fps=clip.fps, codec="libx264", audio_codec="aac")
    return out_path
