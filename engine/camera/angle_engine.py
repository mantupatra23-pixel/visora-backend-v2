from moviepy.editor import VideoFileClip, vfx
import os

def apply_angle(clip: VideoFileClip, angle: str = "center"):
    """
    Simulate a camera angle/position change by cropping + scaling.
    angle: "left", "center", "right"
    Returns a new clip with same duration as input (but visually shifted).
    """

    w, h = clip.size

    # fraction to crop from sides (adjust for stronger/weaker effect)
    crop_frac = 0.12  # 12% crop for side angles
    if angle == "center":
        # small zoom for center
        x1 = int(w * crop_frac/2)
        x2 = int(w * (1 - crop_frac/2))
    elif angle == "left":
        # crop more on right side, keep left visible
        x1 = 0
        x2 = int(w * (1 - crop_frac))
    elif angle == "right":
        # crop more on left side, keep right visible
        x1 = int(w * crop_frac)
        x2 = w
    else:
        x1 = int(w * crop_frac/2)
        x2 = int(w * (1 - crop_frac/2))

    # prevent invalid ranges
    x1 = max(0, min(x1, w-2))
    x2 = max(x1+2, min(x2, w))

    # crop and resize back to original size (simulate camera reframe)
    cropped = clip.crop(x1=x1, y1=0, x2=x2, y2=h)
    new_clip = cropped.resize((w, h))

    # slight easing: apply a tiny zoom in for cinematic feel
    new_clip = new_clip.fx(vfx.lum_contrast, 0, 10, 128)  # optional subtle contrast
    return new_clip

def apply_angle_sequence(clips, angles):
    """
    clips: list of moviepy clips (scene clips or file paths)
    angles: list of "left"/"center"/"right" with same length
    Returns processed list of clips
    """
    processed = []
    for i, clip in enumerate(clips):
        ang = angles[i] if i < len(angles) else "center"
        processed.append(apply_angle(clip, ang))
    return processed
