"""Meow Mockery: replace every syllable you said with a meow, in time and in tune.

The cat also reacts to how you said it. Say nothing and it waits, then asks.
End on a question and it answers, flatly. Go on past about 40 seconds and it
loses interest, meowing shorter and quieter until it yawns.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from meow_now import meows
from meow_now.analysis import Analysis, Syllable, analyze
from meow_now.audio import ANALYSIS_RATE, SAMPLE_RATE, resample

CAT_CENTER_HZ = 650.0  # where the speaker's median pitch lands, in cat
CAT_MIN_HZ = 350.0
CAT_MAX_HZ = 1200.0
MAX_INPUT_S = 60.0

BORED_AFTER_S = 40.0  # syllables starting after this get less and less effort
BORED_FADE_S = 20.0  # seconds over which the effort falls to its floor
BORED_FLOOR = 0.35  # the least effort a bored cat puts into a meow
QUESTION_RISE = 1.12  # the final syllable must end this much above where it started
QUESTION_MIN_S = 0.15  # shorter final syllables are too brief to read as a question
MEH_PAUSE_S = 0.6  # the pause before the cat answers your question

REACTIONS = ("none", "silent", "question", "bored")


@dataclass(frozen=True)
class Mockery:
    audio: np.ndarray
    analysis: Analysis
    meows: tuple[meows.Meow, ...]  # one per syllable, in order
    reaction: str = "none"  # one of REACTIONS
    caption: str | None = None  # a line the UI can show for the reaction
    extras: tuple[tuple[float, meows.Meow], ...] = ()  # (start, meow) not tied to a syllable


def cat_pitch(voice_pitch: float | None, median: float | None) -> float:
    """Map a speaker's pitch into cat range, keeping how high or low it was relative to them."""
    if not voice_pitch or not median:
        return CAT_CENTER_HZ * 1.1  # unvoiced: a short high mew
    ratio = voice_pitch / median
    # Exaggerate a little: cats are more dramatic than people.
    return float(np.clip(CAT_CENTER_HZ * ratio**1.3, CAT_MIN_HZ, CAT_MAX_HZ))


def ends_rising(syl: Syllable) -> bool:
    """Whether a syllable's pitch ends clearly higher than it started, like a question.

    Very short syllables are skipped, and the ends are averaged, because the pitch
    tracker is least sure of itself at a syllable's edges.
    """
    contour = syl.contour
    if syl.duration < QUESTION_MIN_S or len(contour) < 4:
        return False
    half = len(contour) // 2
    return float(np.mean(contour[-3:])) >= QUESTION_RISE * float(np.mean(contour[:half]))


def boredom(start: float) -> float:
    """How much effort the cat puts into a meow starting at `start`: 1.0 down to the floor."""
    if start <= BORED_AFTER_S:
        return 1.0
    return max(BORED_FLOOR, 1.0 - (1.0 - BORED_FLOOR) * (start - BORED_AFTER_S) / BORED_FADE_S)


def clock(seconds: float) -> str:
    """Seconds as m:ss, the way a recording timer shows them."""
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def mock(samples: np.ndarray, rate: int, seed: int = 0) -> Mockery:
    """Analyze a recording and render the meow version of it at SAMPLE_RATE."""
    # Analysis cost scales with the sample rate, and 16 kHz is plenty for pitch.
    samples = resample(samples[: int(MAX_INPUT_S * rate)], rate, ANALYSIS_RATE)
    analysis = analyze(samples, ANALYSIS_RATE)
    rng = np.random.default_rng(seed)
    median = analysis.median_pitch
    syllables = analysis.syllables

    planned: list[meows.Meow] = []
    clips: list[tuple[float, np.ndarray]] = []
    for i, syl in enumerate(syllables):
        effort = boredom(syl.start)
        duration = max(0.08, float(np.clip(syl.duration * 1.1, 0.12, 1.4)) * effort)
        meow = meows.Meow(
            pitch=cat_pitch(syl.pitch, median),
            duration=duration,
            loudness=(0.35 + 0.65 * syl.loudness) * effort,
            kind=meows.kind_for(duration, rng) if syl.pitch else "mew",
            contour=syl.contour,
        )
        planned.append(meow)
        clips.append((syl.start, meows.synthesize(meow, SAMPLE_RATE, seed=seed * 1000 + i)))

    # Reactions come after your last meow; the silent one waits out the whole recording.
    last = max((s.start + m.duration for s, m in zip(syllables, planned, strict=True)), default=0.0)
    reaction, caption = "none", None
    extra: tuple[float, meows.Meow] | None = None
    if not syllables:
        # The Silent Treatment: your silence back, then one small questioning mrrp.
        reaction, caption = "silent", "The cat waited. So did you."
        mrrp = meows.Meow(
            pitch=CAT_CENTER_HZ * 1.1,
            duration=0.35,
            loudness=0.5,
            kind="mrrp",
            contour=(0.9, 0.92, 1.0, 1.12, 1.28),
        )
        extra = (max(analysis.duration, 0.5) + 0.2, mrrp)
    elif syllables[-1].start > BORED_AFTER_S:
        # Lost interest: the fading meows above, then one long falling yawn.
        stopped = next(s.start for s in syllables if s.start > BORED_AFTER_S)
        reaction, caption = "bored", f"The cat stopped listening at {clock(stopped)}."
        yawn = meows.Meow(
            pitch=CAT_CENTER_HZ * 0.85,
            duration=1.3,
            loudness=0.45,
            kind="meow",
            contour=(1.15, 1.1, 1.0, 0.88, 0.76, 0.66, 0.6),
        )
        extra = (last + 0.3, yawn)
    elif ends_rising(syllables[-1]):
        # The rhetorical "meh": your question mirrored, a pause, one flat falling answer.
        reaction, caption = "question", "You asked. The cat considered it. Meh."
        meh = meows.Meow(
            pitch=CAT_CENTER_HZ * 0.9,
            duration=0.45,
            loudness=0.6,
            kind="meow",
            contour=(1.0, 1.0, 0.98, 0.92, 0.82),
        )
        extra = (last + MEH_PAUSE_S, meh)

    extras = (extra,) if extra else ()
    for start, meow in extras:
        clips.append((start, meows.synthesize(meow, SAMPLE_RATE, seed=seed * 1000 + len(planned))))

    audio = meows.place(clips, analysis.duration, SAMPLE_RATE)
    return Mockery(
        audio=audio,
        analysis=analysis,
        meows=tuple(planned),
        reaction=reaction,
        caption=caption,
        extras=extras,
    )
