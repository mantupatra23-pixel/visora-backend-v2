import subprocess
import os
import shlex

WAV2LIP = os.getenv("WAV2LIP_PATH", "/opt/wav2lip")

def run_wav2lip(audio, face, out_file):
    cmd = f"python3 {WAV2LIP}/inference.py --checkpoint_path {WAV2LIP}/checkpoints/wav2lip_gan.pth --face {face} --audio {audio} --outfile {out_file}"
    proc = subprocess.run(cmd, shell=True)

    if proc.returncode != 0:
        raise RuntimeError("Wav2Lip failed")

    return out_file
