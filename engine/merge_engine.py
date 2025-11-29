# engine/merge_engine.py
"""
Final processing:
- Re-encode, add container metadata, generate thumbnail
- Optionally upload to S3 / Cloudinary (placeholder)
"""

import logging
import subprocess
from pathlib import Path
import uuid

LOG = logging.getLogger("visora.merge")
LOG.setLevel(logging.INFO)

OUT_DIR = Path("static/videos")
OUT_DIR.mkdir(parents=True, exist_ok=True)

def _run_cmd(cmd):
    LOG.debug("Run cmd: %s", cmd)
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        LOG.error("Command failed: %s\nstderr: %s", cmd, p.stderr.decode())
        raise RuntimeError(p.stderr.decode())
    return p.stdout

def merge_final(lip_video_path: str) -> str:
    """
    Re-encode lip video to consistent format & create thumbnail.
    """
    lip = Path(lip_video_path)
    if not lip.exists():
        raise FileNotFoundError(f"Lip video not found: {lip}")

    out_file = OUT_DIR / f"{uuid.uuid4()}.mp4"
    # Re-encode to H264 baseline, AAC audio, 720p (adjust as needed)
    cmd = (
        f"ffmpeg -y -i {lip} -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k "
        f"-movflags +faststart -vf scale='min(1280,iw)':'-2' {out_file}"
    )
    _run_cmd(cmd)
    LOG.info("Re-encoded final: %s", out_file)

    # thumbnail (first frame)
    thumb = str(out_file.with_suffix('.jpg'))
    _run_cmd(f"ffmpeg -y -i {out_file} -ss 00:00:00 -vframes 1 {thumb}")
    LOG.info("Thumbnail: %s", thumb)

    # Optionally: upload_to_s3(out_file) -> placeholder function
    return str(out_file)

# Placeholder for S3 upload. Implement using boto3 if needed.
def upload_to_s3(filepath: str, bucket: str, key_prefix: str="videos/"):
    """
    Example:
    import boto3
    s3 = boto3.client('s3', aws_access_key_id=..., aws_secret_access_key=...)
    s3.upload_file(filepath, bucket, key_prefix + Path(filepath).name)
    """
    LOG.info("upload_to_s3 called for %s -> %s/%s", filepath, bucket, key_prefix)
    raise NotImplementedError("Configure S3 upload logic here if you want cloud hosting.")
