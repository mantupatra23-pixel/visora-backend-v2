"""
engine/cinematic_engine.py

CinematicEngine - starter implementation / pipeline for your Visora engine.

How it works (high level):
1. Accepts a "project" or "script" / list of scenes.
2. For each scene:
   - Calls scene -> frame generator hooks (you must implement real rendering functions in your avatar/fullbody3d modules)
   - Optionally generates or attaches audio (TTS / voice clone)
   - Returns path to rendered clip
3. Assembles clips into a final MP4 using moviepy.
4. Returns final output path.

Add your actual rendering/3D/facegen functions in the hooks below:
 - render_scene_frames(scene, out_dir)
 - generate_scene_audio(scene, out_dir)

This file is intentionally verbose & documented so you can extend quickly.
"""

import os
import shutil
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Optional

# Video assembly
from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip, CompositeAudioClip

# Utilities
from PIL import Image
import numpy as np
import tempfile
import json
import time

# Configure logger
logger = logging.getLogger("CinematicEngine")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(ch)


class CinematicEngineError(Exception):
    pass


class CinematicEngine:
    def __init__(self, work_dir: Optional[str] = None, debug: bool = False):
        """
        :param work_dir: directory for temporary rendering (will be created if missing)
        :param debug: verbose logging
        """
        self.work_dir = Path(work_dir or "./tmp_cinematic_engine").absolute()
        self.debug = debug
        self._ensure_dirs()
        if self.debug:
            logger.setLevel(logging.DEBUG)
        logger.info("CinematicEngine initialized. work_dir=%s", self.work_dir)

    def _ensure_dirs(self):
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def clear_workdir(self):
        """Remove temp dir completely (useful between runs)."""
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)
        self._ensure_dirs()

    def render_project(self, project: Dict) -> str:
        """
        Main entrypoint.
        :param project: dict describing the project. Example minimal:
            {
              "title": "My clip",
              "scenes": [
                {"id":"s1", "duration": 4.0, "script":"A boy walking in rain, cinematic look", "bg":"rain1.mp4", ...},
                ...
              ],
              "output": "final.mp4"  # optional
            }
        :return: path to final mp4
        """
        start = time.time()
        project_id = project.get("id", str(uuid.uuid4()))
        project_dir = self.work_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Rendering project %s → %s", project.get("title", "<untitled>"), project_dir)

        scenes = project.get("scenes", [])
        if not scenes:
            raise CinematicEngineError("Project has no scenes")

        rendered_clips = []
        for i, scene in enumerate(scenes):
            logger.info("Rendering scene %d/%d id=%s", i+1, len(scenes), scene.get("id"))
            clip_path = self.render_scene(scene, project_dir, index=i)
            rendered_clips.append(clip_path)

        final_path = project_dir / (project.get("output", "final_output.mp4"))
        self.assemble_clips(rendered_clips, final_path)
        elapsed = time.time() - start
        logger.info("Project rendered: %s (in %.2fs)", final_path, elapsed)
        return str(final_path)

    def render_scene(self, scene: Dict, project_dir: Path, index: int = 0) -> str:
        """
        Render a single scene and return path to rendered clip (mp4).
        This calls internal hooks where actual rendering must be implemented.
        """
        scene_id = scene.get("id", f"scene_{index}")
        scene_dir = project_dir / f"{index:02d}_{scene_id}"
        scene_dir.mkdir(parents=True, exist_ok=True)

        # 1) Generate frames (placeholder - replace with your real renderer)
        logger.debug("Generating frames for scene %s", scene_id)
        frames_dir = scene_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        # Hook: replace this with actual frame generation
        num_frames = int(scene.get("duration", 3.0) * scene.get("fps", 25))
        self._placeholder_generate_frames(frames_dir, num_frames)

        # 2) Convert frames -> temp video clip using moviepy or ffmpeg (here use moviepy ImageSequenceClip)
        from moviepy.editor import ImageSequenceClip
        frame_files = sorted([str(p) for p in frames_dir.glob("*.png")])
        if not frame_files:
            raise CinematicEngineError(f"No frames generated for scene {scene_id}")

        fps = scene.get("fps", 25)
        clip = ImageSequenceClip(frame_files, fps=fps)
        tmp_clip_path = scene_dir / f"{scene_id}_video.mp4"
        clip.write_videofile(str(tmp_clip_path), codec="libx264", fps=fps, audio=False, verbose=False, logger=None)
        clip.close()

        # 3) Generate or attach audio (TTS / voice) - placeholder hook
        audio_file = None
        if scene.get("voice") or scene.get("tts", True):
            logger.debug("Generating audio for scene %s", scene_id)
            audio_file = self.generate_scene_audio(scene, scene_dir)

        # 4) If audio exists, merge into final clip
        final_scene_clip = scene_dir / f"{scene_id}_final.mp4"
        if audio_file and Path(audio_file).exists():
            video_clip = VideoFileClip(str(tmp_clip_path))
            audio_clip = AudioFileClip(str(audio_file))
            # If audio shorter/longer, we can set duration / loop etc.
            audio_clip = audio_clip.set_duration(video_clip.duration)
            video_clip = video_clip.set_audio(audio_clip)
            video_clip.write_videofile(str(final_scene_clip), codec="libx264", audio_codec="aac", verbose=False, logger=None)
            video_clip.close()
            audio_clip.close()
        else:
            # No audio - rename temp clip to final scene clip
            shutil.move(str(tmp_clip_path), str(final_scene_clip))

        logger.info("Scene rendered: %s", final_scene_clip)
        return str(final_scene_clip)

    def assemble_clips(self, clip_paths: List[str], out_path: Path, cleanup: bool = True):
        """
        Concatenate scene clips into final MP4
        """
        logger.info("Assembling %d clips into %s", len(clip_paths), out_path)
        clips = []
        for p in clip_paths:
            clips.append(VideoFileClip(str(p)))
        final = concatenate_videoclips(clips, method="compose")
        # Optionally set bitrate / codec here
        final.write_videofile(str(out_path), codec="libx264", audio_codec="aac", threads=4, verbose=False, logger=None)
        final.close()
        for c in clips:
            c.close()
        logger.info("Assembled final video: %s", out_path)

        if cleanup:
            # optional: remove intermediate scene folders
            for p in clip_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass

    # ---------------------------
    # Hooks / Placeholders below
    # Replace these with your actual renderers / TTS / avatar pipelines
    # ---------------------------

    def _placeholder_generate_frames(self, out_dir: Path, n_frames: int):
        """
        Temporary generator: creates simple colored PNG frames.
        Replace this with calls to your 3D renderer / avatar generator / sd api wrappers.
        """
        logger.debug("Placeholder: generating %d frames in %s", n_frames, out_dir)
        for i in range(n_frames):
            im = Image.new("RGB", (720, 1280), color=(int(255 * (i / max(1, n_frames - 1))), 50, 100))
            # draw frame number (optional)
            try:
                from PIL import ImageDraw, ImageFont
                draw = ImageDraw.Draw(im)
                draw.text((30, 30), f"Frame {i+1}/{n_frames}", fill=(255, 255, 255))
            except Exception:
                pass
            im.save(out_dir / f"frame_{i:04d}.png")

    def generate_scene_audio(self, scene: Dict, scene_dir: Path) -> Optional[str]:
        """
        Placeholder TTS generator.
        Replace with ElevenLabs / coqui / local TTS pipeline or your voiceclone module.
        Returns path to audio file or None.
        """
        # For now, no audio - return None
        # Example: if you have voiceclone/elevenlabs integration, call it here and save output as wav/mp3
        logger.debug("Placeholder: no audio generated for scene (implement generate_scene_audio)")
        return None

    # ---------------------------
    # Utility helpers
    # ---------------------------

    @staticmethod
    def read_json(path: str) -> Dict:
        with open(path, "r", encoding="utf8") as f:
            return json.load(f)

    @staticmethod
    def write_json(path: str, data: Dict):
        with open(path, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# Quick CLI / test usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = CinematicEngine(work_dir="./tmp_visora_demo", debug=True)
    demo_project = {
        "title": "Demo",
        "scenes": [
            {"id": "s1", "duration": 2.0, "fps": 12, "script": "A boy walking in rain"},
            {"id": "s2", "duration": 2.0, "fps": 12, "script": "Close up cinematic face"},
        ],
        "output": "demo_final.mp4"
    }
    out = engine.render_project(demo_project)
    print("Result:", out)
