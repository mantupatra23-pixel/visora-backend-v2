import replicate
import uuid
import os

def clone_voice_and_generate(script_text, voice_sample_path):
    model = "tstramer/tortoise-tts"

    output = replicate.run(
        model,
        input={
            "text": script_text,
            "voice_audios": [open(voice_sample_path, "rb")],
            "preset": "fast",
            "cvvp_amount": 0.5
        }
    )

    audio_url = output["audio"]

    audio_id = str(uuid.uuid4())[:8]
    save_path = f"static/videos/clone_audio_{audio_id}.wav"

    os.system(f"wget {audio_url} -O {save_path}")

    return save_path
