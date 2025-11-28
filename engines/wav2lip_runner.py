# engines/wav2lip_runner.py
"""
Wav2Lip runner wrapper with checkpoint auto-download helper.

Usage:
  python engines/wav2lip_runner.py --face face.mp4 --audio speech.wav --out out.mp4

Env / config:
  WAV2LIP_CHECKPOINT  -> path to checkpoint file (if not exists, auto-download will try)
  WAV2LIP_CHECK_URL   -> optional direct URL to download checkpoint (prefered)
  WAV2LIP_MODEL_DIR   -> directory to store checkpoints (default: /opt/wav2lip/checkpoints)
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import shutil

DEFAULT_MODEL_DIR = Path(os.environ.get("WAV2LIP_MODEL_DIR", "/opt/wav2lip/checkpoints"))
DEFAULT_CHECK_URL = os.environ.get("WAV2LIP_CHECK_URL", "").strip()

# Known public URL fallback (example). Replace if you have a preferred host.
PUBLIC_CHECKPOINT_URL = DEFAULT_CHECK_URL or "https://github.com/Rudrabha/Wav2Lip/releases/download/v0.1/wav2lip_gan.pth"

def ensure_checkpoint(path: Path):
    if path.exists():
        print("Checkpoint found:", path)
        return True
    print("Checkpoint not found at", path)
    DEFAULT_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dest = path
    print("Attempting to download checkpoint from:", PUBLIC_CHECKPOINT_URL)
    try:
        tmp = str(dest) + ".tmp"
        urllib.request.urlretrieve(PUBLIC_CHECKPOINT_URL, tmp)
        shutil.move(tmp, str(dest))
        print("Downloaded checkpoint to:", dest)
        return True
    except Exception as e:
        print("Failed to download checkpoint automatically:", e)
        return False

def run_wav2lip(face, audio, out, checkpoint):
    # This assumes that your repo has inference.py or appropriate entrypoint.
    # Adjust command as per your Wav2Lip repo structure
    cmd = [
        sys.executable, "inference.py",
        "--checkpoint_path", str(checkpoint),
        "--face", str(face),
        "--audio", str(audio),
        "--outfile", str(out)
    ]
    print("Running wav2lip:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        print(line, end="")
    proc.wait()
    return proc.returncode

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--face", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--checkpoint", default=os.environ.get("WAV2LIP_CHECKPOINT", str(DEFAULT_MODEL_DIR / "wav2lip_gan.pth")))
    args = parser.parse_args()

    checkpoint_path = Path(args.checkpoint)
    ok = ensure_checkpoint(checkpoint_path)
    if not ok:
        print("Checkpoint not available. Please set WAV2LIP_CHECKPOINT to a valid file.")
        return 2

    rc = run_wav2lip(args.face, args.audio, args.out, checkpoint_path)
    if rc != 0:
        print("Wav2Lip failed with exit code", rc)
    else:
        print("Wav2Lip completed, output:", args.out)
    return rc

if __name__ == "__main__":
    sys.exit(main())
