# engine/lipsync.py
"""
LIPSYNC ENGINE
Provides:
 - wav2lip_sync(face_video_path, audio_path, out_path, wav2lip_repo, checkpoint, use_gpu=True)
 - align_text_audio(text, audio_path) -> list of {start,end,phoneme}
     (tries aeneas; fallback -> phonemizer no-timing)
 - phonemes_to_visemes(phoneme_timed_list) -> viseme_timed_list
 - generate_viseme_json(...) -> writes a viseme JSON usable by character engine
 - apply_viseme_to_rig(...) -> placeholder hook (user must implement rig-specific mapping)

USAGE:
  from engine.lipsync import wav2lip_sync, align_text_audio, phonemes_to_visemes
  wav2lip_sync("face.mp4","char.wav","out_synced.mp4","/home/user/Wav2Lip","/home/user/wav2lip_gan.pth")
  phonemes = align_text_audio("Hello world", "char.wav")
  visemes = phonemes_to_visemes(phonemes)
"""

from __future__ import annotations
import os
import sys
import json
import shlex
import subprocess
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

log = logging.getLogger("lipsync")
log.setLevel(logging.INFO)
if not log.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(ch)

# Try import aeneas for accurate forced alignment (timestamps)
try:
    from aeneas.executetask import ExecuteTask
    from aeneas.task import Task
    AENEAS_AVAILABLE = True
except Exception:
    AENEAS_AVAILABLE = False

# Try phonemizer as fallback for phoneme extraction (no timings)
try:
    from phonemizer import phonemize
    PHONEMIZER_AVAILABLE = True
except Exception:
    PHONEMIZER_AVAILABLE = False

# -------------------------------
# Wav2Lip integration (subprocess)
# -------------------------------
def wav2lip_sync(face_video_path: str,
                 audio_path: str,
                 out_path: str,
                 wav2lip_repo: str,
                 checkpoint_path: str,
                 use_gpu: bool = True,
                 additional_args: Optional[List[str]] = None) -> str:
    """
    Use Wav2Lip repo to sync audio to face video.
    - face_video_path: path to a short mp4 showing the face (can be longer; Wav2Lip will use frames)
    - audio_path: path to wav/mp3 audio
    - out_path: output synced mp4
    - wav2lip_repo: path to local Wav2Lip repo clone (contains inference.py)
    - checkpoint_path: path to pretrained checkpoint (wav2lip_gan.pth)
    - use_gpu: whether to run on GPU (CUDA required)
    - additional_args: list of extra CLI args for inference.py
    Returns out_path (string). Raises RuntimeError on failure.
    """
    face_video_path = str(face_video_path)
    audio_path = str(audio_path)
    out_path = str(out_path)
    wav2lip_repo = Path(wav2lip_repo).expanduser().resolve()
    if not wav2lip_repo.exists():
        raise RuntimeError(f"Wav2Lip repo path not found: {wav2lip_repo}")

    inf_script = wav2lip_repo / "inference" / "run.py"
    # Note: repo versions vary. Common entrypoints:
    # - inference/run.py  OR  inference.py  OR  inference.py (older).
    if not inf_script.exists():
        # try fallback names
        candidates = [wav2lip_repo / "inference.py", wav2lip_repo / "inference" / "inference.py", wav2lip_repo / "inference" / "inference.py"]
        found = None
        for c in candidates:
            if c.exists():
                found = c
                break
        if not found:
            # older Wav2Lip uses "inference.py" at root
            found_root = wav2lip_repo / "inference.py"
            if found_root.exists():
                inf_script = found_root
            else:
                raise RuntimeError("Could not find Wav2Lip inference script in repo. Check wav2lip_repo path.")
    else:
        found = inf_script

    # Choose script path
    script_path = str(found if 'found' in locals() and found is not None else inf_script)

    # Build command. Many Wav2Lip forks use slightly different CLI args.
    # Typical modern command (Wav2Lip repo by Rudrabha):
    # python inference/run.py --checkpoint_path <ckpt> --face <face_video> --audio <audio> --outfile <out>
    cmd = [
        sys.executable, script_path,
        "--checkpoint_path", str(checkpoint_path),
        "--face", face_video_path,
        "--audio", audio_path,
        "--outfile", out_path
    ]
    if not use_gpu:
        cmd += ["--no_cuda"]
    if additional_args:
        cmd += additional_args

    log.info("Running Wav2Lip inference: %s", " ".join(shlex.quote(c) for c in cmd))
    try:
        proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=1800)
        log.info("Wav2Lip output: %s", proc.stdout[-1000:])
        if Path(out_path).exists():
            return out_path
        else:
            raise RuntimeError("Wav2Lip finished but output file missing.")
    except subprocess.CalledProcessError as e:
        log.error("Wav2Lip failed: stdout=%s stderr=%s", e.stdout, e.stderr)
        raise RuntimeError("Wav2Lip inference failed: " + str(e))
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Wav2Lip inference timed out")

# -------------------------------
# Forced alignment: text -> timed phonemes (aeneas)
# -------------------------------
def align_text_audio(text: str, audio_path: str, language: str = "eng") -> List[Dict[str, Any]]:
    """
    Align text with audio to produce timed segments.
    Returns list of dicts: [{start:0.0, end:0.5, phoneme: "AH"}, ...]
    Uses aeneas if available for accurate timestamps. Fallback returns phoneme list w/o times.
    Note: aeneas expects plain text lines. For shorter lines this works best.
    """
    audio_path = str(audio_path)
    if AENEAS_AVAILABLE:
        try:
            # aeneas requires: Task config string
            # here we use "task_language=eng|os_task_file_format=json|is_text_type=plain"
            tmp_cfg = "task_language=eng|is_text_type=plain|os_task_file_format=json"
            task = Task(config_string=tmp_cfg)
            task.audio_file_path_absolute = audio_path
            # write text to tmp file
            tmp_txt = Path(audio_path).with_suffix(".txt")
            tmp_txt.write_text(text, encoding="utf8")
            task.text_file_path_absolute = str(tmp_txt)
            tmp_out = Path(audio_path).with_suffix(".align.json")
            task.output_file_path_absolute = str(tmp_out)
            ExecuteTask(task).run()
            result = json.loads(tmp_out.read_text(encoding="utf8"))
            fragments = []
            for fragment in result.get("fragments", []):
                frag_txt = fragment.get("lines", [""])[0]
                start = float(fragment.get("begin", 0.0))
                end = float(fragment.get("end", 0.0))
                # phonemize fragment text if phonemizer available
                phoneme = frag_txt
                if PHONEMIZER_AVAILABLE:
                    try:
                        phoneme = phonemize(frag_txt, language="en-us", backend="espeak", strip=True)
                    except Exception:
                        pass
                fragments.append({"start": start, "end": end, "text": frag_txt, "phoneme": phoneme})
            return fragments
        except Exception as e:
            log.exception("aeneas alignment failed: %s", e)
            # fallback to phonemizer below
    # fallback: phonemizer only (no timestamps)
    if PHONEMIZER_AVAILABLE:
        ph = phonemize(text, language="en-us", backend="espeak", strip=True)
        return [{"start": None, "end": None, "text": text, "phoneme": ph}]
    # last fallback: return raw text
    return [{"start": None, "end": None, "text": text, "phoneme": text}]

# -------------------------------
# Phoneme -> Viseme mapping
# -------------------------------
# Basic phoneme-to-viseme mapping (common English ARPAbet-ish / simple tokens)
# This is a heuristic map — adapt for your rig's viseme names.
PHONEME_TO_VISEME = {
    # vowels
    "AA": "A", "AE": "A", "AH": "A", "AO": "O", "AW": "O", "AY": "I",
    "EH": "E", "ER": "ER", "EY": "E", "IH": "I", "IY": "I", "OW": "O", "OY": "O",
    # consonants
    "P": "MBP", "B": "MBP", "M": "MBP",
    "F": "FV", "V": "FV",
    "TH": "TH", "DH": "TH",
    "T": "TD", "D": "TD", "S": "SZ", "Z": "SZ", "SH": "SH", "ZH": "SH",
    "CH": "CH", "JH": "CH",
    "L": "L", "R": "R",
    "W": "W", "Y": "Y", "NG": "NG", "K": "K", "G": "K",
    # fallback
    "default": "REST"
}

def _map_phoneme_to_viseme_token(phoneme_token: str) -> str:
    # Basic normalize: uppercase, remove non-alpha
    t = "".join([c for c in phoneme_token.upper() if c.isalpha()])
    return PHONEME_TO_VISEME.get(t, PHONEME_TO_VISEME["default"])

def phonemes_to_visemes(phoneme_timed_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert timed phoneme fragments (from align_text_audio) into viseme timeline.
    Input example:
      [{"start":0.0,"end":0.4,"text":"Hello","phoneme":"HH AH L OW"}]
    Output:
      [{"start":0.0,"end":0.1,"viseme":"H"}, ...] approx split
    Note: If no timestamps present, we return a coarse map with None times.
    """
    visemes = []
    for frag in phoneme_timed_list:
        phoneme_str = str(frag.get("phoneme") or frag.get("text") or "")
        # split tokens by whitespace
        tokens = [tok.strip() for tok in phoneme_str.replace("|", " ").split() if tok.strip()]
        start = frag.get("start")
        end = frag.get("end")
        if start is None or end is None or len(tokens) == 0:
            # no timing info — return whole fragment as one viseme token (approx)
            vis = _map_phoneme_to_viseme_token(tokens[0] if tokens else "default")
            visemes.append({"start": None, "end": None, "viseme": vis, "text": frag.get("text")})
            continue
        # split the time range evenly across tokens
        duration = max(1e-4, float(end) - float(start))
        per = duration / max(1, len(tokens))
        for i, tok in enumerate(tokens):
            s = start + i * per
            e = s + per
            vis = _map_phoneme_to_viseme_token(tok)
            visemes.append({"start": s, "end": e, "viseme": vis, "phoneme": tok})
    return visemes

# -------------------------------
# Save viseme json helper
# -------------------------------
def generate_viseme_json(viseme_timed_list: List[Dict[str, Any]], out_json: str) -> str:
    Path = Pathlib = None
    from pathlib import Path as _P
    Path = _P
    outp = Path(out_json)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(viseme_timed_list, indent=2), encoding="utf8")
    return str(outp)

# -------------------------------
# Placeholder: apply viseme map to rig (user must implement for their rig)
# -------------------------------
def apply_viseme_to_rig(viseme_timed_list: List[Dict[str, Any]], character, rig_interface) -> bool:
    """
    viseme_timed_list: list of {start,end,viseme}
    character: Character object or identifier
    rig_interface: user provided callable that accepts (character, time_sec, viseme_name, strength)
    This function will call rig_interface at sample times to set blendshapes or keyframes.
    Returns True on success (placeholder).
    """
    # Example sample: call at viseme middle
    for v in viseme_timed_list:
        s = v.get("start")
        e = v.get("end")
        mid = None
        if s is None or e is None:
            # no timing - skip or apply at t=0
            mid = 0.0
        else:
            mid = (s + e) / 2.0
        vis = v.get("viseme")
        try:
            rig_interface(character, time_sec=mid, viseme_name=vis, strength=1.0)
        except Exception as e:
            log.debug("rig_interface call failed for viseme %s at %s: %s", vis, mid, e)
    return True

# -------------------------------
# Example helper to extract a "face-only" video from a longer scene video
# using ffmpeg (face cropping prior to Wav2Lip). This is optional: Wav2Lip
# can handle full frames but better if face area is focused.
# -------------------------------
def crop_face_region_from_video(in_video: str, out_video: str, x:int=0, y:int=0, w:int=256, h:int=256):
    """
    Simple ffmpeg crop. For automatic face crop you should run a face detector and compute bbox.
    """
    cmd = ["ffmpeg", "-y", "-i", in_video, "-filter:v", f"crop={w}:{h}:{x}:{y}", "-c:a", "copy", out_video]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return out_video
    except Exception as e:
        log.exception("FFmpeg crop failed: %s", e)
        raise

# -------------------------------
# Quick test flow (example usage)
# -------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Example:
    # 1) use wav2lip_sync to sync an existing face video with audio
    # 2) use align_text_audio -> phonemes_to_visemes -> apply_viseme_to_rig
    print("Lipsync module ready. See functions: wav2lip_sync, align_text_audio, phonemes_to_visemes")
