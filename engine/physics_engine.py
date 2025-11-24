"""
engine/physics_engine.py

Physics Engine starter for Visora:
 - Provides high-level functions to run physics simulations:
    * simulate_rain(scene_meta, intensity, duration, out_dir)
    * simulate_dust(scene_meta, intensity, area, out_dir)
    * simulate_cloth(character_asset, cloth_params, out_dir)
    * simulate_hair(character_asset, hair_params, out_dir)
 - If Blender is configured (BLENDER_EXEC), calls Blender headless scripts in /scripts/.
 - If Blender not available, creates placeholder visual overlays (fast) using PIL + moviepy so pipeline remains end-to-end.
 - Exports caches/frames/meshes to out_dir and returns metadata for downstream renderer/compositor.

Usage:
    from engine.physics_engine import PhysicsEngine
    pe = PhysicsEngine(work_dir="./tmp_physics", blender_exec=os.getenv("BLENDER_EXEC"))
    meta = pe.simulate_rain(scene_meta={"location":"street"}, intensity=0.8, duration=4.0, out_dir="./tmp_physics/run1")
"""

from __future__ import annotations
import os
import uuid
import json
import shutil
import time
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List

# For placeholder visuals
from PIL import Image, ImageDraw, ImageFilter
try:
    from moviepy.editor import ImageSequenceClip, VideoFileClip, CompositeVideoClip
    MOVIEPY_AVAILABLE = True
except Exception:
    MOVIEPY_AVAILABLE = False

log = logging.getLogger("PhysicsEngine")
log.setLevel(logging.INFO)
if not log.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(ch)


class PhysicsEngineError(Exception):
    pass


class PhysicsEngine:
    def __init__(self, work_dir: str = "./tmp_physics_engine", blender_exec: Optional[str] = None, debug: bool = False):
        """
        :param work_dir: base dir for caches/exports
        :param blender_exec: optional path to blender executable for headless simulation
        """
        self.work_dir = Path(work_dir).absolute()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.blender_exec = blender_exec or os.getenv("BLENDER_EXEC")
        self.debug = debug
        log.info("PhysicsEngine init: work_dir=%s blender_exec=%s", self.work_dir, self.blender_exec)

    # -------------------------
    # Helpers
    # -------------------------
    def _ensure_out(self, out_dir: str) -> Path:
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _call_blender_script(self, script_name: str, args: List[str], timeout: int = 1800):
        """
        Call a blender headless python script under scripts/ by supplying args after '--'
        Returns: subprocess.CompletedProcess
        """
        if not self.blender_exec:
            raise PhysicsEngineError("Blender executable not configured (BLENDER_EXEC missing)")

        repo_root = Path(__file__).resolve().parent.parent
        script_path = repo_root / "scripts" / script_name
        if not script_path.exists():
            raise PhysicsEngineError(f"Required Blender script missing: {script_path}")

        cmd = [self.blender_exec, "--background", "--python", str(script_path), "--"] + args
        log.info("Calling Blender script: %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
            log.debug("Blender stdout: %s", proc.stdout[-1000:])
            log.debug("Blender stderr: %s", proc.stderr[-1000:])
            return proc
        except subprocess.CalledProcessError as e:
            log.error("Blender script failed: %s", e.stderr[-1000:])
            raise PhysicsEngineError("Blender script failed: " + str(e))
        except Exception as e:
            log.exception("Blender call exception: %s", e)
            raise

    # -------------------------
    # Rain Simulation
    # -------------------------
    def simulate_rain(self, scene_meta: Dict[str, Any], intensity: float = 0.6, duration: float = 4.0, fps: int = 24, out_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Simulate rain particles for a scene.
        - scene_meta: info about camera, environment, bounding box (optional)
        - intensity: 0..1 (drop density)
        - duration: seconds of simulation to produce
        - returns metadata: { frames_dir, overlay_video (optional), particles_cache, params }
        Blender path: calls scripts/blender_simulate_rain.py with args: out_dir intensity duration fps
        Fallback path: create animated rain overlay PNG frames and mp4 (fast).
        """
        out = self._ensure_out(out_dir or str(self.work_dir / f"rain_{uuid.uuid4().hex[:6]}"))
        meta = {"type": "rain", "intensity": float(intensity), "duration": duration, "fps": fps, "out_dir": str(out)}
        log.info("Simulate rain: %s", meta)

        # Try blender if configured
        if self.blender_exec:
            try:
                args = [str(out), str(float(intensity)), str(float(duration)), str(int(fps))]
                self._call_blender_script("blender_simulate_rain.py", args)
                # expected outputs: out/frames/*.exr or png, out/particles_cache/*.abc (optional), out/overlay.mp4
                meta["frames_dir"] = str(out / "frames")
                meta["cache"] = str(out / "particles_cache")
                meta["overlay"] = str(out / "overlay.mp4") if (out / "overlay.mp4").exists() else None
                return meta
            except PhysicsEngineError as e:
                log.warning("Blender rain simulation failed, falling back to placeholder: %s", e)

        # Fallback: generate simple raindrop overlay frames
        frames_dir = out / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        num_frames = max(1, int(duration * fps))
        width = int(scene_meta.get("width", 720))
        height = int(scene_meta.get("height", 1280))
        self._create_rain_frames(frames_dir, num_frames, width, height, intensity)
        overlay_mp4 = out / "overlay.mp4"
        if MOVIEPY_AVAILABLE:
            files = sorted([str(p) for p in frames_dir.glob("*.png")])
            clip = ImageSequenceClip(files, fps=fps)
            clip.write_videofile(str(overlay_mp4), codec="libx264", audio=False, verbose=False, logger=None)
            clip.close()
            meta["overlay"] = str(overlay_mp4)
        meta["frames_dir"] = str(frames_dir)
        meta["cache"] = None
        return meta

    # -------------------------
    # Dust Simulation
    # -------------------------
    def simulate_dust(self, scene_meta: Dict[str, Any], intensity: float = 0.5, area: float = 1.0, duration: float = 3.0, fps: int = 24, out_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Simulate dust particles / volumetric fog.
        Blender path: scripts/blender_simulate_dust.py
        Fallback: create soft translucent dust overlay frames
        """
        out = self._ensure_out(out_dir or str(self.work_dir / f"dust_{uuid.uuid4().hex[:6]}"))
        meta = {"type": "dust", "intensity": float(intensity), "area": float(area), "duration": duration, "fps": fps, "out_dir": str(out)}
        log.info("Simulate dust: %s", meta)

        if self.blender_exec:
            try:
                args = [str(out), str(float(intensity)), str(float(area)), str(float(duration)), str(int(fps))]
                self._call_blender_script("blender_simulate_dust.py", args)
                meta["frames_dir"] = str(out / "frames")
                meta["cache"] = str(out / "particles_cache")
                meta["overlay"] = str(out / "overlay.mp4") if (out / "overlay.mp4").exists() else None
                return meta
            except PhysicsEngineError as e:
                log.warning("Blender dust simulation failed, falling back: %s", e)

        # Fallback dust frames
        frames_dir = out / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        num_frames = max(1, int(duration * fps))
        width = int(scene_meta.get("width", 720))
        height = int(scene_meta.get("height", 1280))
        self._create_dust_frames(frames_dir, num_frames, width, height, intensity)
        overlay_mp4 = out / "overlay.mp4"
        if MOVIEPY_AVAILABLE:
            files = sorted([str(p) for p in frames_dir.glob("*.png")])
            clip = ImageSequenceClip(files, fps=fps)
            clip.write_videofile(str(overlay_mp4), codec="libx264", audio=False, verbose=False, logger=None)
            clip.close()
            meta["overlay"] = str(overlay_mp4)
        meta["frames_dir"] = str(frames_dir)
        meta["cache"] = None
        return meta

    # -------------------------
    # Cloth Simulation
    # -------------------------
    def simulate_cloth(self, character_asset: Dict[str, Any], cloth_params: Dict[str, Any], duration: float = 4.0, fps: int = 24, out_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Simulate cloth on a character rig.
        Blender path: scripts/blender_simulate_cloth.py (should import character FBX/GLB, add cloth modifier, run sim, export baked mesh cache)
        Fallback: return placeholder metadata (no baked mesh)
        Returns: { baked_cache, baked_frames_dir, out_dir, params }
        """
        out = self._ensure_out(out_dir or str(self.work_dir / f"cloth_{uuid.uuid4().hex[:6]}"))
        meta = {"type": "cloth", "params": cloth_params, "duration": duration, "fps": fps, "out_dir": str(out)}
        log.info("Simulate cloth: %s char_asset=%s", meta, character_asset.get("model_file", "<none>"))

        if self.blender_exec and character_asset.get("model_file"):
            try:
                args = [str(out), str(character_asset.get("model_file")), json.dumps(cloth_params), str(float(duration)), str(int(fps))]
                self._call_blender_script("blender_simulate_cloth.py", args, timeout=3600)
                meta["baked_cache"] = str(out / "cloth_cache")
                meta["frames_dir"] = str(out / "frames")
                return meta
            except PhysicsEngineError as e:
                log.warning("Blender cloth sim failed, fallback: %s", e)

        # Fallback: no mesh, just metadata
        meta["baked_cache"] = None
        meta["frames_dir"] = None
        return meta

    # -------------------------
    # Hair Simulation
    # -------------------------
    def simulate_hair(self, character_asset: Dict[str, Any], hair_params: Dict[str, Any], duration: float = 4.0, fps: int = 24, out_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Simulate hair physics (particle hair system).
        Blender path: scripts/blender_simulate_hair.py
        Fallback: metadata placeholder
        """
        out = self._ensure_out(out_dir or str(self.work_dir / f"hair_{uuid.uuid4().hex[:6]}"))
        meta = {"type": "hair", "params": hair_params, "duration": duration, "fps": fps, "out_dir": str(out)}
        log.info("Simulate hair: %s char_asset=%s", meta, character_asset.get("model_file", "<none>"))

        if self.blender_exec and character_asset.get("model_file"):
            try:
                args = [str(out), str(character_asset.get("model_file")), json.dumps(hair_params), str(float(duration)), str(int(fps))]
                self._call_blender_script("blender_simulate_hair.py", args, timeout=3600)
                meta["baked_cache"] = str(out / "hair_cache")
                meta["frames_dir"] = str(out / "frames")
                return meta
            except PhysicsEngineError as e:
                log.warning("Blender hair sim failed, fallback: %s", e)

        meta["baked_cache"] = None
        meta["frames_dir"] = None
        return meta

    # -------------------------
    # Compositing helper (overlay frames onto base frames)
    # -------------------------
    def composite_overlay_on_frames(self, base_frames_dir: str, overlay_mp4_or_frames: str, out_dir: Optional[str] = None, fps: int = 24) -> Dict[str, Any]:
        """
        Composite overlay (alpha) on top of base frames and write final frames or mp4.
        overlay_mp4_or_frames can be:
          - path to overlay mp4 (alpha or RGB)
          - path to folder with png frames (assumed same length)
        Returns metadata with 'out_frames_dir' and 'out_mp4' (if created)
        """
        base = Path(base_frames_dir)
        if not base.exists():
            raise PhysicsEngineError("Base frames directory not found: " + base_frames_dir)
        out = self._ensure_out(out_dir or str(self.work_dir / f"composite_{uuid.uuid4().hex[:6]}"))
        out_frames = out / "frames"
        out_frames.mkdir(parents=True, exist_ok=True)
        log.info("Composite overlay onto frames: base=%s overlay=%s", base, overlay_mp4_or_frames)

        # If overlay is mp4 we will composite per-frame using moviepy if available
        if MOVIEPY_AVAILABLE:
            base_files = sorted([str(p) for p in base.glob("*.png")])
            # load overlay clip
            overlay_clip = None
            if Path(overlay_mp4_or_frames).is_file():
                overlay_clip = VideoFileClip(str(overlay_mp4_or_frames)).resize((base_files and Image.open(base_files[0]).size[0] or 720, None))
            else:
                # assume overlay frames dir
                overlay_dir = Path(overlay_mp4_or_frames)
                overlay_files = sorted([str(p) for p in overlay_dir.glob("*.png")])
                overlay_clip = ImageSequenceClip(overlay_files, fps=fps)

            # write composite frames by applying overlay over each base frame
            for i, bf in enumerate(base_files):
                base_img = Image.open(bf).convert("RGBA")
                try:
                    # get overlay frame as image
                    ov = overlay_clip.get_frame(i / float(fps))
                    ov_img = Image.fromarray(ov).convert("RGBA")
                    base_img.alpha_composite(ov_img)
                except Exception:
                    # if overlay shorter, just keep base
                    pass
                base_img.save(out_frames / f"frame_{i:04d}.png")
            # produce mp4
            out_mp4 = out / "composite.mp4"
            files = sorted([str(p) for p in out_frames.glob("*.png")])
            clip = ImageSequenceClip(files, fps=fps)
            clip.write_videofile(str(out_mp4), codec="libx264", audio=False, verbose=False, logger=None)
            clip.close()
            return {"out_frames_dir": str(out_frames), "out_mp4": str(out_mp4)}
        else:
            # no moviepy - just copy base frames to out (no composition)
            for i, bf in enumerate(sorted([p for p in base.glob("*.png")])):
                shutil.copy(bf, out_frames / bf.name)
            return {"out_frames_dir": str(out_frames), "out_mp4": None}

    # -------------------------
    # Placeholder frame-generators
    # -------------------------
    def _create_rain_frames(self, out_dir: Path, n: int, w: int, h: int, intensity: float):
        out_dir.mkdir(parents=True, exist_ok=True)
        import random, math
        density = max(0.05, min(1.0, intensity)) * 0.02 * (w*h/1000000.0) * 100  # heuristic
        drops = int(100 * density)
        for i in range(n):
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            for d in range(drops):
                x = random.randint(0, w-1)
                y = int((random.random() + i/float(n)) * h) % h
                length = max(8, int(8 * (0.5 + random.random())))
                alpha = int(120 * min(1.0, intensity + random.random()*0.3))
                draw.line((x, y, x, y+length), fill=(200,200,255, alpha), width=1)
            # slight blur for motion
            img = img.filter(ImageFilter.GaussianBlur(radius=0.8))
            img.save(out_dir / f"rain_{i:04d}.png")

    def _create_dust_frames(self, out_dir: Path, n: int, w: int, h: int, intensity: float):
        out_dir.mkdir(parents=True, exist_ok=True)
        import random
        particles = int(40 + 200 * float(intensity))
        for i in range(n):
            base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(base)
            for p in range(particles):
                x = int(random.random() * w)
                y = int(random.random() * h)
                size = int(1 + random.random() * 6 * intensity)
                alpha = int(20 + 180 * (random.random() * intensity))
                color = (200, 180, 150, alpha)
                draw.ellipse((x, y, x+size, y+size), fill=color)
            base = base.filter(ImageFilter.GaussianBlur(radius=1.0*intensity))
            base.save(out_dir / f"dust_{i:04d}.png")

    # -------------------------
    # Utility: clear workdir
    # -------------------------
    def clear_workdir(self):
        try:
            shutil.rmtree(self.work_dir)
        except Exception:
            pass
        self.work_dir.mkdir(parents=True, exist_ok=True)
