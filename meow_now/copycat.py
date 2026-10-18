"""Copycat: the cat meows a short phrase, you copy it, and it tells you how close you got.

A phrase is a few meows with their own rhythm and melody, drawn from a seed.
Phrases grow by one meow at a time, and a longer phrase always starts with the
shorter one, so each round of a game repeats what you already copied and adds
one more.

Scoring is pure and deterministic, built on `analysis.analyze`. It compares
three things, each in a way that forgives what a person cannot help:

- **Count.** Every meow of the phrase should match one syllable of yours. A
  meow with no syllable is missed; a syllable with no meow is an extra. The
  analysis sometimes splits one sound at an energy valley; touching pieces may
  be matched to one meow together, so that is not held against you.
- **Timing.** Your onsets are fitted to the cat's with one global offset and
  one global tempo (you can start whenever you like, and copy it a little
  faster or slower), then each meow is judged by what is left: early or late.
- **Pitch.** Everything is in semitones, so your voice is free to sit anywhere:
  one global transposition is removed (the median over your meows), and a meow
  heard a whole octave out counts as the right note, since pitch trackers and
  people both slip octaves. What is left says whether each meow was higher or
  lower than the tune asked for. Within each meow, its contour (rising,
  falling, arched or flat) is compared after removing its mean and allowing
  you to exaggerate or flatten it by up to a factor of two.

Each matched meow gets a 0 to 1 mark for each of the three, and its score is
their weighted geometric mean, so a meow cannot buy back a wrong note with
good timing. The phrase scores the mean over its meows (a missed meow scores
0), less a share for each extra syllable. A round is won at PASS_SCORE or
better with every meow matched and nothing extra. Each meow also gets one
verdict for the page to show: missed, unvoiced, or else whichever of
higher or lower, early or late, and shape is furthest past its tolerance,
or good when none is past it.

Every tolerance below was set from measured distributions of honest copies
and of wrong ones; see the README for the numbers, and `tests/test_copycat.py`
(runnable as a script) to measure them again.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from meow_now import cats, meows
from meow_now.analysis import CONTOUR_POINTS, Syllable, analyze
from meow_now.audio import ANALYSIS_RATE, SAMPLE_RATE, resample

# ---------- phrases ----------

MIN_LENGTH = 1
MAX_LENGTH = 12
LEAD_S = 0.35  # silence before the first meow
TAIL_S = 0.4  # and after the last
DURATION_RANGE = (0.26, 0.52)  # seconds, per meow
GAPS = (0.2, 0.2, 0.55)  # seconds of silence after a meow: mostly quick, now and then a beat
NOTES = (-7, -4, 0, 3, 5, 8)  # semitones from the cat's pitch
MIN_STEP = 3  # each meow moves at least this many semitones from the last, so the tune matters
KINDS = ("meow", "meow", "meow", "mrrp", "mew")  # no yowls (too long) and no purrs (no tune)
SHAPES: dict[str, tuple[float, ...]] = {
    "plain": (),  # the synthesizer's own rise and fall
    "up": (0.84, 0.9, 1.05, 1.28),  # a question
    "down": (1.22, 1.08, 0.92, 0.84),  # a complaint
    "arch": (0.82, 1.18, 1.18, 0.82),  # a song
}
DEFAULT_PITCH_HZ = meows.DEFAULT_PITCH_HZ
# The highest note a phrase may use. Above about 1 kHz a 16 kHz recording
# has too few samples per period to track reliably, so a high cat (Biscuit)
# performs its phrases a little lower than it talks.
TOP_NOTE_HZ = 950.0
# The performing cat keeps its voice, but a growl this rough would blur the tune.
MAX_PERFORMING_ROUGHNESS = 0.3

# ---------- analysis ----------

# Cats meow at 400 to 1200 Hz, and people copying them reach for a squeaky
# falsetto, so the tracker looks well above its speech ceiling.
MAX_PITCH_HZ = 1300.0
MAX_ATTEMPT_S = 30.0
JOIN_GAP_S = 0.015  # syllables closer than this are pieces of one sound
MAX_PIECES = 3  # at most this many touching pieces may make up one meow

# ---------- scoring ----------

TEMPO_RANGE = 1.5  # your tempo may be up to this much faster or slower than the cat's
TEMPO_STEPS = 25  # tempos tried between the two ends, evenly spaced in log tempo
# Matching is looser than marking: an onset up to about 0.45 s out can still be
# matched to its meow (and called early or late); further out, it reads as a
# missed meow plus an extra syllable.
ALIGN_SIGMA_S = 0.25

# Each tolerance sits near the 98th percentile of that error over honest
# copies, so an honest meow is flagged about 2 times in 100. Each sigma is its
# tolerance / 1.25, so a meow right at the tolerance keeps about 45 % of that mark.
TIME_TOL_S = 0.1  # past this, the meow is early or late
TIME_SIGMA_S = 0.08
PITCH_TOL_ST = 2.5  # past this, the meow is higher or lower
PITCH_SIGMA_ST = 2.0
SHAPE_TOL_ST = 1.4  # past this (RMS semitones), the meow's shape is wrong
SHAPE_SIGMA_ST = 1.12
OCTAVE_SLACK_ST = 3.0  # a note within this of a whole octave out is taken as that octave
SHAPE_GAIN = (0.5, 2.0)  # you may flatten or exaggerate a contour by this much
SHAPE_TRIM = 2  # of the contour's points, this many worst-fitting ones are not counted

WEIGHTS = {"timing": 0.3, "pitch": 0.45, "shape": 0.25}
MISS_COST = 1.0  # alignment costs: a missed meow
EXTRA_COST = 0.6  # a syllable the phrase did not ask for
EXTRA_PENALTY = 0.5  # each extra syllable takes this share of one meow off the score
PASS_SCORE = 75  # this score or better, with every meow matched and none extra, wins

VERDICTS = ("good", "higher", "lower", "early", "late", "shape", "unvoiced", "missed")


@dataclass(frozen=True)
class Phrase:
    seed: int
    cat: cats.Cat | None
    meows: tuple[meows.Meow, ...]
    starts: tuple[float, ...]  # seconds, one per meow

    @property
    def length(self) -> int:
        return len(self.meows)

    @property
    def duration(self) -> float:
        return self.starts[-1] + self.meows[-1].duration + TAIL_S


def _voice(cat: cats.Cat | None) -> cats.Voice:
    """The performing voice: the cat's own, with any growl tamed so the tune stays clear."""
    if cat is None:
        return cats.Voice()
    return replace(cat.voice, roughness=min(cat.voice.roughness, MAX_PERFORMING_ROUGHNESS))


def phrase(seed: int, length: int, cat: cats.Cat | None = None) -> Phrase:
    """The first `length` meows of the phrase drawn from `seed`, for `cat` (None: a plain voice).

    Each meow is drawn from its own generator, so phrase(seed, n + 1) begins with phrase(seed, n).
    """
    if not MIN_LENGTH <= length <= MAX_LENGTH:
        raise ValueError(f"length must be {MIN_LENGTH} to {MAX_LENGTH}, not {length}")
    base = min(cat.pitch if cat else DEFAULT_PITCH_HZ, TOP_NOTE_HZ / 2 ** (max(NOTES) / 12))
    voice = _voice(cat)
    planned, starts, t, note = [], [], LEAD_S, 0
    for i in range(length):
        rng = np.random.default_rng([seed, i])
        kind = KINDS[int(rng.integers(len(KINDS)))]
        duration = float(rng.uniform(*DURATION_RANGE))
        if kind == "mew":
            duration *= 0.75  # a mew is short and sweet
        duration = round(duration, 3)
        steps = [x for x in NOTES if abs(x - note) >= MIN_STEP]
        note = 0 if i == 0 else steps[int(rng.integers(len(steps)))]
        shape = list(SHAPES)[int(rng.integers(len(SHAPES)))]
        gap = GAPS[int(rng.integers(len(GAPS)))]
        planned.append(
            voice.meow(
                pitch=round(base * 2 ** (note / 12), 1),
                duration=duration,
                kind=kind,
                contour=SHAPES[shape],
            )
        )
        starts.append(round(t, 3))
        t += duration + gap
    return Phrase(seed=seed, cat=cat, meows=tuple(planned), starts=tuple(starts))


def render(p: Phrase) -> np.ndarray:
    """The phrase as audio at SAMPLE_RATE."""
    clips = [
        (start, meows.synthesize(m, SAMPLE_RATE, seed=p.seed * 1000 + i))
        for i, (start, m) in enumerate(zip(p.starts, p.meows, strict=True))
    ]
    return meows.place(clips, p.duration, SAMPLE_RATE)


def target_track(m: meows.Meow) -> np.ndarray:
    """The pitch the cat aims for through one meow, in Hz, at CONTOUR_POINTS even steps."""
    return m.pitch * meows.pitch_shape(m.kind, m.contour, np.linspace(0, 1, CONTOUR_POINTS))


def target_pitch(m: meows.Meow) -> float:
    """The median pitch of one meow as heard, which is what the analysis reports for a syllable."""
    x = np.linspace(0, 1, 101)
    return float(np.median(m.pitch * meows.pitch_shape(m.kind, m.contour, x)))


def target_contour(m: meows.Meow) -> tuple[float, ...]:
    """One meow's contour the way the analysis reports a syllable's: pitch over its median."""
    return tuple(float(v) for v in target_track(m) / target_pitch(m))


# ---------- scoring ----------


@dataclass(frozen=True)
class MeowScore:
    verdict: str  # one of VERDICTS
    score: float  # 0..1
    onset_error: float | None = None  # seconds, positive is late, in the cat's tempo
    pitch_error: float | None = None  # semitones, positive is higher, after transposition
    shape_error: float | None = None  # RMS semitones between contours
    # What was heard, mapped onto the phrase's timeline and into the cat's register.
    heard_start: float | None = None
    heard_duration: float | None = None
    heard_pitch: float | None = None
    heard_contour: tuple[float, ...] = ()


@dataclass(frozen=True)
class Score:
    score: int  # 0..100
    passed: bool
    meows: tuple[MeowScore, ...]  # one per meow of the phrase
    extra: int  # syllables you made that the phrase did not ask for
    heard: int  # syllables heard in all
    tempo: float  # your tempo relative to the cat's, from the timing fit
    transpose: float  # semitones from the cat's register to yours


@dataclass(frozen=True)
class _Unit:
    """One or more touching syllables, taken together as one meow."""

    start: float
    duration: float
    pitch: float | None
    contour: tuple[float, ...]


def _units(syls: tuple[Syllable, ...]) -> dict[tuple[int, int], _Unit]:
    """Every run of 1 to MAX_PIECES touching syllables, keyed by [first, last)."""
    out: dict[tuple[int, int], _Unit] = {}
    for j in range(len(syls)):
        for k in range(j + 1, min(j + MAX_PIECES, len(syls)) + 1):
            if k - j > 1:
                prev, nxt = syls[k - 2], syls[k - 1]
                if nxt.start - (prev.start + prev.duration) > JOIN_GAP_S:
                    break
            out[(j, k)] = _merge(syls[j:k])
    return out


def _merge(parts: tuple[Syllable, ...]) -> _Unit:
    """Touching syllables as one: from the first start to the last end, with one contour."""
    if len(parts) == 1:
        s = parts[0]
        return _Unit(s.start, s.duration, s.pitch, s.contour)
    end = parts[-1].start + parts[-1].duration
    voiced = [s for s in parts if s.pitch and s.contour]
    if not voiced:
        return _Unit(parts[0].start, end - parts[0].start, None, ())
    # Rebuild one track in Hz from the pieces, then resample it as one contour.
    track = np.concatenate(
        [s.pitch * np.repeat(s.contour, max(1, round(s.duration * 100))) for s in voiced]
    )
    points = np.interp(np.linspace(0, len(track) - 1, CONTOUR_POINTS), np.arange(len(track)), track)
    median = float(np.median(track))
    return _Unit(
        parts[0].start,
        end - parts[0].start,
        median,
        tuple(float(np.clip(p / median, 0.5, 2.0)) for p in points),
    )


def _semitones(ratio) -> np.ndarray:
    return 12.0 * np.log2(np.asarray(ratio, dtype=np.float64))


def _octave_forgive(error: float) -> float:
    """A note a whole octave (or two) out, give or take OCTAVE_SLACK_ST, counts as that octave."""
    octaves = round(error / 12.0)
    if octaves and abs(error - 12.0 * octaves) <= OCTAVE_SLACK_ST:
        return error - 12.0 * octaves
    return error


def _shape_error(heard: tuple[float, ...], target: tuple[float, ...]) -> float:
    """RMS semitones between two contours, after removing each one's mean and the best gain.

    The tracker is least sure at a syllable's edges and now and then slips an
    octave on a frame, so a point near a whole octave out is folded back, and
    the SHAPE_TRIM worst-fitting points are left out of the RMS.
    """
    h = np.array([_octave_forgive(x) for x in _semitones(heard)])
    t = _semitones(target)
    h, t = h - h.mean(), t - t.mean()
    tt = float(t @ t)
    gain = float(np.clip((h @ t) / tt, *SHAPE_GAIN)) if tt > 1e-9 else 1.0
    residual = np.sort((h - gain * t) ** 2)[: len(t) - SHAPE_TRIM]
    return float(np.sqrt(np.mean(residual)))


def _align(
    onsets: np.ndarray, units: dict[tuple[int, int], _Unit], count: int
) -> tuple[float, float, float, dict[int, tuple[int, int]]]:
    """The cheapest monotone matching of meows to syllable runs, over tempos and offsets.

    Returns (cost, tempo, offset, matches), where matches maps a meow index to
    its (first, last) syllable run. Every candidate tempo and offset is scored
    at once, in a dynamic program vectorized over the candidates.
    """
    n = len(onsets)
    starts = np.array([units[(j, j + 1)].start for j in range(count)])
    tempos = np.exp(np.linspace(-np.log(TEMPO_RANGE), np.log(TEMPO_RANGE), TEMPO_STEPS))
    # Offsets that put some syllable exactly on one of the first few meows.
    anchors = range(min(n, 3))
    a = np.repeat(tempos, len(anchors) * count)
    b = np.concatenate(
        [starts[None, :] - tempo * onsets[list(anchors), None] for tempo in tempos]
    ).ravel()
    return _dp(onsets, units, count, a, b)


def _cell(
    cost: list[list[np.ndarray]],
    i: int,
    j: int,
    onsets: np.ndarray,
    units: dict[tuple[int, int], _Unit],
    a: np.ndarray,
    b: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """The cheapest way, per candidate, to align i meows with j syllables, and how it was done.

    How is -1 for a missed meow, -2 for an extra syllable, or k >= 0 for meow
    i - 1 matched to syllables [k, j).
    """
    best = np.full(len(a), np.inf)
    how = np.zeros(len(a), dtype=np.int64)

    def offer(candidate: np.ndarray, label: int) -> None:
        nonlocal best, how
        take = candidate < best
        best = np.where(take, candidate, best)
        how = np.where(take, label, how)

    if i > 0:
        offer(cost[i - 1][j] + MISS_COST, -1)
    if j > 0:
        offer(cost[i][j - 1] + EXTRA_COST, -2)
    limit = MISS_COST + EXTRA_COST  # a match dearer than a miss plus an extra is never taken
    for k in range(max(0, j - MAX_PIECES), j) if i > 0 else ():
        unit = units.get((k, j))
        if unit is not None:
            late = (unit.start - b) / a - onsets[i - 1]
            offer(cost[i - 1][k] + np.minimum((late / ALIGN_SIGMA_S) ** 2 / 2.0, limit), k)
    return best, how


def _traceback(back: list[list[np.ndarray]], n: int, count: int, pick: int) -> dict:
    """Follow candidate `pick` back from (n, count): which syllable run each meow matched."""
    matches: dict[int, tuple[int, int]] = {}
    i, j = n, count
    while i > 0 or j > 0:
        how = int(back[i][j][pick])
        if how == -1:
            i -= 1
        elif how == -2:
            j -= 1
        else:
            matches[i - 1] = (how, j)
            i, j = i - 1, how
    return matches


def _dp(
    onsets: np.ndarray,
    units: dict[tuple[int, int], _Unit],
    count: int,
    a: np.ndarray,
    b: np.ndarray,
) -> tuple[float, float, float, dict[int, tuple[int, int]]]:
    """Align at each candidate tempo a[c] and offset b[c] (syllable time = a * cat time + b)."""
    n = len(onsets)
    cost = [[np.zeros(len(a))] * (count + 1) for _ in range(n + 1)]
    back = [[np.zeros(len(a), dtype=np.int64)] * (count + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        for j in range(count + 1):
            if i or j:
                cost[i][j], back[i][j] = _cell(cost, i, j, onsets, units, a, b)
    total = cost[n][count]
    # Cheapest first; among equals, the tempo nearest the cat's, then the earliest offset.
    pick = int(np.lexsort((b, np.abs(np.log(a)), np.round(total, 9)))[0])
    return float(total[pick]), float(a[pick]), float(b[pick]), _traceback(back, n, count, pick)


def _refit(
    onsets: np.ndarray,
    units: dict[tuple[int, int], _Unit],
    matches: dict[int, tuple[int, int]],
    tempo: float,
    offset: float,
) -> tuple[float, float]:
    """Least-squares tempo and offset through the matched onsets, tempo kept in range."""
    if len(matches) < 2:
        if len(matches) == 1:
            ((i, run),) = matches.items()
            return tempo, units[run].start - tempo * onsets[i]
        return tempo, offset
    x = np.array([onsets[i] for i in matches])
    y = np.array([units[run].start for run in matches.values()])
    slope = float(np.clip(np.polyfit(x, y, 1)[0], 1 / TEMPO_RANGE, TEMPO_RANGE))
    return slope, float(np.mean(y - slope * x))


def _judge(
    m: meows.Meow,
    unit: _Unit,
    onset: float,
    fit: tuple[float, float],
    diff: float | None,
    transpose: float,
) -> MeowScore:
    """Mark one matched meow and name its worst problem.

    `fit` is the (tempo, offset) of the timing fit, and `diff` the semitones from
    the meow's target pitch to what was heard, None if nothing was heard in tune.
    """
    tempo, offset = fit
    late = (unit.start - offset) / tempo - onset
    heard = {
        "heard_start": round((unit.start - offset) / tempo, 3),
        "heard_duration": round(unit.duration / tempo, 3),
    }
    if diff is None:
        # Whispered, or too rough to track: on time, maybe, but no tune to judge.
        return MeowScore("unvoiced", 0.0, onset_error=late, **heard)
    pitch_error = _octave_forgive(diff - transpose)
    shape_error = _shape_error(unit.contour, target_contour(m))
    marks = {
        "timing": np.exp(-0.5 * (late / TIME_SIGMA_S) ** 2),
        "pitch": np.exp(-0.5 * (pitch_error / PITCH_SIGMA_ST) ** 2),
        "shape": np.exp(-0.5 * (shape_error / SHAPE_SIGMA_ST) ** 2),
    }
    mark = float(np.prod([marks[k] ** w for k, w in WEIGHTS.items()]))
    # Name the worst problem, each measured against its own tolerance.
    worst, verdict = max(
        (abs(pitch_error) / PITCH_TOL_ST, "higher" if pitch_error > 0 else "lower"),
        (abs(late) / TIME_TOL_S, "late" if late > 0 else "early"),
        (shape_error / SHAPE_TOL_ST, "shape"),
    )
    return MeowScore(
        verdict if worst > 1.0 else "good",
        mark,
        onset_error=late,
        pitch_error=pitch_error,
        shape_error=shape_error,
        heard_pitch=round(target_pitch(m) * 2 ** (pitch_error / 12), 1),
        heard_contour=unit.contour,
        **heard,
    )


def score(p: Phrase, samples: np.ndarray, rate: int) -> Score:
    """Score a recording of someone copying phrase `p`."""
    samples = resample(samples[: int(MAX_ATTEMPT_S * rate)], rate, ANALYSIS_RATE)
    syls = analyze(samples, ANALYSIS_RATE, max_pitch=MAX_PITCH_HZ).syllables
    n = p.length
    if not syls:
        missed = tuple(MeowScore("missed", 0.0) for _ in range(n))
        return Score(0, False, missed, extra=0, heard=0, tempo=1.0, transpose=0.0)

    onsets = np.array(p.starts)
    units = _units(syls)
    _, tempo, offset, matches = _align(onsets, units, len(syls))
    tempo, offset = _refit(onsets, units, matches, tempo, offset)
    # One more pass at the refined fit, so every residual is measured against it.
    _, tempo, offset, matches = _dp(onsets, units, len(syls), np.array([tempo]), np.array([offset]))

    used = sum(k - j for j, k in matches.values())
    extra = len(syls) - used
    targets = [target_pitch(m) for m in p.meows]

    # The transposition from the cat's register to yours, robust to a meow or two way off.
    diffs = {
        i: float(_semitones(units[run].pitch / targets[i]))
        for i, run in matches.items()
        if units[run].pitch
    }
    transpose = float(np.median(list(diffs.values()))) if diffs else 0.0

    results = [
        _judge(m, units[matches[i]], onsets[i], (tempo, offset), diffs.get(i), transpose)
        if i in matches
        else MeowScore("missed", 0.0)
        for i, m in enumerate(p.meows)
    ]

    total = sum(r.score for r in results) / n
    total *= max(0.0, 1.0 - EXTRA_PENALTY * extra / n)
    final = int(round(100 * total))
    return Score(
        score=final,
        passed=final >= PASS_SCORE and extra == 0 and all(r.verdict != "missed" for r in results),
        meows=tuple(results),
        extra=extra,
        heard=len(syls),
        tempo=tempo,
        transpose=transpose,
    )
