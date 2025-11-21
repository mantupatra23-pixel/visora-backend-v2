import replicate
import uuid
import os

def generate_3d_from_face(face_path):
    output = replicate.run(
        "zjx1217/instant-3d-portrait:d3edfb1a912cbbfa0a9f3570e4eb06bbf2aee83aa4e2a5df1dd88a2bbdb92065",
        input={"image": open(face_path, "rb")}
    )

    mesh_url = output["mesh"]
    mesh_save_path = f"static/3d/mesh_{uuid.uuid4().hex[:8]}.obj"
    os.system(f"wget {mesh_url} -O {mesh_save_path}")

    return mesh_save_path


def stylize_3d_texture(face_path, style="pixar"):
    style_prompts = {
        "pixar": "Pixar Disney 3D character, cute round face, big eyes, soft colors",
        "anime": "anime 3D cel-shaded character, glowing eyes, clean lines",
        "fortnite": "Fortnite style 3D warrior, sharp details, game shading",
        "realistic": "photorealistic metahuman texture"
    }

    prompt = style_prompts.get(style, "Pixar 3D character")

    output = replicate.run(
        "lans2023/face-stylizer:acbe43...",   # 3D tex stylizer
        input={"image": open(face_path, "rb"), "prompt": prompt}
    )

    tex_url = output["output"][0]
    tex_path = f"static/3d/tex_{uuid.uuid4().hex[:8]}.png"
    os.system(f"wget {tex_url} -O {tex_path}")

    return tex_path


def generate_3d_talking_avatar(mesh_path, texture_path, audio_path):
    output = replicate.run(
        "metahuman/sadtalker-3d:dsa321...",
        input={
            "mesh": open(mesh_path, "rb"),
            "texture": open(texture_path, "rb"),
            "audio": open(audio_path, "rb"),
        }
    )

    video_url = output["video"]
    save_path = f"static/videos/3d_{uuid.uuid4().hex[:8]}.mp4"
    os.system(f"wget {video_url} -O {save_path}")

    return save_path
