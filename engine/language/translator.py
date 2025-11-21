from deep_translator import GoogleTranslator

def auto_detect_and_translate(text, target_lang="auto"):
    """
    Detect language automatically and translate to target_lang
    If target_lang = auto → return original language with translation
    """
    detect_lang = GoogleTranslator(source='auto', target='en').detect(text)

    if target_lang == "auto":
        return text, detect_lang
    else:
        translated = GoogleTranslator(source='auto', target=target_lang).translate(text)
        return translated, detect_lang
