# engines/blender_runner.py
"""
Blender render runner (stub).
Assumes blender CLI is available: `blender --background --python <script> -- [args]`
This function calls blender with a small helper script (render_script.py) that you should
place in repo or adapt to your scene pipeline.
"""
import os
import subprocess
from pathlib import Path
import logging
import json

LOG = logging.getLogger("blender")
LOG.setLevel(logging.INFO)

BLENDER_BIN = os.environ.get("BLENDER_BIN", "blender")  # path to blender
BLENDER_SCRIPT = os.environ.get("BLENDER_SCRIPT", "engines/blender_render_script.py")
OUT_DIR = Path(os.environ.get("VIDEO_SAVE_DIR", "static/videos"))
OUT_DIR.mkdir(parents=True, exist_ok=True)


def render_scene(scene_config: dict, output_filename: str | None = None, timeout_sec: int = 1800) -> str:
    """
    scene_config: dictionary with parameters for blender script.
    The blender script should parse sys.argv after '--' to accept JSON path or simple args.
    """
    if output_filename is None:
        output_filename = f"blender_render_{int(os.times()[4])}.mp4"
    output_path = OUT_DIR / output_filename

    # write scene config to temp file
    cfg_path = OUT_DIR / f"scene_{int(os.times()[4])}.json"
    with open(cfg_path, "w") as fh:
        json.dump(scene_config, fh)

    cmd = [
        BLENDER_BIN,
        "--background",
        "--python", BLENDER_SCRIPT,
        "--", str(cfg_path), str(output_path)
    ]
    LOG.info("Starting Blender: %s", " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout_sec)
    LOG.info(proc.stdout)
    if proc.returncode != 0:
        raise RuntimeError("Blender render failed")

    return str(output_path)
