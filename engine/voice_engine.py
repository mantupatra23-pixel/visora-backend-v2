# engine/voice_engine.py
"""
Voice engine with ElevenLabs (preferred) + Coqui (local) + gTTS fallback.
Usage:
    from engine.voice_engine import render_voice_for_character
    wav = render_voice_for_character({
        "name":"Anita","gender":"female","age":"adult","dialogue":"Hello"
    }, "/tmp/audio")
Environment:
    - Set ELEVEN_API_KEY in env to enable ElevenLabs
Dependencies to have in requirements.txt:
    requests
    pydub
    gTTS
    TTS (Coqui) optional
    (pydub needs ffmpeg installed on the server)
"""

import os
import uuid
import logging
from pathlib import Path
import json
import time

log = logging.getLogger("voice_engine")
log.setLevel(logging.INFO)

# Try EleventLabs via REST (no SDK required)
import requests

# Try Coqui
try:
    from TTS.api import TTS  # optional - coqui
    COQUI_AVAILABLE = True
except Exception:
    COQUI_AVAILABLE = False

# Try gTTS fallback
try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except Exception:
    GTTS_AVAILABLE = False

from pydub import AudioSegment

ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY", "").strip()
ELEVEN_BASE = "https://api.elevenlabs.io/v1"

# Voice preset mapping: (gender, age_group) -> dict of preferred backends (eleven voice id etc.)
# Replace the "eleven_voice_id" values with actual voice IDs you create in ElevenLabs.
VOICE_PRESETS = {
    ("female", "adult"): {
        "eleven_voice_id": "21m00Tcm4TlvDq8ikWAM",  # placeholder - replace
        "coqui": "tts_models/en/vctk/vits",
        "label": "female_adult"
    },
    ("male", "adult"): {
        "eleven_voice_id": "AZnzlk1XvdvUeBnXmlld",  # placeholder - replace
        "coqui": "tts_models/en/ljspeech/tacotron2-DDC",
        "label": "male_adult"
    },
    ("female", "child"): {
        "eleven_voice_id": "EXAMPLE_CHILD_FEMALE",  # replace with your child voice id if any
        "coqui": "tts_models/en/vctk/vits",
        "label": "female_child"
    },
    ("male", "child"): {
        "eleven_voice_id": "EXAMPLE_CHILD_MALE",  # replace
        "coqui": "tts_models/en/vctk/vits",
        "label": "male_child"
    },
    ("male", "old"): {
        "eleven_voice_id": "EXAMPLE_OLD_MALE",  # replace
        "coqui": "tts_models/en/ljspeech/tacotron2-DDC",
        "label": "male_old"
    },
    ("female", "old"): {
        "eleven_voice_id": "EXAMPLE_OLD_FEMALE",  # replace
        "coqui": "tts_models/en/vctk/vits",
        "label": "female_old"
    },
    ("neutral", "adult"): {
        "eleven_voice_id": None,
        "coqui": "tts_models/en/vctk/vits",
        "label": "neutral_adult"
    },
}

# Cache coqui models
TTS_CACHE = {}

def normalize_gender_age(gender: str, age: str):
    g = (gender or "neutral").lower()
    a = (age or "adult").lower()
    if a in ("kid", "child", "children", "young"):
        a = "child"
    if a in ("old", "elder", "elderly"):
        a = "old"
    if g not in ("male", "female", "neutral"):
        g = "neutral"
    return g, a

def select_voice_preset(gender: str, age: str):
    g, a = normalize_gender_age(gender, age)
    key = (g, a)
    if key in VOICE_PRESETS:
        return VOICE_PRESETS[key]
    # fallback adult same gender
    return VOICE_PRESETS.get((g, "adult")) or VOICE_PRESETS.get(("neutral", "adult"))

# ------------------------------
# ElevenLabs synth function
# ------------------------------
def eleven_synthesize_to_wav(text: str, voice_id: str, out_wav_path: str, stability: float = 0.5, similarity_boost: float = 0.75):
    """
    Use ElevenLabs TTS REST API v1 to synthesize and save wav.
    Requires ELEVEN_API_KEY in env.
    Returns out_wav_path on success, else raise Exception.
    """
    if not ELEVEN_API_KEY:
        raise RuntimeError("ELEVEN_API_KEY not set")

    # endpoint: /text-to-speech/{voice_id}
    url = f"{ELEVEN_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "voice_settings": {
            "stability": float(stability),
            "similarity_boost": float(similarity_boost)
        }
    }
    # make request (ElevenLabs returns audio/wav)
    resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"ElevenLabs TTS failed: {resp.status_code} {resp.text}")

    # write stream to file
    Path(out_wav_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_wav_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    # Ensure file is valid (pydub load check)
    try:
        _ = AudioSegment.from_file(out_wav_path)
    except Exception as e:
        log.warning("ElevenLabs produced invalid audio: %s", e)
        # delete invalid file
        try:
            os.remove(out_wav_path)
        except Exception:
            pass
        raise
    return out_wav_path

# ------------------------------
# Coqui synth function
# ------------------------------
def _init_coqui_model(model_name: str):
    if model_name in TTS_CACHE:
        return TTS_CACHE[model_name]
    if not COQUI_AVAILABLE:
        return None
    try:
        tts = TTS(model_name)
        TTS_CACHE[model_name] = tts
        return tts
    except Exception as e:
        log.warning("Coqui model load failed: %s -> %s", model_name, e)
        return None

def coqui_synthesize_to_wav(text: str, model_name: str, out_wav_path: str):
    Path(out_wav_path).parent.mkdir(parents=True, exist_ok=True)
    tts = _init_coqui_model(model_name)
    if not tts:
        raise RuntimeError("Coqui unavailable or model failed to load")
    # coqui: tts_to_file
    try:
        tts.tts_to_file(text=text, file_path=out_wav_path)
        return out_wav_path
    except Exception as e:
        log.exception("Coqui synth error: %s", e)
        raise

# ------------------------------
# gTTS fallback
# ------------------------------
def gtts_synthesize_to_wav(text: str, out_wav_path: str, sample_rate=22050):
    Path(out_wav_path).parent.mkdir(parents=True, exist_ok=True)
    if not GTTS_AVAILABLE:
        raise RuntimeError("gTTS not available")
    tmp_mp3 = out_wav_path + ".mp3"
    try:
        tts = gTTS(text=text, lang="en")
        tts.save(tmp_mp3)
        audio = AudioSegment.from_file(tmp_mp3, format="mp3")
        audio = audio.set_frame_rate(sample_rate).set_channels(1)
        audio.export(out_wav_path, format="wav")
        os.remove(tmp_mp3)
        return out_wav_path
    except Exception as e:
        log.exception("gTTS synth failed: %s", e)
        if os.path.exists(tmp_mp3):
            try: os.remove(tmp_mp3)
            except: pass
        raise

# ------------------------------
# Master synth wrapper
# ------------------------------
def synthesize_text_to_wav(text: str, preset: dict, out_wav_path: str):
    """
    Try eleven -> coqui -> gtts -> silent.
    """
    Path(out_wav_path).parent.mkdir(parents=True, exist_ok=True)
    # 1) ElevenLabs
    eleven_voice = preset.get("eleven_voice_id")
    if ELEVEN_API_KEY and eleven_voice:
        try:
            return eleven_synthesize_to_wav(text, eleven_voice, out_wav_path)
        except Exception as e:
            log.warning("ElevenLabs synth failed: %s (falling back)", e)

    # 2) Coqui
    coqui_model = preset.get("coqui")
    if COQUI_AVAILABLE and coqui_model:
        try:
            return coqui_synthesize_to_wav(text, coqui_model, out_wav_path)
        except Exception as e:
            log.warning("Coqui synth failed: %s (falling back)", e)

    # 3) gTTS fallback
    if GTTS_AVAILABLE:
        try:
            return gtts_synthesize_to_wav(text, out_wav_path)
        except Exception as e:
            log.warning("gTTS synth failed: %s (falling back)", e)

    # 4) silent fallback
    try:
        silence = AudioSegment.silent(duration=500)  # 0.5s silent
        silence.export(out_wav_path, format="wav")
        return out_wav_path
    except Exception as e:
        log.exception("Failed to create silent wav fallback: %s", e)
        raise RuntimeError("No TTS available and cannot create fallback audio")

# ------------------------------
# Convenience render function
# ------------------------------
def render_voice_for_character(character_meta: dict, out_dir: str):
    """
    character_meta:
      {
        "name": "Anita",
        "gender": "female",
        "age": "adult",
        "dialogue": "Hello world"
      }
    returns absolute path to wav file
    """
    name = character_meta.get("name", "char")
    dialogue = character_meta.get("dialogue", "")
    gender = character_meta.get("gender", "neutral")
    age = character_meta.get("age", "adult")

    preset = select_voice_preset(gender, age)
    uid = uuid.uuid4().hex[:8]
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_").lower() or "char"
    out_wav = os.path.join(out_dir, f"{safe_name}_{uid}.wav")

    # If long text, chunk it? (basic version: synth whole text)
    return synthesize_text_to_wav(dialogue, preset, out_wav)
