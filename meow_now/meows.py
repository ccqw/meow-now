"""Synthesize cat meows.

A meow is a vowel glide, roughly "m-ee-aa-oo", over a pitch that rises and
falls. This module builds one additively: a stack of harmonics on the pitch
track, each weighted by how close it sits to the formants of the vowel being
"said" at that moment, under an envelope that opens from a nasal "m".

Synthesis is pure numpy and deterministic for a given seed, so tests can pin
the output and the same request always sounds the same.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from meow_now.audio import SAMPLE_RATE

# Formant targets (F1, F2, F3 in Hz) along the meow. Cats are small, so these
# sit well above human vowel formants.
_VOWEL_PATH = np.array(
    [
        [450.0, 1500.0, 3200.0],  # m (mouth closed, nasal)
        [700.0, 2600.0, 3800.0],  # ee
        [1100.0, 1900.0, 3600.0],  # aa
        [600.0, 1100.0, 3000.0],  # oo / w
    ]
)
_BANDWIDTHS = np.array([160.0, 220.0, 350.0])
_FORMANT_GAIN = np.array([1.0, 0.7, 0.3])

KINDS = ("mew", "meow", "mrrp", "yowl")


@dataclass(frozen=True)
class Meow:
    pitch: float = 600.0  # Hz at the peak of the meow
    duration: float = 0.5  # seconds
    loudness: float = 1.0  # 0..1
    kind: str = "meow"
    contour: tuple[float, ...] = ()  # optional pitch multipliers over time


def kind_for(duration: float, rng: np.random.Generator) -> str:
    """Pick a meow kind that suits the length of what it is replacing."""
    if duration < 0.18:
        return "mew"
    if duration > 0.75:
        return "yowl"
    return "mrrp" if rng.random() < 0.15 else "meow"


def _pitch_track(meow: Meow, t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    x = t / max(meow.duration, 1e-3)  # 0..1 through the meow
    # The basic shape: start a bit low, peak about a third in, fall away.
    shape = 0.82 + 0.18 * np.exp(-(((x - 0.35) / 0.3) ** 2)) - 0.12 * x
    if meow.kind == "mew":
        shape = 0.9 + 0.1 * np.sin(np.pi * x)
    elif meow.kind == "yowl":
        shape = shape * (1 + 0.04 * np.sin(2 * np.pi * 6.0 * t + rng.uniform(0, np.pi)))
    if meow.contour:
        follow = np.interp(x, np.linspace(0, 1, len(meow.contour)), meow.contour)
        # Follow the speaker's intonation, but keep it recognisably a meow.
        shape = shape * follow**0.8
    return meow.pitch * shape


def _formants(meow: Meow, x: np.ndarray) -> np.ndarray:
    """Formant frequencies over time, shape (len(x), 3)."""
    if meow.kind == "mew":
        stops = np.array([0.0, 0.25, 0.7, 1.0])
        path = _VOWEL_PATH[[0, 1, 1, 3]] * 1.15
    elif meow.kind == "mrrp":
        stops = np.array([0.0, 0.45, 0.75, 1.0])
        path = _VOWEL_PATH[[0, 0, 2, 3]]
    else:
        stops = np.array([0.0, 0.12, 0.5, 1.0])
        path = _VOWEL_PATH
    return np.stack([np.interp(x, stops, path[:, i]) for i in range(3)], axis=1)


def _envelope(meow: Meow, t: np.ndarray, rate: int) -> np.ndarray:
    attack = min(0.04, meow.duration * 0.2)
    release = min(0.12, meow.duration * 0.35)
    env = np.ones_like(t)
    env = np.minimum(env, t / attack)
    env = np.minimum(env, (meow.duration - t) / release)
    env = np.clip(env, 0.0, 1.0) ** 1.5
    if meow.kind == "mrrp":
        # The rolled "rrr" at the start: fast amplitude flutter that fades out.
        roll = 0.5 + 0.5 * np.cos(2 * np.pi * 28.0 * t)
        fade = np.clip(t / (meow.duration * 0.45), 0, 1)
        env = env * (roll * (1 - fade) + fade)
    return env


def synthesize(meow: Meow, rate: int = SAMPLE_RATE, seed: int = 0) -> np.ndarray:
    """Render one meow to mono float32 samples."""
    rng = np.random.default_rng(seed)
    n = max(1, int(meow.duration * rate))
    t = np.arange(n) / rate
    x = t / max(meow.duration, 1e-3)

    f0 = _pitch_track(meow, t, rng)
    phase = 2 * np.pi * np.cumsum(f0) / rate
    formants = _formants(meow, x)

    nyquist = rate / 2
    max_k = int(nyquist / max(float(f0.min()), 50.0))
    out = np.zeros(n)
    for k in range(1, max_k + 1):
        freq = k * f0
        audible = freq < nyquist * 0.95
        if not audible.any():
            break
        # Resonance weight: Lorentzian peaks at each formant.
        weight = np.zeros(n)
        for i in range(3):
            weight += _FORMANT_GAIN[i] / (1 + ((freq - formants[:, i]) / _BANDWIDTHS[i]) ** 2)
        weight = weight / k**0.6  # natural spectral tilt of a voice
        out += np.where(audible, weight, 0.0) * np.sin(k * phase + rng.uniform(0, 2 * np.pi))

    # A breath of noise keeps it from sounding like a synth pad.
    breath = rng.standard_normal(n) * 0.02
    out = (out + breath * np.max(np.abs(out))) * _envelope(meow, t, rate)

    top = float(np.max(np.abs(out)))
    if top > 0:
        out = out / top
    return (out * 0.9 * float(np.clip(meow.loudness, 0.05, 1.0))).astype(np.float32)


def place(meows: list[tuple[float, np.ndarray]], duration: float, rate: int = SAMPLE_RATE):
    """Mix (start_seconds, samples) clips onto one timeline of `duration` seconds."""
    end = max([duration] + [start + len(clip) / rate for start, clip in meows])
    timeline = np.zeros(int(np.ceil(end * rate)) + 1, dtype=np.float32)
    for start, clip in meows:
        i = int(start * rate)
        timeline[i : i + len(clip)] += clip
    peak = float(np.max(np.abs(timeline))) if len(timeline) else 0.0
    if peak > 1.0:
        timeline /= peak
    return timeline
