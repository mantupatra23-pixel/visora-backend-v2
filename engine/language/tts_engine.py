from gtts import gTTS
import uuid

def generate_tts(text, lang_code="en"):
    file = f"static/videos/tts_{uuid.uuid4().hex[:6]}.mp3"
    tts = gTTS(text=text, lang=lang_code)
    tts.save(file)
    return file
