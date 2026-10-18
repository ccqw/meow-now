import numpy as np
import pytest

RATE = 16_000


def voiced_burst(pitch: float, seconds: float, rate: int = RATE) -> np.ndarray:
    """A crude vowel: a few harmonics of `pitch` under a smooth envelope."""
    t = np.arange(int(seconds * rate)) / rate
    tone = sum(np.sin(2 * np.pi * pitch * k * t) / k for k in range(1, 6))
    return (tone * np.hanning(len(t)) * 0.4).astype(np.float32)


def speech_like(pitches, syllable_s=0.25, gap_s=0.15, rate: int = RATE) -> np.ndarray:
    """Syllables at the given pitches with silence between them."""
    gap = np.zeros(int(gap_s * rate), dtype=np.float32)
    parts = [gap]
    for p in pitches:
        parts += [voiced_burst(p, syllable_s, rate), gap]
    return np.concatenate(parts)


@pytest.fixture
def rng():
    return np.random.default_rng(1234)
