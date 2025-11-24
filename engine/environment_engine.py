# engine/environment_engine.py
"""
Environment Builder - starter

High level functions:
 - create_room(params, out_dir)
 - create_road(params, out_dir)
 - create_forest(params, out_dir)
 - setup_hdri(hdri_key_or_path, out_dir)
 - export_scene(scene_desc, out_path)  # calls Blender or placeholder

Blender hooks: this module will call scripts/<blender_script>.py with args after '--'
If BLENDER_EXEC not set -> fallback placeholder images/videos are generated.

Usage:
    from engine.environment_engine import EnvironmentEngine
    ee = EnvironmentEngine(work_dir="./tmp_env", blender_exec=os.getenv("BLENDER_EXEC"))
    meta = ee.create_room({"size": [4,6,3], "floor":"wood"}, out_dir="./tmp_env/room1")
"""
from __future__ import annotations
import os, json, uuid, shutil, time, subprocess, logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from PIL import Image, ImageDraw, ImageFont, ImageFilter
try:
    from moviepy.editor import ImageSequenceClip
    MOVIEPY_AVAILABLE = True
except Exception:
    MOVIEPY_AVAILABLE = False

log = logging.getLogger("EnvironmentEngine")
log.setLevel(logging.INFO)
if not log.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(ch)


class EnvironmentEngineError(Exception): pass


class EnvironmentEngine:
    def __init__(self, work_dir: str = "./tmp_env_engine", blender_exec: Optional[str] = None, debug: bool = False):
        self.work_dir = Path(work_dir).absolute()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.blender_exec = blender_exec or os.getenv("BLENDER_EXEC")
        self.debug = debug
        log.info("EnvironmentEngine init: work_dir=%s blender_exec=%s", self.work_dir, self.blender_exec)

    def _ensure_out(self, out_dir: Optional[str]) -> Path:
        p = Path(out_dir) if out_dir else self.work_dir / f"env_{uuid.uuid4().hex[:6]}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _call_blender_script(self, script_name: str, args: List[str], timeout: int = 1800):
        if not self.blender_exec:
            raise EnvironmentEngineError("BLENDER_EXEC not configured")
        repo_root = Path(__file__).resolve().parent.parent
        script_path = repo_root / "scripts" / script_name
        if not script_path.exists():
            raise EnvironmentEngineError(f"Blender script not found: {script_path}")
        cmd = [self.blender_exec, "--background", "--python", str(script_path), "--"] + args
        log.info("Calling Blender: %s", " ".join(cmd))
        proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
        log.debug("Blender stdout tail: %s", proc.stdout[-400:])
        return proc

    # -------------------------
    # ROOM
    # -------------------------
    def create_room(self, params: Dict[str, Any], out_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        params example:
          {"size":[width,depth,height],"floor":"wood","wall_mat":"plaster","windows":True}
        Returns metadata: {scene_file, frames_dir (if blender), placeholder_image}
        """
        out = self._ensure_out(out_dir)
        meta = {"type":"room","params":params,"out":str(out)}
        log.info("Create room: %s", meta)
        if self.blender_exec:
            try:
                args = [str(out), json.dumps(params)]
                self._call_blender_script("blender_build_room.py", args)
                meta["scene_file"] = str(out / "room.blend")
                meta["frames_dir"] = str(out / "frames") if (out / "frames").exists() else None
                return meta
            except Exception as e:
                log.warning("Blender room failed, fallback: %s", e)

        # fallback: generate placeholder room image
        placeholder = out / "room_placeholder.png"
        self._create_placeholder_room_image(str(placeholder), params)
        meta["placeholder"] = str(placeholder)
        return meta

    # -------------------------
    # ROAD
    # -------------------------
    def create_road(self, params: Dict[str, Any], out_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        params example:
         {"length":50,"lanes":2,"surface":"asphalt","environment":"urban"}
        """
        out = self._ensure_out(out_dir)
        meta = {"type":"road","params":params,"out":str(out)}
        log.info("Create road: %s", meta)
        if self.blender_exec:
            try:
                args = [str(out), json.dumps(params)]
                self._call_blender_script("blender_build_road.py", args)
                meta["scene_file"] = str(out / "road.blend")
                meta["frames_dir"] = str(out / "frames") if (out / "frames").exists() else None
                return meta
            except Exception as e:
                log.warning("Blender road failed, fallback: %s", e)

        placeholder = out / "road_placeholder.png"
        self._create_placeholder_road_image(str(placeholder), params)
        meta["placeholder"] = str(placeholder)
        return meta

    # -------------------------
    # FOREST
    # -------------------------
    def create_forest(self, params: Dict[str, Any], out_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        params example:
         {"density":0.6,"tree_types":["pine","oak"],"area":100}
        """
        out = self._ensure_out(out_dir)
        meta = {"type":"forest","params":params,"out":str(out)}
        log.info("Create forest: %s", meta)
        if self.blender_exec:
            try:
                args = [str(out), json.dumps(params)]
                self._call_blender_script("blender_build_forest.py", args, timeout=3600)
                meta["scene_file"] = str(out / "forest.blend")
                meta["frames_dir"] = str(out / "frames") if (out / "frames").exists() else None
                return meta
            except Exception as e:
                log.warning("Blender forest failed, fallback: %s", e)

        placeholder = out / "forest_placeholder.png"
        self._create_placeholder_forest_image(str(placeholder), params)
        meta["placeholder"] = str(placeholder)
        return meta

    # -------------------------
    # HDRI setup
    # -------------------------
    def setup_hdri(self, hdri_identifier_or_path: str, out_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        hdri_identifier_or_path: either an asset key (if you have an asset manager) or local path to .hdr/.exr
        Returns metadata with path used.
        """
        out = self._ensure_out(out_dir)
        meta = {"type":"hdri","input":hdri_identifier_or_path,"out":str(out)}
        log.info("Setup HDRI: %s", meta)
        if self.blender_exec:
            try:
                args = [str(out), str(hdri_identifier_or_path)]
                self._call_blender_script("blender_setup_hdri.py", args)
                meta["hdri"] = str(out / "hdri_link.txt") if (out / "hdri_link.txt").exists() else hdri_identifier_or_path
                return meta
            except Exception as e:
                log.warning("Blender HDRI setup failed, fallback: %s", e)
        # fallback just copy if path exists
        p = Path(hdri_identifier_or_path)
        if p.exists():
            dest = out / p.name
            shutil.copy(p, dest)
            meta["hdri"] = str(dest)
        else:
            meta["hdri"] = None
        return meta

    # -------------------------
    # EXPORT SCENE (finalize)
    # -------------------------
    def export_scene(self, scene_desc: Dict[str, Any], out_path: str) -> Dict[str, Any]:
        """
        scene_desc is arbitrary dict referencing created assets.
        If Blender configured -> call exporter script -> produce frames or blend.
        Otherwise create a placeholder composite image or short mp4 (very fast).
        """
        outp = Path(out_path)
        outp.parent.mkdir(parents=True, exist_ok=True)
        meta = {"out": str(outp)}
        if self.blender_exec:
            try:
                args = [str(outp), json.dumps(scene_desc)]
                self._call_blender_script("blender_export_scene.py", args, timeout=3600)
                meta["frames_dir"] = str(outp.parent / "frames") if (outp.parent / "frames").exists() else None
                meta["scene_file"] = str(outp)  # could be .blend or .mp4
                return meta
            except Exception as e:
                log.warning("Blender export failed, fallback: %s", e)
        # fallback: create a single composite placeholder image
        img = Image.new("RGB", (1280,720), color=(90,120,140))
        d = ImageDraw.Draw(img)
        txt = f"Scene placeholder - {scene_desc.get('name','scene')}"
        try:
            fnt = ImageFont.load_default()
        except Exception:
            fnt = None
        d.text((20,20), txt, fill=(255,255,255), font=fnt)
        img.save(outp)
        meta["placeholder"] = str(outp)
        return meta

    # -------------------------
    # Placeholder image creators
    # -------------------------
    def _create_placeholder_room_image(self, out_png: str, params: Dict[str,Any]):
        w,h = 1280,720
        img = Image.new("RGB",(w,h),(160,140,120))
        d = ImageDraw.Draw(img)
        d.rectangle((50,100,w-50,h-100), outline=(40,40,40), width=6)
        d.text((60,110), f"Room: size={params.get('size')}", fill=(255,255,255))
        img.save(out_png)

    def _create_placeholder_road_image(self, out_png: str, params: Dict[str,Any]):
        w,h = 1280,720
        img = Image.new("RGB",(w,h),(70,70,80))
        d = ImageDraw.Draw(img)
        d.rectangle((w//2-120,0,w//2+120,h), fill=(30,30,30))
        d.text((20,20), f"Road: lanes={params.get('lanes')}", fill=(255,255,255))
        img.save(out_png)

    def _create_placeholder_forest_image(self, out_png: str, params: Dict[str,Any]):
        w,h = 1280,720
        img = Image.new("RGB",(w,h),(20,80,40))
        d = ImageDraw.Draw(img)
        for i in range(200):
            x = int((i*37)%w)
            r = 10 + (i%5)*3
            d.ellipse((x,h-100-r,x+6,h-100), fill=(10,50,10))
        d.text((20,20), f"Forest: density={params.get('density')}", fill=(255,255,255))
        img.save(out_png)

    # -------------------------
    # Utility: clear workdir
    # -------------------------
    def clear_workdir(self):
        shutil.rmtree(self.work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
