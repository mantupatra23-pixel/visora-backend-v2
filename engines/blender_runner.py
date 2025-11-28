import subprocess
import shlex
import os
import logging

logger = logging.getLogger("blender_runner")

def run_blender_scene(scene_json, lip_video, out_file, settings):
    blender = os.getenv("BLENDER_BIN", "/usr/bin/blender")
    script = os.path.join(os.path.dirname(__file__), "blender_render.py")

    cmd = f'{blender} -b -P {script} -- "{scene_json}" "{lip_video}" "{out_file}"'
    logger.info(cmd)

    proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if proc.returncode != 0:
        logger.error(proc.stdout)
        raise RuntimeError("Blender failed")

    return out_file
