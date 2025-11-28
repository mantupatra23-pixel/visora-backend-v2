# engines/postprocess.py
"""
Simple helpers: combine audio + video (ffmpeg), optimize, and upload to S3 (if configured).
"""
import os
import subprocess
from pathlib import Path
import logging
import boto3

LOG = logging.getLogger("postprocess")
LOG.setLevel(logging.INFO)

OUT_DIR = Path(os.environ.get("VIDEO_SAVE_DIR", "static/videos"))
OUT_DIR.mkdir(parents=True, exist_ok=True)


def combine_audio_video(video_in: str, audio_in: str, out_file: str | None = None) -> str:
    if out_file is None:
        out_file = str(Path(OUT_DIR) / f"final_{Path(video_in).stem}.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_in),
        "-i", str(audio_in),
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(out_file)
    ]
    LOG.info("Running ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    LOG.info(proc.stdout)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg combine error")
    return out_file


def upload_to_s3(local_path: str, key: str) -> dict:
    bucket = os.environ.get("S3_BUCKET")
    if not bucket:
        raise RuntimeError("S3_BUCKET not configured")

    s3 = boto3.client(
        "s3",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=os.environ.get("AWS_REGION")
    )
    s3.upload_file(local_path, bucket, key)
    url = f"https://{bucket}.s3.amazonaws.com/{key}"
    return {"bucket": bucket, "key": key, "url": url}
