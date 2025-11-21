import os
import random
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, vfx

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_BASE = os.path.join(BASE, "templates")


def pick_template_bg(template):
    folder = os.path.join(TEMPLATE_BASE, template)
    if not os.path.exists(folder):
        raise Exception(f"Template '{template}' folder missing!")

    files = [f for f in os.listdir(folder) if f.endswith(".mp4")]

    if not files:
        raise Exception(f"No videos found in {folder}")

    return os.path.join(folder, random.choice(files))


def apply_template_style(template, clip):
    if template == "motivation":
        return clip.fx(vfx.colorx, 1.2).fx(vfx.lum_contrast, 0, 20, 128)
    
    if template == "sad":
        return clip.fx(vfx.blackwhite).fx(vfx.blur, 2)
    
    if template == "romantic":
        return clip.fx(vfx.colorx, 1.3).fx(vfx.fadein, 0.5)
    
    if template == "dialogue":
        return clip.fx(vfx.colorx, 1.1).fx(vfx.resize, width=720)
    
    if template == "sports":
        return clip.fx(vfx.colorx, 1.5).fx(vfx.lum_contrast, 10, 50, 180)
    
    return clip  # default


def build_captions(script, duration):
    txt = TextClip(
        script,
        fontsize=68,
        color="white",
        stroke_color="black",
        stroke_width=3,
        method="caption",
        size=(700, None)
    ).set_position(("center", "bottom")).set_duration(duration)

    return txt
