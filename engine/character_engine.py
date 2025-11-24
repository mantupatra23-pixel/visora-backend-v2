"""
engine/character_engine.py

Starter 3D Character Engine for Visora:
 - lightweight, modular, and ready to call from cinematic_scene / pipeline.
 - contains hooks where real Blender/Unreal calls should be placed.
 - fallback: can generate placeholder image frames (PIL) so pipeline end-to-end works w/o heavy deps.

USAGE (example):
    from engine.character_engine import CharacterEngine
    ce = CharacterEngine(work_dir="./tmp_char")
    params = {"name":"Ravi","gender":"male","age":"adult","height_m":1.75,"outfit":"casual","face_detail":"high"}
    char = ce.create_character(params)
    clip_path = ce.render_character_animation(char, duration=3, fps=12)
"""

import os
import uuid
import json
import shutil
import time
from pathlib import Path
from typing import Dict, Any, Optional

# Image placeholder generation
from PIL import Image, ImageDraw, ImageFont

# If you plan to call Blender headless, we'll use subprocess to call a .py blender script
import subprocess
import logging

log = logging.getLogger("CharacterEngine")
log.setLevel(logging.INFO)
if not log.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(ch)


class CharacterEngineError(Exception):
    pass


class Character:
    """Simple data holder for a character instance"""
    def __init__(self, uid: str, name: str, params: Dict[str, Any], workdir: Path):
        self.uid = uid
        self.name = name
        self.params = params
        self.workdir = workdir
        self.assets = {}  # paths to model / textures / rig
        self.state = {"created_at": time.time()}


class CharacterEngine:
    def __init__(self, work_dir: str = "./tmp_character_engine", blender_exec: Optional[str] = None, debug: bool = False):
        """
        work_dir: where per-character folders will be created
        blender_exec: optional path to blender executable for headless renders (e.g. '/usr/bin/blender')
        """
        self.work_dir = Path(work_dir).absolute()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.blender_exec = blender_exec or os.getenv("BLENDER_EXEC")  # set env or pass path
        self.debug = debug
        log.info("CharacterEngine init: work_dir=%s blender_exec=%s", self.work_dir, self.blender_exec)

    # -------------------------
    # Core API
    # -------------------------
    def create_character(self, params: Dict[str, Any]) -> Character:
        """
        Create a Character instance and prepare asset folder.
        params keys (recommended):
           name, gender, age, height_m, body_type, outfit, face_detail
        """
        name = params.get("name", "char")
        uid = uuid.uuid4().hex[:10]
        char_dir = self.work_dir / f"{name}_{uid}"
        char_dir.mkdir(parents=True, exist_ok=True)
        char = Character(uid=uid, name=name, params=params, workdir=char_dir)
        # default assets placeholder
        char.assets["model_file"] = None
        char.assets["rig_file"] = None
        char.assets["textures"] = {}
        log.info("Created character %s at %s", name, char_dir)
        return char

    def import_model(self, char: Character, model_source: str, model_type: str = "fbx") -> str:
        """
        Hook: import an external model (Mixamo FBX or custom) and store path in char.assets.
        model_source: path to uploaded model or asset key
        returns saved path
        """
        dest = char.workdir / f"model.{model_type}"
        # if model_source is an uploaded path, copy; if it's an asset key, implement asset fetch
        if os.path.exists(model_source):
            shutil.copy(model_source, dest)
            char.assets["model_file"] = str(dest)
            log.info("Imported model for %s -> %s", char.name, dest)
            return str(dest)
        else:
            # placeholder: no model, return None
            log.warning("Model source not found: %s (placeholder used)", model_source)
            char.assets["model_file"] = None
            return None

    def apply_outfit(self, char: Character, outfit_key: str):
        """
        Hook: apply outfit to the character model (replace textures / swap meshes).
        Implement your outfit library mapping here.
        """
        char.params["outfit"] = outfit_key
        log.info("Applied outfit '%s' to %s (placeholder)", outfit_key, char.name)

    def apply_expression(self, char: Character, expression: str, intensity: float = 1.0):
        """
        Hook: apply facial expression (blendshape) to char.
        This should call your facial rig system or Blender script.
        """
        log.info("Apply expression %s intensity=%.2f for %s (placeholder)", expression, intensity, char.name)
        char.state.setdefault("expressions", []).append({"expression": expression, "intensity": intensity})

    def apply_mocap(self, char: Character, mocap_path: str, retarget: bool = True) -> bool:
        """
        Hook: apply BVH or FBX mocap file to the character rig (retargeting).
        In production this will call Blender/other retarget pipeline.
        """
        if not os.path.exists(mocap_path):
            log.warning("Mocap path not found: %s", mocap_path)
            return False
        # placeholder: store path
        char.assets["mocap"] = str(mocap_path)
        log.info("Assigned mocap %s to %s (retarget flag=%s)", mocap_path, char.name, retarget)
        return True

    def generate_lipsync_map(self, char: Character, audio_path: str, method: str = "wav2lip") -> Optional[str]:
        """
        Hook: generate viseme/timing map for lipsync. Returns path to viseme json or None.
        Placeholder will create a tiny json with duration and basic steps.
        """
        viseme_path = char.workdir / f"{char.name}_visemes.json"
        # placeholder: simple map
        info = {"audio": audio_path, "steps": [{"t": 0.0, "viseme": "rest"}, {"t": 0.2, "viseme": "aa"}], "method": method}
        with open(viseme_path, "w") as f:
            json.dump(info, f, indent=2)
        log.info("Generated placeholder viseme map at %s", viseme_path)
        return str(viseme_path)

    def render_character_animation(self, char: Character, duration: float = 3.0, fps: int = 12, force_placeholder: bool = False) -> str:
        """
        Render an animation clip for the char and return path to mp4.
        If blender_exec is configured and you have a blender render script, call it; else produce placeholder frames -> mp4 via ffmpeg (moviepy).
        """
        out_mp4 = char.workdir / f"{char.name}_{char.uid}.mp4"
        if self.blender_exec and not force_placeholder:
            # call a blender headless script `scripts/blender_render_character.py` that you must implement
            blender_script = Path(__file__).resolve().parent.parent / "scripts" / "blender_render_character.py"
            if blender_script.exists():
                cmd = [
                    self.blender_exec, "--background", "--python", str(blender_script),
                    "--", str(char.workdir), str(out_mp4), str(duration), str(fps)
                ]
                log.info("Launching Blender headless: %s", " ".join(cmd))
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    if out_mp4.exists():
                        log.info("Blender produced: %s", out_mp4)
                        return str(out_mp4)
                    else:
                        log.warning("Blender finished but no output mp4 found, falling back to placeholder")
                except subprocess.CalledProcessError as e:
                    log.exception("Blender headless failed: %s", e)
                    # fall through to placeholder
            else:
                log.warning("Blender script not found: %s", blender_script)

        # Placeholder renderer (fast): create N colored frames with character name and save as mp4 via ffmpeg/moviepy
        num_frames = max(1, int(duration * fps))
        frames_dir = char.workdir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        self._create_placeholder_frames(frames_dir, char.name, num_frames, size=(720,1280))
        # try to write mp4 using moviepy to avoid requiring ffmpeg CLI in code
        try:
            from moviepy.editor import ImageSequenceClip
            frame_files = sorted([str(p) for p in frames_dir.glob("*.png")])
            clip = ImageSequenceClip(frame_files, fps=fps)
            clip.write_videofile(str(out_mp4), codec="libx264", audio=False, verbose=False, logger=None)
            clip.close()
            log.info("Placeholder MP4 written: %s", out_mp4)
            return str(out_mp4)
        except Exception as e:
            log.exception("Failed to create mp4 via moviepy: %s", e)
            # fallback: create zero-byte file to indicate failure (not ideal)
            out_mp4.write_bytes(b"")
            return str(out_mp4)

    # -------------------------
    # Utilities / Placeholder helpers
    # -------------------------
    def _create_placeholder_frames(self, out_dir: Path, text: str, n: int, size=(720,1280)):
        """
        Make simple PNG frames labelled with character name + frame number.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        w, h = size
        for i in range(n):
            img = Image.new("RGB", (w,h), color=(int(255*(i/n)), 40, 80))
            draw = ImageDraw.Draw(img)
            try:
                fnt = ImageFont.load_default()
            except Exception:
                fnt = None
            draw.text((20,20), f"{text} - frame {i+1}/{n}", fill=(255,255,255), font=fnt)
            draw.text((20,h-40), f"uid:{uuid.uuid4().hex[:6]}", fill=(255,255,255), font=fnt)
            img.save(out_dir / f"frame_{i:04d}.png")

    # -------------------------
    # Simple CLI test
    # -------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ce = CharacterEngine(work_dir="./tmp_char_demo", blender_exec=None, debug=True)
    p = {"name":"Ravi","gender":"male","age":"adult","height_m":1.75,"outfit":"casual","face_detail":"high"}
    char = ce.create_character(p)
    # optional: import model (if you have)
    # ce.import_model(char, "/path/to/sample.fbx")
    ce.apply_outfit(char, "casual")
    ce.apply_expression(char, "smile", 0.8)
    mp4 = ce.render_character_animation(char, duration=3.0, fps=12, force_placeholder=True)
    print("Generated:", mp4)
