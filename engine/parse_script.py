"""
engine/preset_mapper.py

Map parsed scene -> engine-ready presets:
 - character_params: 3D rig options, size, expression, outfit tag, voice/lipsync profile
 - lighting_profile: key/fill/back intensities, color temp, contrast/lut preset
 - camera_profile: (optional) map to internal camera rigs

Usage:
  from engine.preset_mapper import map_presets
  scene_with_presets = map_presets(parsed_scene)
"""

from __future__ import annotations
from typing import Dict, Any
import math

# --- Default profiles (tweakable) ---------------------------------------
CHARACTER_BASE = {
    "male": {
        "height_m": 1.75,
        "body_type": "athletic",
        "skin_tone": "medium",
        "hair": "short",
        "outfit": "casual",
        "lipsync_profile": "neutral_fast",   # used by voice/lipsync engine
        "face_detail": "high"
    },
    "female": {
        "height_m": 1.65,
        "body_type": "slim",
        "skin_tone": "medium",
        "hair": "shoulder",
        "outfit": "casual",
        "lipsync_profile": "neutral_fast",
        "face_detail": "high"
    },
    "unknown": {
        "height_m": 1.70,
        "body_type": "average",
        "skin_tone": "medium",
        "hair": "short",
        "outfit": "casual",
        "lipsync_profile": "neutral",
        "face_detail": "medium"
    }
}

LIGHTING_PRESETS = {
    "soft_cinematic": {
        "key_intensity": 0.9,
        "fill_intensity": 0.45,
        "back_intensity": 0.35,
        "key_angle_deg": 30,
        "color_temp": 4200,
        "contrast": 0.9,
        "lut": "film_warm"
    },
    "high_contrast": {
        "key_intensity": 1.4,
        "fill_intensity": 0.2,
        "back_intensity": 0.5,
        "key_angle_deg": 25,
        "color_temp": 3800,
        "contrast": 1.2,
        "lut": "film_contrast"
    },
    "flat_daylight": {
        "key_intensity": 1.0,
        "fill_intensity": 0.9,
        "back_intensity": 0.2,
        "key_angle_deg": 50,
        "color_temp": 5600,
        "contrast": 0.8,
        "lut": "neutral"
    },
    "night_blue": {
        "key_intensity": 0.6,
        "fill_intensity": 0.25,
        "back_intensity": 0.3,
        "key_angle_deg": 30,
        "color_temp": 3000,
        "contrast": 1.0,
        "lut": "cool_night"
    }
}

CAMERA_RIGS = {
    "static": {"rig": "static_rig", "stabilizer": True},
    "slow_pan": {"rig": "pan_rig", "stabilizer": True, "speed": "slow"},
    "tracking": {"rig": "track_rig", "stabilizer": True, "speed": "medium"},
    "dolly": {"rig": "dolly_rig", "stabilizer": True, "speed": "medium"},
    "crane": {"rig": "crane_rig", "stabilizer": True, "speed": "slow"},
}

# --- Helper functions ----------------------------------------------------
def _pick_lighting_by_weather_time(weather: str, time_of_day: str) -> str:
    w = (weather or "").lower()
    t = (time_of_day or "").lower()
    if "rain" in w or "storm" in w or "fog" in w:
        return "soft_cinematic"
    if t in ("night", "dawn", "dusk"):
        return "night_blue"
    if t in ("noon", "day"):
        return "flat_daylight"
    return "soft_cinematic"

def _map_character_base(role: str, emotion: str, count: int) -> Dict[str, Any]:
    # choose gender mapping
    role_lower = (role or "").lower()
    if "boy" in role_lower or "man" in role_lower:
        base = CHARACTER_BASE["male"].copy()
    elif "girl" in role_lower or "woman" in role_lower:
        base = CHARACTER_BASE["female"].copy()
    else:
        base = CHARACTER_BASE["unknown"].copy()

    # adjust height for 'boy' or 'child'
    if "boy" in role_lower or "girl" in role_lower or "child" in role_lower:
        base["height_m"] = 1.45
        base["body_type"] = "child"

    # tweak outfit for emotion / scene words
    if emotion in ("sad", "scared"):
        base["outfit"] = "worn"
        base["face_detail"] = "high"
    if emotion in ("happy", "excited"):
        base["outfit"] = "clean_casual"

    # scale by count (if many extras, reduce detail)
    if count > 4:
        base["face_detail"] = "medium"
    return base

def _map_camera_rig(camera_info: Dict[str, Any]) -> Dict[str, Any]:
    movement = camera_info.get("movement", "static")
    rig = CAMERA_RIGS.get(movement, CAMERA_RIGS["static"]).copy()
    # map angle preferences
    angle = camera_info.get("angle")
    if angle and "wide" in angle:
        rig["lens"] = "wide_35mm"
    elif angle and "close" in angle:
        rig["lens"] = "tele_85mm"
    else:
        rig["lens"] = "standard_50mm"
    # speed mapping
    speed = camera_info.get("speed")
    if speed == "slow":
        rig["speed_factor"] = 0.6
    elif speed == "fast":
        rig["speed_factor"] = 1.6
    else:
        rig["speed_factor"] = 1.0
    return rig

# --- Main mapping function -----------------------------------------------
def map_presets(parsed_scene: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accept parsed_scene (output of parse_script) and add:
      - 'character_params' : list of per-character 3D parameters
      - 'lighting_profile' : chosen preset values
      - 'camera_profile' : selected rig
    Returns augmented dict (shallow copy).
    """
    scene = parsed_scene.copy()

    # environment -> lighting choice
    env = scene.get("environment", {})
    weather = env.get("weather", "clear")
    time_of_day = env.get("time", "day")

    lighting_key = _pick_lighting_by_weather_time(weather, time_of_day)
    lighting = LIGHTING_PRESETS.get(lighting_key, LIGHTING_PRESETS["soft_cinematic"]).copy()

    # camera mapping
    camera = scene.get("camera", {})
    camera_profile = _map_camera_rig(camera)

    # characters mapping (list)
    characters = scene.get("characters", [])
    char_params = []
    for c in characters:
        role = c.get("role", "unknown")
        emotion = c.get("emotion", "neutral")
        count = int(c.get("count", 1) or 1)
        base = _map_character_base(role, emotion, count)
        # add some scene-specific overrides:
        if lighting_key == "night_blue":
            # slightly desaturate outfit and increase face detail for night grading
            base["face_detail"] = "very_high"
            base["skin_tone"] = base.get("skin_tone", "medium")
        # lipsync profile: map to voice style (simple heuristic)
        if "speaking" in scene.get("actions", []):
            base["lipsync_profile"] = "speech_natural"
        char_params.append(base)

    # Attach to scene
    scene["lighting_profile"] = {
        "preset_name": lighting_key,
        "values": lighting
    }
    scene["camera_profile"] = camera_profile
    scene["character_params"] = char_params

    # also map to engine flags for renderer
    scene.setdefault("engine_flags", {})
    scene["engine_flags"].update({
        "use_high_quality_faces": any(p.get("face_detail", "") in ("high", "very_high") for p in char_params),
        "apply_lut": lighting.get("lut"),
        "render_mode": "cinematic" if "cinematic" in (scene.get("description", "").lower()) else "standard"
    })

    return scene

# --- minimal self-test --------------------------------------------------
if __name__ == "__main__":
    from pprint import pprint
    sample = {
        "description": "A cinematic shot of a boy walking in the rain",
        "camera": {"movement": "slow_pan", "angle": "wide", "speed": "slow"},
        "environment": {"weather": "rain", "time": "evening", "location": "street"},
        "characters": [{"present": True, "role": "boy", "count": 1, "gender": "male", "emotion": "neutral"}],
        "actions": ["walking"],
        "timing": {"duration_seconds": 8, "fps": 25, "num_frames": 200}
    }
    pprint(map_presets(sample))
