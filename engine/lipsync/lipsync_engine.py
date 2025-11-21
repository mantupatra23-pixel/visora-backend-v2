import os
import uuid
import subprocess

def generate_lipsync(face_img, audio_file):
    output_id = str(uuid.uuid4())[:8]
    out_video = f"static/videos/avatar_{output_id}.mp4"

    # Wav2Lip command (light model)
    cmd = [
        "python3", "-m", "wav2lip",
        "--face", face_img,
        "--audio", audio_file,
        "--outfile", out_video
    ]

    subprocess.run(cmd)

    return out_video
