# engine/multiscene10/scenes_utils.py
import math, os, uuid, json
from moviepy.editor import VideoFileClip, concatenate_videoclips, CompositeVideoClip, TextClip, AudioFileClip, vfx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRESETS_PATH = os.path.join(BASE_DIR, "presets.json")
with open(PRESETS_PATH, "r") as f:
    PRESETS = json.load(f)

def smart_split_script(script_text, max_scenes=3):
    """
    Splits script by sentences into up to max_scenes parts.
    If script contains markers like [--scene--] uses them.
    """
    if "[--scene--]" in script_text:
        parts = [p.strip() for p in script_text.split("[--scene--]") if p.strip()]
        return parts[:max_scenes]
    # naive split by sentences
    import re
    sentences = re.split(r'(?<=[.!?])\s+', script_text.strip())
    if len(sentences) <= max_scenes:
        return [" ".join(sentences)]
    # distribute sentences to scenes
    avg = math.ceil(len(sentences)/max_scenes)
    scenes = []
    for i in range(0, len(sentences), avg):
        scenes.append(" ".join(sentences[i:i+avg]).strip())
    # pad/truncate
    return (scenes + [""]*max_scenes)[:max_scenes]

def build_subtitle_clip(text, duration, width=900):
    # returns a TextClip for the duration (position bottom)
    if not text:
        return None
    txt = TextClip(text, fontsize=56, color="white", stroke_color="black", stroke_width=3, size=(width, None), method="caption")
    txt = txt.set_position(("center", "bottom")).set_duration(duration)
    return txt

def finalize_and_export(final_clip, out_path, fps=24):
    """
    final_clip: moviepy clip
    out_path: final file path
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # ensure audio codec aac for compatibility
    final_clip.write_videofile(out_path, fps=fps, codec="libx264", audio_codec="aac")
    return out_path

def apply_transition(clip_a, clip_b, transition_type="crossfade", duration=0.6):
    """
    Produces combined clip1->transition->clip2
    """
    if transition_type == "crossfade":
        # crossfadeout on a and crossfadein on b
        return clip_a.crossfadeout(duration).set_end(clip_a.duration).concatenate_videoclips([clip_a, clip_b.set_start(clip_a.duration).crossfadein(duration)])
    elif transition_type == "dip_to_black":
        t = duration
        a = clip_a.fx(vfx.fadeout, t)
        b = clip_b.fx(vfx.fadein, t)
        return concatenate_videoclips([a, b])
    elif transition_type == "whip":
        # quick zoom + blur on end of A and start of B (approx)
        a = clip_a.fx(vfx.lum_contrast, 0, 20, 128).fx(vfx.fadeout, duration*0.6)
        b = clip_b.fx(vfx.fadein, duration*0.6)
        return concatenate_videoclips([a, b])
    elif transition_type == "zoom":
        a = clip_a.fx(vfx.zoom_in, 1.08).fx(vfx.fadeout, duration)
        b = clip_b.fx(vfx.zoom_in, 1.02).fx(vfx.fadein, duration)
        return concatenate_videoclips([a, b])
    else:
        return concatenate_videoclips([clip_a, clip_b])
