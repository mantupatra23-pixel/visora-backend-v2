"""
engine/cinematic_scene.py
=========================

CinematicScene - lightweight engine scaffold for building a cinematic scene
with camera, characters, lighting, audio/lipsync and final render to MP4.

This is intentionally modular:
 - Methods are placeholders that collect scene configuration.
 - `render()` tries to call local modules if available (generator_3d, postprocess).
 - Useful for testing end-to-end without 3D heavy dependencies:
    it will create a simple placeholder MP4 (black frames + text) if real renderer missing.

Usage:
    from engine.cinematic_scene import CinematicScene
    scene = CinematicScene(script="A boy walking in rain, cinematic look")
    scene.add_camera(name="cam_main", path="orbit", fov=45)
    scene.add_character(name="boy_01", model="fullbody/default")
    scene.set_lighting(kind="moody", intensity=0.8)
    scene.add_audio(tts_text="...")           # optional
    out = scene.render(output_path="/tmp/out.mp4")
"""

import os
import json
import uuid
import tempfile
from datetime import datetime

# try to use repo's modules if they exist
try:
    from engine.generator_3d import Generator3D  # optional: your 3D generator interface
except Exception:
    Generator3D = None

try:
    from engine.postprocess import PostProcess  # optional: video/audio postprocessing
except Exception:
    PostProcess = None

# fallback: simple video creation
try:
    import moviepy.editor as mpy
except Exception:
    mpy = None


class CinematicScene:
    def __init__(self, script: str, metadata: dict = None):
        """
        script: textual description for the scene (prompt)
        metadata: optional dictionary for extra config (duration, fps, style)
        """
        self.id = str(uuid.uuid4())[:8]
        self.script = script
        self.metadata = metadata or {}
        # scene elements
        self.cameras = []
        self.characters = []
        self.lights = []
        self.audio = None
        self.actions = []  # camera+character choreography
        # rendering defaults
        self.duration = float(self.metadata.get("duration", 5.0))  # seconds
        self.fps = int(self.metadata.get("fps", 25))
        self.resolution = tuple(self.metadata.get("resolution", (640, 360)))
        self.tempdir = tempfile.gettempdir()

    # -------------------------
    # Scene composition methods
    # -------------------------
    def add_camera(self, name="cam", path="static", fov=50, start=None, end=None):
        """Add a camera with a motion path (path is an abstract name here)."""
        cam = {
            "name": name,
            "path": path,
            "fov": fov,
            "start": start,
            "end": end
        }
        self.cameras.append(cam)
        return cam

    def add_character(self, name="char", model="default", position=None, anim=None):
        """Add a character (placeholder). model can be a key to your fullbody/facegen modules."""
        char = {
            "name": name,
            "model": model,
            "position": position or [0, 0, 0],
            "anim": anim or "idle"
        }
        self.characters.append(char)
        return char

    def set_lighting(self, kind="neutral", intensity=1.0):
        """Simple lighting setup."""
        light = {"kind": kind, "intensity": float(intensity)}
        self.lights.append(light)
        return light

    def add_action(self, when: float, what: dict):
        """Schedule an action (camera move, char action) at time `when` seconds."""
        self.actions.append({"time": when, "what": what})
        return self.actions[-1]

    def add_audio(self, tts_text: str = None, audio_file: str = None):
        """
        Attach audio to scene. Either provide TTS text or pre-recorded audio_file path.
        We keep one audio track only in this placeholder.
        """
        if tts_text:
            self.audio = {"type": "tts", "text": tts_text}
        elif audio_file:
            self.audio = {"type": "file", "path": audio_file}
        else:
            self.audio = None
        return self.audio

    # -------------------------
    # Internal helpers
    # -------------------------
    def _summarize(self):
        return {
            "id": self.id,
            "script": self.script,
            "cameras": self.cameras,
            "characters": self.characters,
            "lights": self.lights,
            "actions": self.actions,
            "audio": self.audio,
            "duration": self.duration,
            "fps": self.fps,
            "resolution": self.resolution
        }

    def _try_generator_3d(self):
        """If your repo provides generator_3d.Generator3D, try to call it."""
        if not Generator3D:
            return None

        try:
            gen = Generator3D(scene_config=self._summarize())
            result = gen.generate()  # expected: { "frames_dir": "...", "frames": N }
            return result
        except Exception as e:
            # don't crash: return None to fall back
            print("Generator3D error:", e)
            return None

    def _try_postprocess(self, video_path):
        """If PostProcess exists, call it for audio mux or colour grading."""
        if not PostProcess:
            return video_path
        try:
            pp = PostProcess(video_path)
            out = pp.apply_all(audio=self.audio)
            return out
        except Exception as e:
            print("PostProcess error:", e)
            return video_path

    def _create_placeholder_mp4(self, out_path):
        """Create a very simple MP4 with moviepy (if installed) as a placeholder render.
           It writes N black frames and overlays the script text.
        """
        if not mpy:
            # If moviepy not installed, create an empty file to indicate process finished
            with open(out_path, "wb") as f:
                f.write(b"")  # zero-byte placeholder (not a valid mp4)
            return out_path

        w, h = self.resolution
        duration = max(1.0, float(self.duration))
        txt = f"{self.script}\nid:{self.id}"

        # Create a text clip on black background
        txt_clip = mpy.TextClip(txt, fontsize=24, color="white", method="label")
        txt_clip = txt_clip.set_position(("center", "center")).set_duration(duration)

        bg = mpy.ColorClip(size=(w, h), color=(0, 0, 0))
        bg = bg.set_duration(duration)

        video = mpy.CompositeVideoClip([bg, txt_clip])
        video = video.set_fps(self.fps)

        # write to temporary path
        tmp_out = out_path
        video.write_videofile(tmp_out, codec="libx264", audio=False, fps=self.fps, verbose=False, logger=None)
        return tmp_out

    # -------------------------
    # Main render API
    # -------------------------
    def render(self, output_path: str = None, force_placeholder=False):
        """
        Render the current scene to an MP4 at output_path.
        Return: dict with keys: ok(bool), path(str), info(dict)
        Steps:
          1. try generator_3d -> get frames
          2. if frames found, encode to mp4 (postprocess)
          3. else fallback to placeholder mp4 (moviepy)
        """
        if output_path is None:
            output_path = os.path.join(self.tempdir, f"cinematic_{self.id}.mp4")

        summary = self._summarize()
        # try actual 3D generator if available and not forced placeholder
        if not force_placeholder:
            gen_result = self._try_generator_3d()
        else:
            gen_result = None

        if gen_result and "frames_dir" in gen_result:
            # expected interface: frames_dir + maybe audio_path
            frames_dir = gen_result["frames_dir"]
            # if you have ffmpeg or moviepy, convert frames -> mp4
            try:
                if mpy:
                    # make a clip from image sequence
                    clips = mpy.ImageSequenceClip(sorted([os.path.join(frames_dir, f) for f in os.listdir(frames_dir)]),
                                                 fps=self.fps)
                    clips.write_videofile(output_path, codec="libx264", audio=False, verbose=False, logger=None)
                else:
                    # fallback: try system ffmpeg (best-effort)
                    import subprocess
                    cmd = [
                        "ffmpeg", "-y", "-framerate", str(self.fps),
                        "-i", os.path.join(frames_dir, "%06d.png"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", output_path
                    ]
                    subprocess.run(cmd, check=True)
                # run postprocess if present
                final = self._try_postprocess(output_path)
                return {"ok": True, "path": final, "info": summary}
            except Exception as e:
                print("Error while encoding frames:", e)
                # fallthrough to placeholder
        # fallback: create placeholder MP4
        try:
            final = self._create_placeholder_mp4(output_path)
            final = self._try_postprocess(final)
            return {"ok": True, "path": final, "info": summary}
        except Exception as e:
            return {"ok": False, "error": str(e), "info": summary}


# small test helper when executed directly
if __name__ == "__main__":
    s = CinematicScene("A test boy walking in rain, cinematic look", metadata={"duration": 4, "fps": 12, "resolution": (640,360)})
    s.add_camera("cam_main", path="orbit")
    s.add_character("boy01", model="fullbody/default")
    s.set_lighting("moody", 0.9)
    s.add_audio(tts_text="This is a test audio line.")
    out_file = os.path.join(tempfile.gettempdir(), "test_cinematic.mp4")
    result = s.render(out_file, force_placeholder=True)
    print(json.dumps(result, indent=2))
