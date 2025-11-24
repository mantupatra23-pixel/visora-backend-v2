"""
engine/motion_engine.py

Motion & Animation Engine (starter)

Provides:
 - mocap import
 - mocap retarget hook (calls blender headless if available)
 - motion blending (placeholder)
 - procedural walk generator (placeholder keyframe JSON)
 - foot-planting hook (Blender recommended)
 - export animation to FBX/BVH via Blender headless or placeholder

Usage:
  from engine.motion_engine import MotionEngine
  me = MotionEngine(work_dir="./tmp_motion", blender_exec=os.getenv("BLENDER_EXEC"))
  meta = me.import_mocap("/path/to/run.bvh")
  blended = me.blend_motions(meta1["path"], meta2["path"], weight=0.4)
  out = me.retarget_mocap_to_rig(character, blended)
"""

from __future__ import annotations
import os
import uuid
import json
import shutil
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
import subprocess

# For placeholder procedural frames
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("MotionEngine")
log.setLevel(logging.INFO)
if not log.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(ch)


class MotionEngineError(Exception):
    pass


class MotionMeta:
    def __init__(self, path: str, name: str, duration: float = None, fps: int = 30, frames: int = None):
        self.path = path
        self.name = name
        self.duration = duration
        self.fps = fps
        self.frames = frames
        self.id = uuid.uuid4().hex[:8]


class MotionEngine:
    def __init__(self, work_dir: str = "./tmp_motion_engine", blender_exec: Optional[str] = None, debug: bool = False):
        self.work_dir = Path(work_dir).absolute()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.blender_exec = blender_exec or os.getenv("BLENDER_EXEC")
        self.debug = debug
        log.info("MotionEngine init: work_dir=%s blender_exec=%s", self.work_dir, self.blender_exec)

    # -------------------------
    # Mocap import / meta
    # -------------------------
    def import_mocap(self, mocap_source: str) -> Dict[str, Any]:
        """
        Import a mocap file (BVH/FBX) into the engine workspace.
        Returns metadata: { path, name, duration, fps, frames }
        """
        if not os.path.exists(mocap_source):
            raise MotionEngineError(f"mocap source not found: {mocap_source}")

        name = Path(mocap_source).stem
        dest = self.work_dir / f"{name}_{uuid.uuid4().hex[:6]}{Path(mocap_source).suffix}"
        shutil.copy(mocap_source, dest)
        # Placeholder metadata: real duration requires parsing BVH/FBX
        meta = {"path": str(dest), "name": name, "duration": None, "fps": None, "frames": None}
        # try to extract simple info if BVH (very naive)
        if dest.suffix.lower() == ".bvh":
            try:
                with open(dest, "r", encoding="utf8", errors="ignore") as f:
                    txt = f.read(20000)
                # search for "Frames:" and "Frame Time:"
                import re
                m1 = re.search(r"Frames:\s*([0-9]+)", txt)
                m2 = re.search(r"Frame Time:\s*([0-9\.]+)", txt)
                if m1 and m2:
                    frames = int(m1.group(1))
                    ft = float(m2.group(1))
                    fps = int(round(1.0 / ft))
                    duration = frames / fps
                    meta.update({"duration": duration, "fps": fps, "frames": frames})
            except Exception as e:
                log.debug("BVH parse quick-info failed: %s", e)
        log.info("Imported mocap: %s", meta)
        return meta

    # -------------------------
    # Retargeting (Blender hook)
    # -------------------------
    def retarget_mocap_to_rig(self, mocap_meta: Dict[str, Any], character_asset: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retarget mocap to a character rig. This should call a Blender headless script that:
         - imports the character model (FBX/GLB)
         - imports the mocap (BVH/FBX)
         - retargets mocap to character rig
         - exports retargeted animation as FBX (or leaves it in Blender for rendering)
        Returns metadata with keys: { 'retargeted_anim': path_to_fbx, 'frames': N, 'fps': fps }
        """
        if not self.blender_exec:
            # no blender - return a placeholder "retargeted" which is just original mocap path
            log.warning("Blender not configured; retarget will return mocap as-is (placeholder)")
            return {"retargeted_anim": mocap_meta["path"], "frames": mocap_meta.get("frames"), "fps": mocap_meta.get("fps")}

        # Assume we have script at scripts/blender_retarget.py
        script = Path(__file__).resolve().parent.parent / "scripts" / "blender_retarget.py"
        if not script.exists():
            log.warning("Blender retarget script not found: %s -> placeholder used", script)
            return {"retargeted_anim": mocap_meta["path"], "frames": mocap_meta.get("frames"), "fps": mocap_meta.get("fps")}

        out_fbx = self.work_dir / f"retarget_{uuid.uuid4().hex[:6]}.fbx"
        cmd = [
            self.blender_exec, "--background", "--python", str(script),
            "--", mocap_meta["path"], character_asset.get("model_file", ""), str(out_fbx)
        ]
        log.info("Calling blender retarget: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if out_fbx.exists():
                log.info("Retargeted animation produced: %s", out_fbx)
                return {"retargeted_anim": str(out_fbx), "frames": mocap_meta.get("frames"), "fps": mocap_meta.get("fps")}
            else:
                raise MotionEngineError("Blender retarget finished but no output found")
        except subprocess.CalledProcessError as e:
            log.exception("Blender retarget failed: %s", e)
            raise MotionEngineError("Blender retarget failed: " + str(e))

    # -------------------------
    # Motion blending
    # -------------------------
    def blend_motions(self, base_motion_path: str, overlay_motion_path: str, weight: float = 0.5) -> Dict[str, Any]:
        """
        Blend two motions: base + overlay with given weight.
        Placeholder implementation: will generate a JSON 'blended_motion' which is list of keyframes.
        Production: use motion graph / interpolation on per-joint curves.
        Returns metadata: { 'path': blended_json, 'frames': N, 'fps': fps }
        """
        # validate files exist
        if not os.path.exists(base_motion_path) or not os.path.exists(overlay_motion_path):
            raise MotionEngineError("Motion files for blending not found")

        # create a placeholder JSON describing blend
        blended = {
            "id": uuid.uuid4().hex[:8],
            "base": base_motion_path,
            "overlay": overlay_motion_path,
            "weight": float(weight),
            "notes": "This is a placeholder blended motion. Replace with real motion blending implementation."
        }
        out = self.work_dir / f"blended_{blended['id']}.json"
        with open(out, "w") as f:
            json.dump(blended, f, indent=2)
        log.info("Created placeholder blended motion: %s", out)
        return {"path": str(out), "frames": None, "fps": None}

    # -------------------------
    # Procedural walk generator (placeholder)
    # -------------------------
    def procedural_walk(self, speed: float = 1.0, step_length: float = 0.5, duration: float = 2.0, fps: int = 30) -> Dict[str, Any]:
        """
        Generate a very simple procedural walk as keyframe JSON.
        Production: implement parametric gait generator or use mocap retargeting.
        Returns metadata with 'path' pointing to JSON keyframes.
        """
        num_frames = int(duration * fps)
        frames = []
        for i in range(num_frames):
            t = i / fps
            # foot offsets (very naive sinusoidal)
            left_foot_y = (0.5 * step_length) * (0.5 + 0.5 * __sin_phase(t, speed, 0))
            right_foot_y = (0.5 * step_length) * (0.5 + 0.5 * __sin_phase(t, speed, 0.5))
            frames.append({"frame": i, "time": t, "left_foot_y": left_foot_y, "right_foot_y": right_foot_y})
        meta = {"id": uuid.uuid4().hex[:8], "fps": fps, "duration": duration, "frames": num_frames}
        out = self.work_dir / f"procedural_walk_{meta['id']}.json"
        with open(out, "w") as f:
            json.dump({"meta": meta, "frames": frames}, f, indent=2)
        log.info("Procedural walk generated: %s", out)
        return {"path": str(out), "frames": num_frames, "fps": fps}

    # -------------------------
    # Foot-plant correction (Blender hook)
    # -------------------------
    def apply_foot_planting(self, motion_file: str, rig_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply foot planting correction to reduce foot sliding.
        Production: call Blender IK/FK solver + constraint adjustments.
        If Blender not configured, return input unchanged.
        """
        if not self.blender_exec:
            log.warning("Blender not configured; foot-planting will be skipped (placeholder)")
            return {"corrected_motion": motion_file}
        script = Path(__file__).resolve().parent.parent / "scripts" / "blender_footplant.py"
        if not script.exists():
            log.warning("Blender footplant script not found: %s (placeholder)", script)
            return {"corrected_motion": motion_file}
        out_file = self.work_dir / f"footplant_{uuid.uuid4().hex[:6]}.fbx"
        cmd = [self.blender_exec, "--background", "--python", str(script), "--", motion_file, json.dumps(rig_info), str(out_file)]
        log.info("Running foot-plant Blender script: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if out_file.exists():
                return {"corrected_motion": str(out_file)}
            else:
                raise MotionEngineError("Footplant finished but no output")
        except subprocess.CalledProcessError as e:
            log.exception("Blender footplant failed: %s", e)
            raise MotionEngineError("Blender footplant failed")

    # -------------------------
    # Export animation (FBX/BVH) using Blender headless or fallback
    # -------------------------
    def export_animation_to_fbx(self, character_asset: Dict[str, Any], animation_source: str, out_path: str) -> str:
        """
        Exports the animation applied to a character rig into an FBX for later re-use.
        If Blender configured and script exists, call it. Otherwise, raise or copy source.
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.blender_exec:
            log.warning("Blender not configured; copying source to out_path as placeholder")
            shutil.copy(animation_source, out_path)
            return str(out_path)

        script = Path(__file__).resolve().parent.parent / "scripts" / "blender_export_anim.py"
        if not script.exists():
            log.warning("Blender export script not found; copying source as placeholder")
            shutil.copy(animation_source, out_path)
            return str(out_path)

        cmd = [self.blender_exec, "--background", "--python", str(script), "--", animation_source, character_asset.get("model_file", ""), str(out_path)]
        log.info("Calling blender export: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if out_path.exists():
                log.info("Exported animation to: %s", out_path)
                return str(out_path)
            else:
                raise MotionEngineError("Blender export finished but no output found")
        except subprocess.CalledProcessError as e:
            log.exception("Blender export failed: %s", e)
            raise MotionEngineError("Blender export failed")

    # -------------------------
    # Utility helpers
    # -------------------------
    def read_motion_meta(self, path: str) -> Dict[str, Any]:
        """
        Read JSON/bvh meta (if json created by this engine). Otherwise return basic info.
        """
        p = Path(path)
        if p.suffix.lower() == ".json":
            try:
                return json.loads(p.read_text(encoding="utf8"))
            except Exception:
                pass
        return {"path": path}

    # -------------------------
    # Test CLI
    # -------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    me = MotionEngine(work_dir="./tmp_motion_demo", blender_exec=None, debug=True)
    # test import (use any local .bvh or create demo)
    # procedural walk test
    pw = me.procedural_walk(speed=1.0, step_length=0.4, duration=2.0, fps=12)
    print("Procedural walk JSON:", pw)
