from pathlib import Path

import numpy as np
import pytest

RATE = 16_000
BROWSER_DIR = Path(__file__).parent / "browser"


def pytest_addoption(parser):
    parser.addoption(
        "--browser-smoke",
        action="store_true",
        help="also collect the headless Chromium smoke tests in tests/browser",
    )


def pytest_ignore_collect(collection_path, config):
    # The browser tests need Playwright and Chromium, so the default run skips them entirely.
    if collection_path == BROWSER_DIR and not config.getoption("--browser-smoke"):
        return True
    return None


def voiced_burst(pitch: float, seconds: float, rate: int = RATE) -> np.ndarray:
    """A crude vowel: a few harmonics of `pitch` under a smooth envelope."""
    t = np.arange(int(seconds * rate)) / rate
    tone = sum(np.sin(2 * np.pi * pitch * k * t) / k for k in range(1, 6))
    return (tone * np.hanning(len(t)) * 0.4).astype(np.float32)


def gliding_burst(start: float, end: float, seconds: float, rate: int = RATE) -> np.ndarray:
    """A crude vowel whose pitch glides from `start` to `end` Hz, like a question or a sigh."""
    n = int(seconds * rate)
    f0 = np.linspace(start, end, n)
    phase = 2 * np.pi * np.cumsum(f0) / rate
    tone = sum(np.sin(k * phase) / k for k in range(1, 6))
    return (tone * np.hanning(n) * 0.4).astype(np.float32)


def ending_with(x: np.ndarray, last: np.ndarray, gap_s=0.15, rate: int = RATE) -> np.ndarray:
    """Speech `x` followed by one more syllable `last` and a short silence."""
    gap = np.zeros(int(gap_s * rate), dtype=np.float32)
    return np.concatenate([x, last, gap])


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
