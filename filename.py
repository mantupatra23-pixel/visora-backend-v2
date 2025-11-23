# engine/face_lock.py
import os
import logging
from typing import Optional
try:
    import face_recognition
    FACE_LIB_AVAILABLE = True
except Exception:
    FACE_LIB_AVAILABLE = False

from PIL import Image
import numpy as np

logger = logging.getLogger("face_lock")
logging.basicConfig(level=logging.INFO)

def _pil_to_np(img):
    return np.array(img.convert("RGB"))

def extract_face_embeddings(pil_image: Image.Image):
    """
    Returns list of face embeddings found in image (list of np arrays).
    Uses face_recognition (dlib) under the hood.
    """
    if not FACE_LIB_AVAILABLE:
        logger.debug("face_recognition not available — embeddings disabled")
        return []

    rgb = _pil_to_np(pil_image)
    # face_recognition expects (H,W,3) uint8
    locations = face_recognition.face_locations(rgb, model="hog")
    if not locations:
        return []
    encodings = face_recognition.face_encodings(rgb, locations)
    return encodings  # list of 128-d numpy arrays

def compare_embeddings(enc_a, enc_b):
    """
    Returns distance between two embeddings (Euclidean).
    """
    if enc_a is None or enc_b is None:
        return float("inf")
    return np.linalg.norm(enc_a - enc_b)

def pick_primary_face(embeddings):
    """
    If multiple faces, choose first for identity lock.
    """
    if not embeddings:
        return None
    return embeddings[0]

def apply_face_lock(prev_frame, curr_frame, strength=0.85):
    """
    prev_frame, curr_frame: PIL.Image
    Attempt to preserve main face identity by blending feature-preserved region from prev_frame.
    Approach (simple):
      - extract embeddings from both frames
      - if distance < threshold -> assume same person -> do lightweight blending of face area
    Returns PIL.Image (modified curr_frame).
    """
    # If library missing — return curr_frame unchanged
    if not FACE_LIB_AVAILABLE:
        return curr_frame

    try:
        prev_embs = extract_face_embeddings(prev_frame)
        curr_embs = extract_face_embeddings(curr_frame)
        if not prev_embs or not curr_embs:
            return curr_frame

        prev_primary = pick_primary_face(prev_embs)
        curr_primary = pick_primary_face(curr_embs)
        if prev_primary is None or curr_primary is None:
            return curr_frame

        dist = compare_embeddings(prev_primary, curr_primary)
        logger.debug("face distance: %f", float(dist))

        # threshold: 0.6 typical for same person under face_recognition
        if dist < 0.6:
            # same identity — do soft face-region blending
            # find face locations to blend region (use face_recognition.face_locations)
            prev_rgb = _pil_to_np(prev_frame)
            curr_rgb = _pil_to_np(curr_frame)
            prev_locs = face_recognition.face_locations(prev_rgb, model="hog")
            curr_locs = face_recognition.face_locations(curr_rgb, model="hog")
            if not prev_locs or not curr_locs:
                return curr_frame
            # take first face bbox
            pr = prev_locs[0]  # tuple (top, right, bottom, left)
            cr = curr_locs[0]
            # convert to box coords
            p_top, p_right, p_bottom, p_left = pr
            c_top, c_right, c_bottom, c_left = cr

            # extract face regions
            prev_face = prev_frame.crop((p_left, p_top, p_right, p_bottom)).resize((c_right - c_left, c_bottom - c_top))
            # blend prev_face onto curr_frame region using strength
            out = curr_frame.copy()
            blend_alpha = float(strength)
            out.paste(prev_face, (c_left, c_top), prev_face.convert("RGBA").split()[-1] if prev_face.mode == "RGBA" else None)
            # for smoother blend, use simple linear interpolation:
            try:
                curr_region = curr_frame.crop((c_left, c_top, c_right, c_bottom)).convert("RGBA")
                prev_region = prev_face.convert("RGBA")
                blended = Image.blend(curr_region, prev_region, blend_alpha)
                out.paste(blended, (c_left, c_top))
                return out
            except Exception:
                # fallback: pasted prev_face already
                return out
        else:
            # not same identity — no locking
            return curr_frame
    except Exception as e:
        logger.warning("face_lock failed: %s", str(e))
        return curr_frame
