# engine/avatar/avatar_engine.py
"""
Visora Avatar Engine - Multilanguage TTS + Avatar Video Orchestration
- Auto-detect language and translate (TextBlob fallback)
- Use gTTS for TTS (many languages supported)
- Optional voice clone path (if voice sample exists)
- Compose avatar video via existing modules:
    generate_face, generate_fullbody, generate_motion_avatar,
    mix_avatar_with_template, clone_voice_and_generate, generate_reel, apply_ai_religh...
- Robust fallbacks and logging.
Author: Aimantuvya + GPT-5 Thinking mini
"""

import os
import random
import shutil
import uuid
import logging
from typing import Tuple, Optional

# TTS and translation libs
from gtts import gTTS

# TextBlob for detection/translation fallback
try:
    from textblob import TextBlob
except Exception:
    TextBlob = None

# pydub for simple audio handling if needed
try:
    from pydub import AudioSegment
except Exception:
    AudioSegment = None

# existing engine imports (from your repo)
# make sure these modules exist in engine/
from engine.facegen.face_generator import generate_face
from engine.fullbody.fullbody_engine import generate_fullbody_avatar
from engine.avatar_motion_engine import generate_motion_avatar
from engine.mixer.template_mixer import mix_avatar_with_template
from engine.voiceclone.clone_engine import clone_voice_and_generate
from engine.reel.reel_engine import generate_reel
from engine.lighting.lighting_engine import apply_ai_relight
from engine.outfit.outfit_engine import apply_outfit_change
from engine.language.translator import auto_detect_and_translate  # if you have native
from engine.language.tts_engine import generate_tts as project_generate_tts   # optional project TTS
from engine.threeD.threeD_avatar import generate_3d_from_face, stylize_3d_texture, generate_3d_talking_avatar
from engine.motion.bg_replace_pipeline import replace_background_with_tracking
# NOTE: If above project modules don't exist exactly, replace these imports with your module names.

# local logger
log = logging.getLogger("avatar_engine")
logging.basicConfig(level=logging.INFO)

# ensure static folders exist
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))  # project root
STATIC_DIR = os.path.join(ROOT_DIR, "static")
VIDEO_DIR = os.path.join(STATIC_DIR, "videos")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")
os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Supported gTTS language codes reference simple subset
# gTTS supports many languages. We'll try to use detected language code directly.
# If not supported, fallback to 'en'.
GTTS_SUPPORTED = {
    # common codes (not exhaustive)
    "en", "hi", "bn", "mr", "gu", "pa", "ta", "te", "kn", "ml", "or", "ur",
    "ar", "es", "fr", "de", "pt", "ru", "ja", "ko", "zh-cn", "zh-tw", "vi",
    # add more if needed
}

def safe_filename(prefix="voice", ext="mp3"):
    rnd = random.randint(1000, 9999)
    name = f"{prefix}_{uuid.uuid4().hex[:8]}_{rnd}.{ext}"
    return os.path.join(VIDEO_DIR, name)

# -------- translation / language detection (fallback) -------- #
def detect_language_textblob(text: str) -> Tuple[str, str]:
    """
    Use TextBlob to detect and optionally translate.
    Returns (translated_text, lang_code)
    """
    if TextBlob is None:
        raise RuntimeError("TextBlob not available")
    blob = TextBlob(text)
    try:
        lang = blob.detect_language()
    except Exception:
        lang = "en"
    # keep original text; translation if needed will be done by higher layer
    return text, lang

def auto_detect_and_translate_fallback(text: str, target_lang="auto") -> Tuple[str, str]:
    """
    Wrapper: tries project-level auto_detect_and_translate if available
    else uses TextBlob fallback.
    Returns (translated_text, detected_lang)
    """
    try:
        # If you have your own translator function in engine.language.translator
        if 'auto_detect_and_translate' in globals() and callable(auto_detect_and_translate):
            # prefer project's more advanced translator
            translated_text, detected_lang = auto_detect_and_translate(text, target_lang)
            return translated_text, detected_lang
    except Exception as ex:
        log.info("Project translator failed, falling back: %s", ex)

    # TextBlob fallback
    try:
        t, lang = detect_language_textblob(text)
        # if target_lang == 'auto', we return same text and detected language
        return t, lang
    except Exception as e:
        log.warning("TextBlob detect failed: %s - default to en", e)
        return text, "en"

# -------- TTS generation (gTTS fallback) -------- #
def generate_tts_file(text: str, lang_code: str = "en") -> str:
    """
    Generate a TTS file using either project_generate_tts (if exists and supported)
    or gTTS fallback. Returns audio file path.
    """
    # use project's TTS engine if available and returns path
    try:
        if 'project_generate_tts' in globals() and callable(project_generate_tts):
            path = project_generate_tts(text, lang_code)
            if path and os.path.exists(path):
                log.info("Used project TTS engine: %s", path)
                return path
    except Exception as e:
        log.info("Project TTS engine failed: %s", e)

    # fallback: gTTS
    # gTTS expects language codes like 'hi', 'en', 'es', etc.
    gtts_lang = lang_code
    # canonicalize zh variants
    if lang_code.lower() in ("zh", "zh-cn", "zh_cn", "zh-hans"):
        gtts_lang = "zh-cn"
    if lang_code.lower() in ("zh-tw", "zh_tw", "zh-hant"):
        gtts_lang = "zh-tw"

    if gtts_lang not in GTTS_SUPPORTED:
        # try major language family mapping or fallback to english
        if gtts_lang.startswith("en"):
            gtts_lang = "en"
        elif gtts_lang.startswith("hi"):
            gtts_lang = "hi"
        else:
            gtts_lang = "en"

    out_path = safe_filename(prefix="voice", ext="mp3")
    try:
        tts = gTTS(text, lang=gtts_lang)
        tts.save(out_path)
        log.info("gTTS saved to %s (lang=%s)", out_path, gtts_lang)
        # Optionally normalise sample rate or convert - using pydub if available
        return out_path
    except Exception as e:
        log.exception("gTTS failed: %s", e)
        raise

# -------- voice clone helper (uses your project's clone function if available) -------- #
def create_or_clone_voice(script_text: str, voice_sample_path: Optional[str]) -> str:
    """
    If voice_sample_path exists, try to clone/convert voice using project's clone engine.
    Else generate TTS file for script_text.
    Returns audio_path.
    """
    # 1) If voice sample provided and clone engine exists
    if voice_sample_path and os.path.exists(voice_sample_path):
        try:
            # clone_voice_and_generate expected to return path
            cloned_path = clone_voice_and_generate(script_text, voice_sample_path)
            if cloned_path and os.path.exists(cloned_path):
                log.info("Voice clone used: %s", cloned_path)
                return cloned_path
        except Exception as ex:
            log.warning("Voice clone failed: %s", ex)
            # fallback to TTS

    # 2) No clone or clone failed -> generate TTS
    # Auto detect language and generate
    translated_text, detected_lang = auto_detect_and_translate_fallback(script_text, target_lang="auto")
    try:
        audio_path = generate_tts_file(translated_text, detected_lang)
        return audio_path
    except Exception as e:
        log.warning("Primary TTS generation failed, fallback english gTTS: %s", e)
        # final fallback english
        return generate_tts_file(script_text, "en")

# -------- main orchestration function -------- #
def generate_talking_avatar(
    script_text: str,
    gender: str = "any",
    emotion: str = "normal",
    user_face: Optional[str] = None,
    mode: str = "fullbody",
    apply_template: bool = False,
    bg_template: Optional[str] = None,
    voice_sample: Optional[str] = None,
    outfit: Optional[str] = None,
) -> str:
    """
    High-level function to generate a talking avatar video.
    Returns final output video path (mp4).
    """

    # 1) Generate or use face
    if user_face and os.path.exists(user_face):
        face_img = user_face
        log.info("Using user-supplied face: %s", user_face)
    else:
        face_img = generate_face(gender=gender)
        log.info("Generated face image: %s", face_img)

    # 1.5) Outfit change (optional)
    if outfit:
        try:
            face_img = apply_outfit_change(face_img, outfit)
            log.info("Applied outfit: %s", outfit)
        except Exception as ex:
            log.warning("Outfit change failed: %s", ex)

    # 2) Generate or clone voice
    audio_path = create_or_clone_voice(script_text, voice_sample)

    # 3) Generate avatar video (fullbody or motion)
    if mode == "fullbody":
        try:
            avatar_video = generate_fullbody_avatar(face_img, audio_path, emotion=emotion)
        except Exception as ex:
            log.warning("Fullbody generation failed, trying motion avatar: %s", ex)
            avatar_video = generate_motion_avatar(face_img, audio_path, emotion=emotion)
    else:
        avatar_video = generate_motion_avatar(face_img, audio_path, emotion=emotion)

    log.info("Avatar video created: %s", avatar_video)

    # 4) Optional template mixing (background / cinematic)
    final_video = avatar_video
    if apply_template and bg_template:
        try:
            final_video = mix_avatar_with_template(avatar_video, bg_template, script_text)
            log.info("Mixed avatar with template: %s", final_video)
        except Exception as ex:
            log.warning("Template mixing failed: %s", ex)
            final_video = avatar_video  # fallback

    # 4.1) AI relight (optional, keep as improvement)
    try:
        final_video = apply_ai_relight(final_video)
    except Exception:
        # If apply_ai_relight not available or fails -> ignore
        pass

    # 5) Auto reel editor (if mode == reel)
    if mode == "reel":
        try:
            final_video = generate_reel(final_video, script_text)
            log.info("Reel generated: %s", final_video)
        except Exception as ex:
            log.warning("Reel generation failed: %s", ex)

    # 6) Final output path normalization (ensure .mp4)
    if not final_video.lower().endswith(".mp4"):
        out_path = final_video + ".mp4"
    else:
        out_path = final_video

    # If final_video exists and the out_path differs, try to copy/rename
    try:
        if final_video != out_path and os.path.exists(final_video):
            shutil.copyfile(final_video, out_path)
    except Exception:
        # ignore copy errors, assume generation already saved to out_path
        pass

    log.info("Final output path: %s", out_path)
    return out_path

    if mode == "3d":
    # 1) Create 3D mesh
    mesh = generate_3d_from_face(face_img)

    # 2) Stylize texture
    tex = stylize_3d_texture(face_img, style)

    # 3) Lipsync with audio
    final_video = generate_3d_talking_avatar(mesh, tex, audio_path)

    return final_video

    if mode == "bg_track":
    bg_input = "engine/templates/cinematic_bg/1.mp4"  # or AI background
    final_video = replace_background_with_tracking(avatar_video, bg_input)
    return final_video

# -------------------------
# Small CLI/test helper
# -------------------------
if __name__ == "__main__":
    # small demo when running file directly
    demo_script = "Hello! This is a demo from Visora engine. किस भाषा में बोलना है, बताएँ।"
    print("Generating demo avatar video (this may take long)...")
    try:
        demo_out = generate_talking_avatar(
            demo_script,
            gender="any",
            emotion="neutral",
            user_face=None,
            mode="fullbody",
            apply_template=False,
            bg_template=None,
            voice_sample=None,
            outfit=None,
        )
        print("Demo finished. Output:", demo_out)
    except Exception as e:
        print("Demo failed:", e)
