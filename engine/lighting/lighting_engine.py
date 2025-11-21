import replicate
import uuid
import os
from moviepy.editor import VideoFileClip, vfx, CompositeVideoClip, ImageClip

def apply_ai_relight(input_face):
    """
    Uses cloud AI model to relight the avatar face
    """

    output = replicate.run(
        "tencentarc/relight:1d532742f3e6d79f32f1e0f791484ca72d7cf6f3cdbba457e98e8123e5eaf0c3",
        input={
            "image": open(input_face, "rb"),
            "lighting": "cinematic", 
        }
    )

    out_url = output["output"][0]
    save_name = f"engine/lighting/relighted_{uuid.uuid4().hex[:8]}.png"
    
    os.system(f"wget {out_url} -O {save_name}")

    return save_name


def apply_cinematic_lighting(video_path):
    """
    Applies cinema-grade shadows, glow, LUT, and soft light passes
    """
    clip = VideoFileClip(video_path)

    # Step 1: Color grading LUT (teal-orange look)
    clip = clip.fx(vfx.colorx, 1.2).fx(vfx.lum_contrast, 10, 40, 180)

    # Step 2: Rim light (white glow on edges)
    glow = clip.fx(vfx.glow, size=15, alpha=0.18)

    # Step 3: Soft shadow overlay
    shadow = clip.fx(vfx.fadein, 0.1).fx(vfx.fadeout, 0.1)

    # Step 4: Composite
    final = CompositeVideoClip([shadow, glow]).set_duration(clip.duration)

    return final
