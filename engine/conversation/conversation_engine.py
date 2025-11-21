# engine/conversation/conversation_engine.py
import os
import uuid
import json
import math
import tempfile
from moviepy.editor import (
    VideoFileClip, AudioFileClip, CompositeVideoClip,
    concatenate_videoclips, vfx, ImageClip
)

# Reuse these engines from your repo (assumed present)
from engine.avatar.avatar_engine import generate_talking_avatar
from engine.audio.music_sfx_engine import render_music, mix_tracks, generate_sfx
from engine.multiscene10.scenes_utils import build_subtitle_clip, finalize_and_export

BASE_STATIC = "static/videos"
os.makedirs(BASE_STATIC, exist_ok=True)

def _parse_conversation(script_text, avatars):
    """
    Accepts either:
      - script_text with markers: [A:] Hi [B:] Hello [A:] ...
      - or plain lines where each line starts with avatar name like "A: Hello"
    avatars: dict mapping avatar key -> avatar config (face, gender, etc)
    Returns list of turns: [{"speaker":"A","text":"Hello", "avatar_conf": {...}}, ...]
    """
    lines = []
    # normalize CRLF
    txt = script_text.replace("\r\n", "\n").strip()
    # if markers used
    parts = []
    # split by pattern like "A:" or "Bob:"
    for raw in txt.split("\n"):
        raw = raw.strip()
        if not raw: continue
        # look for "X: text"
        if ":" in raw:
            sp, t = raw.split(":", 1)
            parts.append({"speaker": sp.strip(), "text": t.strip()})
        else:
            # fallback: append to last if exists
            if parts:
                parts[-1]["text"] += " " + raw
            else:
                parts.append({"speaker": list(avatars.keys())[0], "text": raw})
    # attach avatar config
    turns = []
    for p in parts:
        sp = p["speaker"]
        conf = avatars.get(sp, {})
        turns.append({"speaker": sp, "text": p["text"], "avatar_conf": conf})
    return turns

def _render_line_to_clip(turn, global_opts):
    """
    turn: {"speaker","text","avatar_conf"}
    global_opts: things like mode, default duration, bg, outfit, emotion etc.
    Returns a moviepy VideoFileClip for that line (path or clip).
    """
    conf = turn.get("avatar_conf", {}) or {}
    # combine global and per-avatar config (per-avatar overrides)
    mode = conf.get("mode") or global_opts.get("mode", "fullbody")
    gender = conf.get("gender") or global_opts.get("gender", "any")
    emotion = conf.get("emotion") or global_opts.get("emotion", "neutral")
    outfit = conf.get("outfit") or global_opts.get("outfit", None)
    face = conf.get("face") or global_opts.get("face", None)

    # generate avatar video clip for this single line
    # generate_talking_avatar returns a filepath to mp4 (existing function)
    avatar_video_path = generate_talking_avatar(
        turn["text"],
        gender=gender,
        emotion=emotion,
        user_face=face,
        mode=mode,
        outfit=outfit,
        apply_template=False
    )

    clip = VideoFileClip(avatar_video_path)
    # trim or pad to fit desired seconds (optional)
    desired_dur = global_opts.get("per_line_duration", None)
    if desired_dur:
        if clip.duration > desired_dur:
            clip = clip.subclip(0, desired_dur)
        elif clip.duration < desired_dur:
            last = clip.to_ImageClip(clip.get_frame(clip.duration - 0.01)).set_duration(desired_dur - clip.duration)
            clip = concatenate_videoclips([clip, last])
    # add subtitle
    if global_opts.get("subtitles", True):
        sub = build_subtitle_clip(turn["text"], clip.duration, width=min(900, clip.w))
        if sub:
            clip = CompositeVideoClip([clip, sub.set_start(0)])
    return clip

def _compose_conversation_clips(clips, style="cut", transition="crossfade", bg=None, music_path=None):
    """
    clips: list of moviepy clips in turn order
    style: 'cut' (simple sequential), 'side_by_side' (2 avatars on screen), 'picture_in_picture'
    returns final moviepy clip
    """
    # if side_by_side, group consecutive pairs where speakers differ
    if style == "side_by_side":
        composed = []
        i = 0
        while i < len(clips):
            if i+1 < len(clips):
                left = clips[i].resize(width=540)
                right = clips[i+1].resize(width=540)
                # position left and right
                h = max(left.h, right.h)
                bg_clip = None
                # create canvas (1080x1920 or use max)
                W = 1080; H = max(1920, h)
                left = left.set_position(("left","center"))
                right = right.set_position(("right","center"))
                comp = CompositeVideoClip([left, right], size=(W, H)).set_duration(max(left.duration, right.duration))
                composed.append(comp)
                i += 2
            else:
                # last lone clip, center it
                single = clips[i].resize(width=900)
                W = 1080; H = max(1920, single.h)
                comp = CompositeVideoClip([single.set_position(("center","center"))], size=(W,H)).set_duration(single.duration)
                composed.append(comp)
                i += 1
        timeline = concatenate_videoclips(composed, method="compose")
    else:
        # default cut or sequential with simple transitions
        timeline = clips[0]
        for idx in range(1, len(clips)):
            nxt = clips[idx]
            if transition == "crossfade":
                timeline = concatenate_videoclips([timeline, nxt], method="compose")
            else:
                timeline = concatenate_videoclips([timeline, nxt], method="compose")

    # background music overlay (if provided)
    if music_path:
        music = AudioFileClip(music_path).volumex(0.25).set_duration(timeline.duration)
        # mix timeline's audio (voice) with music
        # MoviePy: set composite audio - here prefer to keep voices (timeline.audio) and set music under it
        try:
            main_audio = timeline.audio
            final_audio = main_audio.volumex(1.0).fx(vfx.audio_fadein,0.1)
            # create combined by mixing using MoviePy's CompositeAudioClip would be ideal; fallback: use music as audio for clip
            timeline = timeline.set_audio(music)
        except Exception:
            timeline = timeline.set_audio(music)
    # optional background video
    if bg:
        try:
            bg_clip = VideoFileClip(bg).resize(timeline.size).subclip(0, timeline.duration)
            final = CompositeVideoClip([bg_clip, timeline.set_position(("center","center"))])
            return final.set_duration(timeline.duration)
        except Exception:
            return timeline
    return timeline

def generate_multi_avatar_conversation(script_text, avatars, global_opts=None, output_name=None, style="cut"):
    """
    Main entry.
    - script_text: text with lines like "A: Hello\nB: Hi\nA: How are you?"
    - avatars: dict, keys = "A","B",... values = {face: path_or_None, gender, outfit, emotion, mode}
      Example:
        avatars = {
          "A": {"face":"static/uploads/a.png","gender":"male","mode":"fullbody"},
          "B": {"face":"static/uploads/b.png","gender":"female","mode":"head"}
        }
    - global_opts: {"per_line_duration":N, "subtitles":True, "bg":None, "music":True, "music_style":"cinematic"}
    - style: 'cut'|'side_by_side'|'picture_in_picture'
    Returns path to final mp4
    """
    global_opts = global_opts or {}
    turns = _parse_conversation(script_text, avatars)
    clips = []
    for turn in turns:
        clip = _render_line_to_clip(turn, global_opts)
        clips.append(clip)

    # music generation (if requested)
    music_path = None
    if global_opts.get("music", True):
        total_dur = sum([c.duration for c in clips])
        music_path = render_music(duration=int(math.ceil(total_dur)), bpm=90, style=global_opts.get("music_style","cinematic"))

    final_clip = _compose_conversation_clips(clips, style=style, transition=global_opts.get("transition","crossfade"), bg=global_opts.get("bg"), music_path=music_path)

    out_name = output_name or f"{BASE_STATIC}/conversation_{uuid.uuid4().hex[:8]}.mp4"
    finalize_and_export(final_clip, out_name, fps=24)
    return out_name
