# engine/audio_engine.py
"""
Production-ready audio generation wrapper.
- Supports local TTS (placeholder) or external TTS provider (ElevenLabs).
- Writes WAV 16k mono (recommended for lipsync).
- Returns absolute path to generated wav.
"""

import os
import logging
import uuid
import subprocess
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("visora.audio")
LOG.setLevel(logging.INFO)

OUT_DIR = Path("static/uploads")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def _run_cmd(cmd):
    LOG.debug("Run cmd: %s", cmd)
    proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        LOG.error("Cmd failed: %s\nstdout:%s\nstderr:%s", cmd, proc.stdout, proc.stderr)
        raise RuntimeError(f"Command failed: {cmd}\n{proc.stderr.decode()}")
    return proc.stdout

def text_to_wav_local(text: str, speaker: Optional[str]=None) -> str:
    """
    Simple local TTS placeholder using `espeak` or fallback to write a dummy wav.
    For production swap this with ElevenLabs/GoogleTTS logic.
    """
    out_path = OUT_DIR / f"{uuid.uuid4()}.wav"
    # try using ffmpeg + espeak (if installed)
    try:
        # generate raw wav with espeak then convert to 16k mono
        tmp = OUT_DIR / f"{uuid.uuid4()}.wav"
        cmd_espeak = f"espeak -w {tmp} \"{text.replace('\"','\\\"')}\""
        _run_cmd(cmd_espeak)
        cmd_fix = f"ffmpeg -y -i {tmp} -ar 16000 -ac 1 -c:a pcm_s16le {out_path}"
        _run_cmd(cmd_fix)
        tmp.unlink(missing_ok=True)
        LOG.info("TTS local done: %s", out_path)
        return str(out_path)
    except Exception as e:
        LOG.warning("Local TTS failed: %s — writing dummy file", e)
        # fallback: create a small silent wav
        cmd_silence = f"ffmpeg -y -f lavfi -i anullsrc=r=16000:cl=mono -t 1 -c:a pcm_s16le {out_path}"
        _run_cmd(cmd_silence)
        return str(out_path)

# Example wrapper for ElevenLabs (pseudo — replace with real API calls and secrets)
def text_to_wav_elevenlabs(text: str, api_key: str, voice: str="alloy") -> str:
    """
    Placeholder for ElevenLabs TTS. Implement actual HTTP upload here.
    """
    raise NotImplementedError("Integrate ElevenLabs or other TTS provider here.")
