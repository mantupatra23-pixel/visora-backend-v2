# engine/render_engine.py
"""
High-level render orchestrator.
- Given script_text + optional assets, will:
  1) generate audio (audio_engine)
  2) run lipsync (lipsync_engine)
  3) merge and finalise (merge_engine)
- Returns final video path and metadata.
"""

import logging
from pathlib import Path
import uuid

from engine.audio_engine import text_to_wav_local
from engine.lipsync_engine import lipsync_with_wav2lip
from engine.merge_engine import merge_final

LOG = logging.getLogger("visora.render")
LOG.setLevel(logging.INFO)

def render_from_script(script_text: str, face_video: str=None) -> dict:
    """
    Orchestrates full pipeline. Returns dict with final path and metadata.
    """
    LOG.info("Render requested for script (len=%d)", len(script_text))
    # Step 1: TTS -> wav
    wav_path = text_to_wav_local(script_text)
    LOG.info("Wav generated: %s", wav_path)

    # Step 2: Lipsync -> lip video
    lip_video = lipsync_with_wav2lip(wav_path, face_video=face_video)
    LOG.info("Lip video: %s", lip_video)

    # Step 3: Merge/finalise
    final = merge_final(lip_video)
    LOG.info("Final video: %s", final)

    # metadata
    meta = {
        "final_path": final,
        "duration_seconds": None,   # optionally fill using ffprobe
        "script_text": script_text[:2000],
        "id": str(uuid.uuid4())
    }
    return meta
