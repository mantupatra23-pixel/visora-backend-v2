# engine/sound_engine.py
"""
engine/sound_engine.py

Simple but capable Sound Engine for Visora:
 - Manage tracks: ambience, foley, dialogue, music
 - Schedule events at timestamps (seconds)
 - Spatialize: stereo panning + distance attenuation
 - Reverb via convolution using provided IR (Impulse Response) or simple reverb fallback
 - Mixdown to final wav/mp3 (via pydub/ffmpeg)
 - Returns metadata about generated audio and timestamps for sync

Usage:
  se = SoundEngine(work_dir="./tmp_sound")
  se.add_ambience("ambience/street_loop.wav", volume_db=-6.0)
  se.add_foley_event("foley/footstep.wav", t=1.2, position=(2.5, 0.3), volume_db=0.0)
  se.add_dialogue("voice/char1.wav", t=0.0)
  final = se.render_mix("out/final.wav", sample_rate=22050)
"""

from __future__ import annotations
import os
import uuid
import json
import math
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional

# Primary audio helpers
from pydub import AudioSegment
from pydub.effects import normalize

# For convolution reverb
import numpy as np
try:
    import soundfile as sf
    from scipy.signal import fftconvolve
    SF_AVAILABLE = True
except Exception:
    SF_AVAILABLE = False

log = logging.getLogger("SoundEngine")
log.setLevel(logging.INFO)
if not log.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(ch)


# -------------------------
# Utility functions
# -------------------------
def _db_to_gain(db: float) -> float:
    """Convert dB change to linear gain"""
    return 10 ** (db / 20.0)


def _stereo_pan(audio: AudioSegment, pan: float) -> AudioSegment:
    """
    Simple stereo pan for pydub AudioSegment.
    pan: -1.0 (left) ... 0.0 (center) ... +1.0 (right)
    Works by adjusting left/right channel volumes.
    """
    if audio.channels == 1:
        audio = audio.set_channels(2)
    left, right = audio.split_to_mono()
    # calculate gains
    left_gain = math.cos((pan + 1) * math.pi / 4)  # smooth equal-power panning
    right_gain = math.sin((pan + 1) * math.pi / 4)
    left = left.apply_gain(20 * math.log10(left_gain) if left_gain > 0 else -120.0)
    right = right.apply_gain(20 * math.log10(right_gain) if right_gain > 0 else -120.0)
    return AudioSegment.from_mono_audiosegments(left, right)


def _distance_attenuation(distance: float, ref_dist: float = 1.0, rolloff: float = 1.0) -> float:
    """
    Simple inverse-distance model: gain = 1 / (1 + rolloff * (d - ref))
    Clamped to small epsilon to avoid 0.
    """
    if distance <= ref_dist:
        return 1.0
    g = 1.0 / (1.0 + rolloff * (distance - ref_dist))
    return max(g, 0.001)


def _apply_convolution_reverb_to_raw(signal: np.ndarray, sr: int, ir_path: str) -> np.ndarray:
    """
    Convolve 'signal' (numpy float32 stereo or mono) with IR read by soundfile.
    Returns convolved numpy array (float32). Requires soundfile & scipy.
    """
    if not SF_AVAILABLE:
        raise RuntimeError("soundfile/scipy not available for convolution reverb")
    ir, ir_sr = sf.read(str(ir_path), dtype='float32')
    # resample IR if needed (simple nearest - ideally use proper resampler)
    if ir_sr != sr:
        log.warning("IR sample rate (%s) != target sr (%s). Consider pre-resampling IR.", ir_sr, sr)
    # if both stereo, convolve per-channel; if mono convert
    if signal.ndim == 1:
        # mono
        conv = fftconvolve(signal, ir if ir.ndim == 1 else ir.mean(axis=1), mode='full')
    else:
        # stereo: signal.shape = (N,2)
        if ir.ndim == 1:
            conv_l = fftconvolve(signal[:, 0], ir, mode='full')
            conv_r = fftconvolve(signal[:, 1], ir, mode='full')
        else:
            # IR may be stereo (M,2)
            conv_l = fftconvolve(signal[:, 0], ir[:, 0], mode='full')
            conv_r = fftconvolve(signal[:, 1], ir[:, 1], mode='full')
        conv = np.stack([conv_l, conv_r], axis=1)
    # normalize to -1..1
    maxv = np.max(np.abs(conv)) + 1e-9
    if maxv > 1.0:
        conv = conv / maxv
    return conv.astype('float32')


def _audiosegment_to_numpy(audio: AudioSegment) -> Tuple[np.ndarray, int]:
    """
    Convert pydub AudioSegment -> numpy array (float32) in range -1..1, shape (N,) or (N,2)
    """
    sr = audio.frame_rate
    samples = np.array(audio.get_array_of_samples()).astype(np.float32)
    if audio.channels == 2:
        samples = samples.reshape((-1, 2))
    # pydub uses sample width; normalize
    samples /= float(1 << (8 * audio.sample_width - 1))
    return samples, sr


def _numpy_to_audiosegment(arr: np.ndarray, sr: int, sample_width: int = 2) -> AudioSegment:
    """
    Convert numpy float32 (-1..1) to pydub AudioSegment
    sample_width in bytes (2 -> 16bit)
    """
    # clip
    arr = np.clip(arr, -1.0, 1.0)
    # convert to int16
    if arr.ndim == 2 and arr.shape[1] == 2:
        interleaved = (arr * (2 ** (8 * sample_width - 1) - 1)).astype(np.int16).flatten()
    else:
        interleaved = (arr * (2 ** (8 * sample_width - 1) - 1)).astype(np.int16)
    seg = AudioSegment(
        interleaved.tobytes(),
        frame_rate=sr,
        sample_width=sample_width,
        channels=2 if (arr.ndim == 2 and arr.shape[1] == 2) else 1
    )
    return seg


# -------------------------
# SoundEngine class
# -------------------------
class SoundEngine:
    def __init__(self, work_dir: str = "./tmp_sound_engine", sample_rate: int = 22050, debug: bool = False):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.sample_rate = sample_rate
        self.debug = debug
        # event lists
        self.ambiences: List[Dict] = []
        self.foley_events: List[Dict] = []
        self.dialogues: List[Dict] = []
        self.music_tracks: List[Dict] = []
        # master track length (seconds)
        self.duration_sec = 0.0
        log.info("SoundEngine init: work_dir=%s sample_rate=%d", self.work_dir, self.sample_rate)

    # -------------------------
    # Add layers / events
    # -------------------------
    def add_ambience(self, path: str, start: float = 0.0, volume_db: float = -6.0, loop: bool = True):
        """Add looping ambience track (background)"""
        self.ambiences.append({"path": str(path), "start": float(start), "volume_db": float(volume_db), "loop": bool(loop)})
        log.info("Added ambience %s start=%.2f vol=%.1f loop=%s", path, start, volume_db, loop)

    def add_foley_event(self, path: str, t: float, position: Tuple[float, float] = (0.0, 0.0), volume_db: float = 0.0, rolloff: float = 1.0):
        """
        Add a short foley event at time t (seconds).
        position: (x,y) in meters relative to listener at (0,0)
        """
        self.foley_events.append({"path": str(path), "t": float(t), "position": tuple(position), "volume_db": float(volume_db), "rolloff": float(rolloff)})
        self.duration_sec = max(self.duration_sec, float(t) + 5.0)  # extend a bit by default
        log.info("Added foley %s @ %.2f pos=%s vol=%.1f", path, t, position, volume_db)

    def add_dialogue(self, path: str, start: float = 0.0, volume_db: float = 0.0, position: Optional[Tuple[float, float]] = None):
        self.dialogues.append({"path": str(path), "start": float(start), "volume_db": float(volume_db), "position": position})
        # update duration
        try:
            seg = AudioSegment.from_file(str(path))
            dur = len(seg) / 1000.0
            self.duration_sec = max(self.duration_sec, float(start) + dur)
        except Exception:
            self.duration_sec = max(self.duration_sec, float(start) + 5.0)
        log.info("Added dialogue %s at %.2f pos=%s", path, start, position)

    def add_music(self, path: str, start: float = 0.0, volume_db: float = -6.0, loop: bool = False):
        self.music_tracks.append({"path": str(path), "start": float(start), "volume_db": float(volume_db), "loop": bool(loop)})
        log.info("Added music %s start=%.2f loop=%s", path, start, loop)

    # -------------------------
    # Core render pipeline
    # -------------------------
    def render_mix(self, out_path: str, ir_path: Optional[str] = None, normalize_master: bool = True, export_format: str = "wav") -> Dict[str, str]:
        """
        Render final mix to out_path (wav/mp3). If ir_path provided and soundfile available, apply convolution reverb globally.
        Returns dict {'wav':..., 'meta':...}
        """
        out_path = Path(out_path)
        # compute total length in ms
        total_ms = int(max(1, self.duration_sec) * 1000)
        # base silent track
        master = AudioSegment.silent(duration=total_ms, frame_rate=self.sample_rate)

        # 1) Add ambiences (looping)
        for a in self.ambiences:
            try:
                seg = AudioSegment.from_file(a["path"]).apply_gain(a["volume_db"])
            except Exception as e:
                log.warning("Ambience load failed %s: %s", a["path"], e)
                continue
            if a["loop"]:
                # loop or truncate to master length
                seg = (seg * (int(total_ms / len(seg)) + 2))[:total_ms]
            else:
                seg = seg[:total_ms]
            master = master.overlay(seg, position=int(a["start"] * 1000))

        # 2) Add music tracks
        for m in self.music_tracks:
            try:
                seg = AudioSegment.from_file(m["path"]).apply_gain(m["volume_db"])
            except Exception as e:
                log.warning("Music load failed %s: %s", m["path"], e)
                continue
            if m["loop"]:
                seg = (seg * (int(total_ms / len(seg)) + 2))[:total_ms]
            else:
                seg = seg[:total_ms]
            master = master.overlay(seg, position=int(m["start"] * 1000))

        # 3) Add dialogue (with optional spatialization)
        for d in self.dialogues:
            try:
                seg = AudioSegment.from_file(d["path"]).apply_gain(d["volume_db"])
            except Exception as e:
                log.warning("Dialogue load failed %s: %s", d["path"], e)
                continue
            if d.get("position") is not None:
                pos = d["position"]
                seg = self._spatialize_segment(seg, pos)
            master = master.overlay(seg, position=int(d["start"] * 1000))

        # 4) Add foley events (spatialize each)
        for f in self.foley_events:
            try:
                seg = AudioSegment.from_file(f["path"]).apply_gain(f["volume_db"])
            except Exception as e:
                log.warning("Foley load failed %s: %s", f["path"], e)
                continue
            pos = f.get("position", (0.0, 0.0))
            seg = self._spatialize_segment(seg, pos, rolloff=f.get("rolloff", 1.0))
            master = master.overlay(seg, position=int(f["t"] * 1000))

        # optional: apply convolution reverb (global)
        if ir_path and SF_AVAILABLE:
            try:
                # convert master to numpy
                sig, sr = _audiosegment_to_numpy(master)
                conv = _apply_convolution_reverb_to_raw(sig, sr, ir_path)
                master = _numpy_to_audiosegment(conv, sr)
                log.info("Applied convolution reverb using IR %s", ir_path)
            except Exception as e:
                log.warning("Convolution reverb failed: %s", e)

        # normalize
        if normalize_master:
            try:
                master = normalize(master)
            except Exception:
                pass

        # export
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if export_format.lower() == "wav":
            master.export(str(out_path), format="wav")
        else:
            master.export(str(out_path), format="mp3", bitrate="192k")

        meta_path = out_path.with_suffix(".meta.json")
        meta = {
            "out": str(out_path),
            "duration_s": total_ms / 1000.0,
            "sample_rate": self.sample_rate,
            "ambiences": self.ambiences,
            "foley_events": self.foley_events,
            "dialogues": self.dialogues,
            "music_tracks": self.music_tracks
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf8")
        log.info("Rendered mix => %s", out_path)
        return {"out": str(out_path), "meta": str(meta_path)}

    # -------------------------
    # Spatial helpers
    # -------------------------
    def _spatialize_segment(self, seg: AudioSegment, position: Tuple[float, float], listener_pos: Tuple[float, float] = (0.0, 0.0), rolloff: float = 1.0) -> AudioSegment:
        """
        position: (x,y) meters. listener at (0,0). positive x is to the right.
        Returns panned + attenuated AudioSegment (stereo).
        """
        # compute relative vector
        dx = position[0] - listener_pos[0]
        dy = position[1] - listener_pos[1]
        distance = math.hypot(dx, dy)
        # pan: map angle to -1..1
        angle = 0.0
        if distance > 0.001:
            angle = math.atan2(dy, dx)  # radians
            # normalize to pan with simple projection: left/right only from x
            pan = max(-1.0, min(1.0, dx / (abs(dx) + abs(dy) + 1e-6)))
        else:
            pan = 0.0
        gain = _distance_attenuation(distance, ref_dist=0.8, rolloff=rolloff)
        seg = seg.apply_gain(20 * math.log10(gain) if gain > 0 else -120.0)
        # panning
        try:
            seg = _stereo_pan(seg, pan)
        except Exception:
            # if _stereo_pan fails, ensure stereo
            if seg.channels == 1:
                seg = seg.set_channels(2)
        return seg

    # -------------------------
    # Helpers: clear events
    # -------------------------
    def clear_all(self):
        self.ambiences.clear()
        self.foley_events.clear()
        self.dialogues.clear()
        self.music_tracks.clear()
        self.duration_sec = 0.0
