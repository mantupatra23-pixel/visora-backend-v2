from moviepy.editor import VideoFileClip, vfx

def apply_camera_motion(input_video, effect="zoom-in"):
    clip = VideoFileClip(input_video)

    if effect == "zoom-in":
        clip = clip.fx(vfx.zoom_in, 1.08)

    elif effect == "zoom-out":
        clip = clip.resize(lambda t: 1 - 0.02*t)

    elif effect == "pan-left":
        clip = clip.set_position(lambda t: (-50*t, 0))

    elif effect == "pan-right":
        clip = clip.set_position(lambda t: (50*t, 0))

    elif effect == "tilt-up":
        clip = clip.set_position(lambda t: (0, -40*t))

    elif effect == "tilt-down":
        clip = clip.set_position(lambda t: (0, 40*t))

    elif effect == "shake":
        import random
        clip = clip.set_position(lambda t: (random.randint(-10,10), random.randint(-10,10)))

    elif effect == "dolly-in":
        clip = clip.fx(vfx.zoom_in, 1.15).fx(vfx.lum_contrast, 0, 25, 128)

    return clip
