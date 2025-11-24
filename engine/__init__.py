# engine/__init__.py
"""
Convenience imports for engine submodules.
Only enable this if submodules are lightweight and safe to import at package import time.
"""

# Basic metadata
__version__ = "0.1.0"

# Expose commonly used submodules / helpers
from .parse_script import parse_script
# from .preset_mapper import map_presets   # enable if present and lightweight

# character / rendering helpers (only if safe to import)
# try:
#     from .character_engine import CharacterEngine
# except Exception:
#     CharacterEngine = None

# try:
#     from .generator_3d import render_scene
# except Exception:
#     render_scene = None

# voice / audio utilities
# try:
#     from .voiceclone import synthesize_voice
# except Exception:
#     synthesize_voice = None

__all__ = [
    "parse_script",
    # "map_presets",
    # "CharacterEngine",
    # "render_scene",
    # "synthesize_voice",
]
