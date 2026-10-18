"""Meow Mockery: replace every syllable you said with a meow, in time and in tune."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from meow_now import meows
from meow_now.analysis import Analysis, analyze
from meow_now.audio import SAMPLE_RATE, resample

CAT_CENTER_HZ = 650.0  # where the speaker's median pitch lands, in cat
CAT_MIN_HZ = 350.0
CAT_MAX_HZ = 1200.0
MAX_INPUT_S = 60.0


@dataclass(frozen=True)
class Mockery:
    audio: np.ndarray
    analysis: Analysis
    meows: tuple[meows.Meow, ...]


def cat_pitch(voice_pitch: float | None, median: float | None) -> float:
    """Map a speaker's pitch into cat range, keeping how high or low it was relative to them."""
    if not voice_pitch or not median:
        return CAT_CENTER_HZ * 1.1  # unvoiced: a short high mew
    ratio = voice_pitch / median
    # Exaggerate a little: cats are more dramatic than people.
    return float(np.clip(CAT_CENTER_HZ * ratio**1.3, CAT_MIN_HZ, CAT_MAX_HZ))


def mock(samples: np.ndarray, rate: int, seed: int = 0) -> Mockery:
    """Analyze a recording and render the meow version of it at SAMPLE_RATE."""
    # Analysis cost scales with the sample rate, and 16 kHz is plenty for pitch.
    samples = resample(samples[: int(MAX_INPUT_S * rate)], rate, SAMPLE_RATE)
    analysis = analyze(samples, SAMPLE_RATE)
    rng = np.random.default_rng(seed)
    median = analysis.median_pitch

    planned: list[meows.Meow] = []
    clips: list[tuple[float, np.ndarray]] = []
    for i, syl in enumerate(analysis.syllables):
        duration = float(np.clip(syl.duration * 1.1, 0.12, 1.4))
        meow = meows.Meow(
            pitch=cat_pitch(syl.pitch, median),
            duration=duration,
            loudness=0.35 + 0.65 * syl.loudness,
            kind=meows.kind_for(duration, rng) if syl.pitch else "mew",
            contour=syl.contour,
        )
        planned.append(meow)
        clips.append((syl.start, meows.synthesize(meow, SAMPLE_RATE, seed=seed * 1000 + i)))

    audio = meows.place(clips, analysis.duration, SAMPLE_RATE)
    return Mockery(audio=audio, analysis=analysis, meows=tuple(planned))
