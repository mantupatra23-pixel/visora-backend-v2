# engine/lipsync_engine.py
"""
Wav2Lip wrapper:
- Expects: wav_path (16k mono), reference face/video or use a static avatar.
- Returns path to generated lip-synced video (mp4).
- Requires wav2lip model at wav2lip/checkpoints/wav2lip_gan.pth
"""

import os
import logging
import uuid
import subprocess
from pathlib import Path

LOG = logging.getLogger("visora.lipsync")
LOG.setLevel(logging.INFO)

WAV2LIP_PTH = Path("wav2lip/checkpoints/wav2lip_gan.pth")
TMP_DIR = Path("static/uploads")
TMP_DIR.mkdir(parents=True, exist_ok=True)

def ensure_model_exists():
    if not WAV2LIP_PTH.exists():
        raise FileNotFoundError(f"Wav2Lip model not found at {WAV2LIP_PTH}. Place wav2lip_gan.pth there.")

def _run_cmd(cmd):
    LOG.debug("Run cmd: %s", cmd)
    proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        LOG.error("Cmd failed: %s\nstderr:%s", cmd, proc.stderr.decode())
        raise RuntimeError(proc.stderr.decode())
    return proc.stdout

def lipsync_with_wav2lip(wav_path: str, face_video: str=None) -> str:
    """
    Run Wav2Lip inference.
    - wav_path: path to wav
    - face_video: video/image used as reference (optional). If None, use default file `assets/face.mp4` if exists.
    """
    ensure_model_exists()
    face_video = face_video or "assets/face.mp4"
    if not Path(face_video).exists():
        # if no face video present, use a fallback (error out or use test.mp4)
        LOG.warning("Face video not found (%s). Using test.mp4 as fallback.", face_video)
        face_video = "test.mp4"

    out_file = TMP_DIR / f"{uuid.uuid4()}.mp4"
    # Call wav2lip inference script (assumed installed with proper entrypoint)
    # Replace the command below according to your wav2lip repo structure.
    cmd = (
        f"python wav2lip/inference.py --checkpoint_path {WAV2LIP_PTH} "
        f"--face {face_video} --audio {wav_path} --outfile {out_file}"
    )
    _run_cmd(cmd)
    LOG.info("Lipsync done: %s", out_file)
    return str(out_file)
