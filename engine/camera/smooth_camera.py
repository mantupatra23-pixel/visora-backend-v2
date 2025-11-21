from moviepy.editor import VideoFileClip
import math

def smooth_pan(clip, direction="left", strength=80):
    """
    direction: left / right / up / down
    strength: how far camera moves
    Applies smooth motion using a sin curve (cinematic motion)
    """

    w, h = clip.size

    def pos(t):
        shift = strength * math.sin(2 * math.pi * (t / clip.duration))
        
        if direction == "left":
            return (-shift, 0)
        elif direction == "right":
            return (shift, 0)
        elif direction == "up":
            return (0, -shift)
        elif direction == "down":
            return (0, shift)
        else:
            return (0,0)

    return clip.set_position(pos)


def smooth_zoom(clip, zoom_amount=1.07):
    return clip.resize(lambda t: 1 + (zoom_amount - 1) * (t / clip.duration))


def smooth_slide_transition(clip, direction="right"):
    """
    Clip slides in with motion:
    direction: right / left / up / down
    """
    w, h = clip.size

    def pos(t):
        progress = t / clip.duration * 1.2  # slightly faster entry
        shift = (1 - progress)

        if shift < 0:
            shift = 0

        if direction == "right":
            return (w * shift, 0)
        elif direction == "left":
            return (-w * shift, 0)
        elif direction == "up":
            return (0, -h * shift)
        elif direction == "down":
            return (0, h * shift)

    return clip.set_position(pos)
