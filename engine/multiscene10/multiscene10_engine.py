# engine/multiscene10/multiscene10_engine.py
import os, uuid, random, tempfile, math
from moviepy.editor import VideoFileClip, CompositeVideoClip, concatenate_videoclips, AudioFileClip, vfx
from .scenes_utils import smart_split_script, build_subtitle_clip, finalize_and_export, apply_transition, PRESETS_PATH
import json

# Imports from other engines (assumes present)
# generate_talking_avatar(script_text,...mode,...) returns path to mp4 for a single scene
from engine.avatar.avatar_engine import generate_talking_avatar
from engine.mixer.template_mixer import mix_avatar_with_template
# optional effects (if present)
from engine.camera.smooth_camera import smooth_pan, smooth_zoom
from engine.camera.angle_engine import apply_angle
from engine.camera.particles_engine import overlay_particles
from engine.camera.lensfx_engine import apply_lens_fx
from engine.audio.music_sfx_engine import render_music, generate_sfx, mix_tracks

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "presets.json"), "r") as f:
    PRESETS = json.load(f)

def _get_template_for_index(i):
    # cycle presets
    tpl = PRESETS["default_scene_templates"][i % len(PRESETS["default_scene_templates"])]
    return tpl

def _make_scene(scene_text, user_face, scene_idx, options):
    """
    Produce a single scene clip path.
    options dict may include: mode, gender, emotion, outfit, template_mix (bool), camera, angle, particles, lensfx, duration
    """
    # 1) generate avatar video for this scene using existing engine
    mode = options.get("mode", "fullbody")
    gender = options.get("gender", "any")
    emotion = options.get("emotion", "neutral")
    outfit = options.get("outfit", None)
    template_mix = options.get("template_mix", False)

    # call avatar generator (this will produce a clip path)
    avatar_clip_path = generate_talking_avatar(scene_text, gender=gender, emotion=emotion, user_face=user_face, mode=mode, apply_template=False, outfit=outfit)

    # 2) If template/AI background requested, mix
    tpl = _get_template_for_index(scene_idx)
    bg = options.get("bg_override", tpl.get("bg"))
    if options.get("template_mix", False) and bg:
        mixed_path = mix_avatar_with_template(avatar_clip_path, bg, scene_text)
        avatar_clip_path = mixed_path

    # 3) Load with MoviePy
    clip = VideoFileClip(avatar_clip_path)
    # if user specified duration override, trim or extend via freeze
    duration = options.get("duration", tpl.get("duration", clip.duration))
    if clip.duration > duration:
        clip = clip.subclip(0, duration)
    elif clip.duration < duration:
        # pad last frame
        last_frame = clip.to_ImageClip(clip.get_frame(clip.duration - 0.01)).set_duration(duration - clip.duration)
        clip = concatenate_videoclips([clip, last_frame])

    # 4) Camera motion / angle
    cam = options.get("camera", tpl.get("camera"))
    ang = options.get("angle", tpl.get("angle"))
    if cam:
        if cam.startswith("pan") or cam.startswith("smooth"):
            clip = smooth_pan(clip, direction="left" if "left" in cam else "right", strength=40)
        if cam.startswith("zoom"):
            clip = smooth_zoom(clip, zoom_amount=1.06)
    if ang:
        clip = apply_angle(clip, ang)

    # 5) Particles
    particles = options.get("particles", None)
    if particles and particles != "off":
        clip_path_tmp = f"/tmp/scene_particle_{uuid.uuid4().hex[:8]}.mp4"
        clip.write_videofile(clip_path_tmp, codec="libx264", audio_codec="aac", fps=24)
        clip = VideoFileClip(overlay_particles(clip_path_tmp, kind=particles, density=80))

    # 6) Lens FX
    if options.get("lensfx", True):
        # apply later on final composition or here (here)
        clip_path_tmp = f"/tmp/scene_lens_{uuid.uuid4().hex[:8]}.mp4"
        clip.write_videofile(clip_path_tmp, codec="libx264", audio_codec="aac", fps=24)
        clip = VideoFileClip(apply_lens_fx(clip_path_tmp))

    # 7) Subtitles
    subtitle = build_subtitle_clip(scene_text, duration)
    if subtitle:
        clip = CompositeVideoClip([clip, subtitle.set_start(0)])

    # return clip
    return clip

def generate_10_scene_movie(script_text, user_face=None, options_all=None, max_scenes=10, output_aspect="portrait"):
    """
    Main function:
    - script_text: long script or with [--scene--] markers
    - user_face: path to face image (or None)
    - options_all: dict with per-scene overrides, e.g. {"scene_0": {"mode":"3d","camera":"zoom-in"}, ...}
    Returns path to final movie file.
    """
    # 1) split script
    scenes = smart_split_script(script_text, max_scenes)
    n = len(scenes)
    clips = []
    # default options_all to {}
    options_all = options_all or {}

    for i, sc_text in enumerate(scenes):
        # fetch per-scene options
        opts = options_all.get(f"scene_{i}", {})
        # merge general options
        merged_opts = {}
        merged_opts.update(options_all.get("global", {}))
        merged_opts.update(opts)
        clip = _make_scene(sc_text, user_face, i, merged_opts)
        clips.append(clip)

    # 2) apply transitions between clips according to presets or options
    final_timeline = []
    for i in range(len(clips)):
        if i == 0:
            final_timeline.append(clips[i])
        else:
            prev = final_timeline.pop()
            transition = options_all.get("transitions", {}).get(f"{i-1}_{i}", None)
            if not transition:
                # use preset sequence
                transition = PRESETS["default_scene_templates"][(i-1) % len(PRESETS["default_scene_templates"])].get("transition", "crossfade")
            combined = apply_transition(prev, clips[i], transition_type=transition, duration=0.6)
            final_timeline.append(combined)

    # 3) compose final clip via concatenate
    # final_timeline will be a list length 1 with concatenated items if transitions applied in combine
    final_clip = concatenate_videoclips(final_timeline, method="compose") if len(final_timeline)>1 else final_timeline[0]

    # 4) background music: generate and mix with existing audio track
    # generate music with similar duration
    total_dur = final_clip.duration
    music_path = render_music(duration=int(math.ceil(total_dur)), bpm=90, style="cinematic")
    # mix music and final audio (final_clip.audio is main)
    # write temp final clip audio to file
    tmp_video_path = f"/tmp/movie_tmp_{uuid.uuid4().hex[:8]}.mp4"
    final_clip.write_videofile(tmp_video_path, fps=24, codec="libx264", audio_codec="aac")
    # now combine using pydub (mix_tracks) or moviepy
    # simple approach: set background music as audio of the clip (overlay)
    audio_music = AudioFileClip(music_path).volumex(0.25)
    voice_audio = AudioFileClip(tmp_video_path).subclip(0, total_dur)
    # mix: overlay voice on top of music
    # moviepy mixing:
    final_audio = audio_music.set_duration(total_dur).volumex(0.6).audio_fadein(0.3)
    # set final composite audio to video
    final_clip = final_clip.set_audio(final_audio)
    # export
    out_id = uuid.uuid4().hex[:8]
    out_name = f"static/videos/movie_{out_id}.mp4"
    finalize_and_export(final_clip, out_name, fps=24)
    return out_name
