from engine.motion.motion_engine import extract_motion, apply_tracked_bg
from engine.motion.remove_bg import remove_bg

def replace_background_with_tracking(video_path, bg_path):
    # 1) Remove background
    fg = remove_bg(video_path)

    # 2) Track camera motion
    motion = extract_motion(video_path)

    # 3) Apply background with same motion
    final = apply_tracked_bg(fg, bg_path, motion)

    return final
