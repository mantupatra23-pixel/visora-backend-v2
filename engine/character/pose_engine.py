# engine/character/pose_engine.py
import json, os, uuid

PRESETS = {
    "idle": {"label":"Idle standing", "replicate_pose":"idle"},
    "hero": {"label":"Hero pose, hands on waist", "replicate_pose":"hero"},
    "running": {"label":"Running", "replicate_pose":"run"},
    "dance1": {"label":"Dance upbeat", "replicate_pose":"dance_hiphop"},
    "folded": {"label":"Folded hands", "replicate_pose":"folded_hands"},
    "salute": {"label":"Salute pose", "replicate_pose":"salute"}
}

def list_poses():
    return PRESETS

def get_pose_token(pose_name):
    """
    Returns a minimal dictionary to feed into cloud model for chosen pose.
    """
    p = PRESETS.get(pose_name, PRESETS["idle"])
    # replicate models differ: we return textual token standardized
    return {"pose": p["replicate_pose"], "label": p["label"]}
