# engine/audio/music_sfx_engine.py
import os
import uuid
import math
import tempfile
import numpy as np
from scipy.io.wavfile import write as wav_write
from pydub import AudioSegment
from datetime import datetime

# Optional replicate fallback
try:
    import replicate
    _HAS_REPLICATE = True
except Exception:
    _HAS_REPLICATE = False

# Helpers
ROOT_STATIC = "static/videos"
os.makedirs(ROOT_STATIC, exist_ok=True)

def _normalize_audio(arr):
    # normalize to int16
    if arr.dtype != np.int16:
        arr = arr / np.max(np.abs(arr) + 1e-9)
        arr = (arr * 32767).astype(np.int16)
    return arr

def _save_wav(arr, sr, out_path):
    arr16 = _normalize_audio(arr)
    wav_write(out_path, sr, arr16)

def _to_mp3(wav_path, mp3_path, bitrate="192k"):
    audio = AudioSegment.from_wav(wav_path)
    audio.export(mp3_path, format="mp3", bitrate=bitrate)
    return mp3_path

# -------------------------
# Procedural Instruments
# -------------------------
SAMPLE_RATE = 22050

def sine_wave(frequency, duration, sr=SAMPLE_RATE, amplitude=0.6):
    t = np.linspace(0, duration, int(sr*duration), False)
    wave = amplitude * np.sin(2 * np.pi * frequency * t)
    return wave

def square_wave(frequency, duration, sr=SAMPLE_RATE, amplitude=0.4):
    t = np.linspace(0, duration, int(sr*duration), False)
    wave = amplitude * np.sign(np.sin(2 * np.pi * frequency * t))
    return wave

def sawtooth_wave(frequency, duration, sr=SAMPLE_RATE, amplitude=0.4):
    t = np.linspace(0, duration, int(sr*duration), False)
    wave = amplitude * (2*(t*frequency - np.floor(0.5 + t*frequency)))
    return wave

def simple_drum_kick(duration=0.3, sr=SAMPLE_RATE):
    t = np.linspace(0, duration, int(sr*duration), False)
    # exponentially decaying sine
    env = np.exp(-5 * t)
    wave = env * np.sin(2 * np.pi * 60 * t) * (1.0 - t)
    return wave

def simple_snare(duration=0.2, sr=SAMPLE_RATE):
    # noise burst with envelope
    t = np.linspace(0, duration, int(sr*duration), False)
    noise = np.random.randn(len(t)) * 0.6
    env = np.exp(-20 * t)
    return noise * env

# -------------------------
# Music generator (procedural)
# -------------------------
# A few chord progressions & scale helpers
MAJOR = [0,2,4,5,7,9,11]
MINOR = [0,2,3,5,7,8,10]

NOTE_FREQ = 440.0 * 2 ** ((np.arange(-48, 48) - 9) / 12.0)  # A4 = index 57 roughly

def note_to_freq(midi_note):
    # midi_note 69 = A4 = 440
    return 440.0 * (2 ** ((midi_note - 69)/12.0))

def build_melody(scale="minor", tonic_midi=60, length=8, bpm=100):
    # returns list of midi notes
    scale_set = MINOR if scale=="minor" else MAJOR
    notes = []
    for i in range(length):
        degree = np.random.choice(len(scale_set))
        octave_shift = np.random.choice([0, 12])
        notes.append(tonic_midi + scale_set[degree] + octave_shift)
    return notes

def render_music(duration=12, bpm=100, style="cinematic", seed=None):
    """
    Generates a procedural music track:
    - duration in seconds
    - bpm: tempo (affects note lengths)
    - style: 'cinematic','lofi','energetic','ambient','electro'
    Returns path to mp3 file.
    """
    if seed is not None:
        np.random.seed(seed)

    sr = SAMPLE_RATE
    t_total = duration
    out = np.zeros(int(sr * t_total))

    # base config by style
    if style == "cinematic":
        tonic = 52  # E3-ish
        scale = "minor"
        beat_strength = 0.9
    elif style == "lofi":
        tonic = 60
        scale = "minor"
        beat_strength = 0.6
    elif style == "energetic":
        tonic = 64
        scale = "major"
        beat_strength = 1.0
    elif style == "ambient":
        tonic = 55
        scale = "minor"
        beat_strength = 0.2
    else:
        tonic = 60
        scale = "minor"
        beat_strength = 0.5

    # render chordal pad (long sustained)
    pad = np.zeros_like(out, dtype=float)
    chord_notes = [tonic, tonic+4, tonic+7] if scale=="major" else [tonic, tonic+3, tonic+7]
    for n in chord_notes:
        freq = note_to_freq(n)
        pad += sine_wave(freq, duration=t_total, sr=sr, amplitude=0.12)

    # soft low bass pulses on beats
    bass = np.zeros_like(out, dtype=float)
    beat_interval = 60.0 / bpm
    for k in np.arange(0, t_total, beat_interval):
        start = int(k*sr)
        dur = int(min(0.28*sr, len(out)-start))
        if dur>0:
            bass[start:start+dur] += simple_drum_kick(duration=0.28, sr=sr)[:dur] * 0.8 * beat_strength

    # melody
    melody = np.zeros_like(out, dtype=float)
    notes = build_melody(scale=scale, tonic_midi=tonic, length=int(t_total / (60.0/bpm) * 0.5), bpm=bpm)
    # place melody notes
    note_len = int(sr * (60.0/bpm) * 0.5)  # half-beat notes
    pos = 0
    for n in notes:
        if pos + note_len > len(out): break
        f = note_to_freq(n)
        melody[pos:pos+note_len] += sine_wave(f, note_len/sr, sr=sr, amplitude=0.25) * np.hanning(note_len)
        pos += note_len

    # percussive hi-hats
    hats = np.zeros_like(out, dtype=float)
    hat_interval = beat_interval/2
    for k in np.arange(0, t_total, hat_interval):
        start = int(k*sr)
        dur = int(0.06*sr)
        if start+dur < len(hats):
            hats[start:start+dur] += (np.random.randn(dur) * np.hanning(dur)) * 0.2

    # snare on off-beat
    snare = np.zeros_like(out, dtype=float)
    for k in np.arange(beat_interval/2, t_total, beat_interval):
        start = int(k*sr)
        dur = int(0.2*sr)
        if start+dur < len(snare):
            snare[start:start+dur] += simple_snare(0.2, sr=sr) * 0.8

    # mix layers with gentle mastering
    out = pad * 0.7 + bass * 1.0 + melody * 1.0 + hats * 0.6 + snare * 0.9
    # gentle compression (soft clip)
    out = out / (np.max(np.abs(out))+1e-9) * 0.95
    # write wav + mp3
    wav_path = os.path.join(ROOT_STATIC, f"music_{uuid.uuid4().hex[:8]}.wav")
    mp3_path = wav_path.replace(".wav", ".mp3")
    _save_wav(out, sr, wav_path)
    _to_mp3(wav_path, mp3_path)
    return mp3_path

# -------------------------
# SFX generator
# -------------------------
def generate_sfx(kind="whoosh", duration=1.0, sr=SAMPLE_RATE):
    """
    kind: whoosh, boom, ding, zap, wind, rain, click
    return: mp3 path
    """
    if kind == "whoosh":
        # frequency sweep + noise
        t = np.linspace(0, duration, int(sr*duration), False)
        sweep = np.sin(2*np.pi * (4000 * (1 - t/duration) + 200 * (t/duration)) * t)
        noise = np.random.randn(len(t)) * (1 - t/duration) * 0.6
        s = (sweep * np.hanning(len(t))) * 0.6 + noise * 0.4
    elif kind == "boom":
        t = np.linspace(0, duration, int(sr*duration), False)
        env = np.exp(-6*t)
        s = env * np.sin(2*np.pi*60*t) * (1.0 - t*0.9)
        s += np.random.randn(len(t)) * 0.2 * np.exp(-10*t)
    elif kind == "ding":
        t = np.linspace(0, duration, int(sr*duration), False)
        s = sine_wave(1000, duration, sr=sr, amplitude=0.6) * np.exp(-4*t)
    elif kind == "zap":
        t = np.linspace(0, duration, int(sr*duration), False)
        s = np.sin(2*np.pi * (2000 + 4000 * np.random.rand()) * t) * (np.exp(-4*t))
        s += np.random.randn(len(t)) * 0.1
    elif kind == "wind":
        t = np.linspace(0, duration, int(sr*duration), False)
        s = np.convolve(np.random.randn(len(t)), np.ones(100)/100, mode='same') * 0.4
        s = s * np.hanning(len(t))
    elif kind == "rain":
        t = np.linspace(0, duration, int(sr*duration), False)
        s = np.random.randn(len(t)) * 0.3
        s = np.convolve(s, np.ones(20)/20, mode='same')
    else:
        t = np.linspace(0, duration, int(sr*duration), False)
        s = np.random.randn(len(t)) * 0.1

    # normalize and save
    wav_path = os.path.join(ROOT_STATIC, f"sfx_{kind}_{uuid.uuid4().hex[:8]}.wav")
    mp3_path = wav_path.replace(".wav", ".mp3")
    _save_wav(s, sr, wav_path)
    _to_mp3(wav_path, mp3_path)
    return mp3_path

# -------------------------
# Mixer: Music + Voice + SFX
# -------------------------
def mix_tracks(voice_path, music_path=None, sfx_path=None, music_gain_db=-8, sfx_gain_db=-3):
    """
    Mixes voice (main) with optional background music and sfx.
    Returns final mp3 path.
    """
    voice = AudioSegment.from_file(voice_path)
    out = voice

    if music_path:
        music = AudioSegment.from_file(music_path)
        # loop or trim to match voice length
        if music.duration_seconds < voice.duration_seconds:
            # loop
            repeats = int(math.ceil(voice.duration_seconds / music.duration_seconds))
            music = music * repeats
        music = music[:int(voice.duration_seconds*1000)]
        music = music - abs(music_gain_db)
        out = music.overlay(voice, position=0)

    if sfx_path:
        sfx = AudioSegment.from_file(sfx_path)
        # place sfx at end by default
        pos = max(0, int((voice.duration_seconds - min(2, sfx.duration_seconds)) * 1000))
        out = out.overlay(sfx - abs(sfx_gain_db), position=pos)

    final_path = os.path.join(ROOT_STATIC, f"mixed_{uuid.uuid4().hex[:8]}.mp3")
    out.export(final_path, format="mp3", bitrate="192k")
    return final_path

# -------------------------
# Optional: Replicate cloud music (placeholder)
# -------------------------
def replicate_generate_music(prompt="cinematic score", duration=10):
    """
    If you have REPLICATE_API_TOKEN and a music model, plug here.
    This is placeholder — if replicate available, you can call your preferred model.
    """
    if not _HAS_REPLICATE:
        raise RuntimeError("Replicate not available")
    # Example (user will change model name)
    model = "some/music-model:latest"
    output = replicate.run(model, input={"prompt": prompt, "duration": duration})
    # expected output is URL to audio
    out_url = output[0] if isinstance(output, list) else output
    out_path = os.path.join(ROOT_STATIC, f"music_cloud_{uuid.uuid4().hex[:8]}.mp3")
    os.system(f"wget {out_url} -O {out_path}")
    return out_path
