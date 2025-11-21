import replicate
import uuid
import os
from moviepy.editor import VideoFileClip, CompositeVideoClip, vfx

def extract_motion(video_path):
    """
    Returns dictionary:
    {
        pan: [values per frame],
        tilt: [...],
        zoom: [...],
        shake_x: [...],
        shake_y: [...]
    }
    """
    output = replicate.run(
        "fxvideo/motion-tracker:03a1d9bc1f",
        input={"video": open(video_path, "rb")}
    )

    return output


def apply_tracked_bg(fg_video_path, bg_video_path, motion):
    fg = VideoFileClip(fg_video_path)
    bg = VideoFileClip(bg_video_path).subclip(0, fg.duration)

    # Resize background to video size
    bg = bg.resize(fg.size)

    # Apply camera motion to background
    def move_bg(t):
        idx = min(int(t * 24), len(motion["pan"]) - 1)  # 24fps index
        
        dx = motion["pan"][idx] * 20 + motion["shake_x"][idx] * 10
        dy = motion["tilt"][idx] * 20 + motion["shake_y"][idx] * 10
        s  = 1 + motion["zoom"][idx] * 0.1

        # Apply scale + position shift
        return dx, dy, s

    def bg_effect(frame_t):
        dx, dy, scale = move_bg(frame_t)
        return bg.resize(scale).set_position((dx, dy)).get_frame(frame_t)

    bg_moving = bg.fl(bg_effect)

    final = CompositeVideoClip([bg_moving, fg]).set_duration(fg.duration)

    out = f"static/videos/bg_track_{uuid.uuid4().hex[:8]}.mp4"
    final.write_videofile(out, fps=24, codec="libx264", audio_codec="aac")
    return out
