def emotion_settings(emotion):
    """
    Returns parameter settings for SadTalker based on emotion.
    """

    if emotion == "happy":
        return {
            "still_mode": False,
            "expression_scale": 1.4,
            "enhancer": "gfpgan",
            "preprocess": "full"
        }

    if emotion == "sad":
        return {
            "still_mode": False,
            "expression_scale": 0.6,
            "enhancer": "gfpgan",
            "preprocess": "full"
        }

    if emotion == "angry":
        return {
            "still_mode": False,
            "expression_scale": 1.8,
            "enhancer": "gfpgan",
            "preprocess": "full"
        }

    if emotion == "surprise":
        return {
            "still_mode": False,
            "expression_scale": 2.0,
            "enhancer": "gfpgan",
            "preprocess": "full"
        }

    # Default: neutral
    return {
        "still_mode": False,
        "expression_scale": 1.0,
        "enhancer": "gfpgan",
        "preprocess": "full"
    }
