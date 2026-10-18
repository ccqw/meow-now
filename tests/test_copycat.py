"""Copycat phrases and scoring.

The scorer is tested on copies whose quality is known by construction: the
phrase's own plan re-synthesized (a perfect copy), the same plan shifted in
pitch or tempo (still a good copy), and the same plan with one thing broken
(a meow missing, one too many, a wrong note, a wrong shape, the wrong timing).

`calibration` below simulates people copying phrases, honestly and not, and
measures how the scores fall. Run this file as a script to print the full
tables the thresholds in meow_now/copycat.py were tuned from:

    python tests/test_copycat.py [copies-per-case]
"""

from __future__ import annotations

import sys
from dataclasses import replace

import numpy as np
import pytest

from meow_now import cats, copycat, meows
from meow_now.analysis import CONTOUR_POINTS, Syllable
from meow_now.audio import SAMPLE_RATE

CATS = [None, *cats.CATS.values()]
LENGTHS = (2, 3, 4, 6, 8)


# ---------- copies of known quality ----------


def with_meows(p: copycat.Phrase, **changes) -> copycat.Phrase:
    """A copy of the plan with fields changed: `seed`, `starts`, or `meows` (a function of each)."""
    edit = changes.pop("meows", None)
    ms = tuple(edit(i, m) for i, m in enumerate(p.meows)) if edit else p.meows
    return replace(p, meows=ms, **changes)


def performed(p: copycat.Phrase, seed_shift: int = 17) -> tuple[np.ndarray, int]:
    """The plan sung again, with fresh jitter, vibrato and breath (a new synthesis seed)."""
    return copycat.render(replace(p, seed=p.seed + seed_shift)), SAMPLE_RATE


def transposed(p: copycat.Phrase, semitones: float) -> copycat.Phrase:
    return with_meows(p, meows=lambda i, m: replace(m, pitch=m.pitch * 2 ** (semitones / 12)))


def retimed(p: copycat.Phrase, tempo: float, lead: float) -> tuple[np.ndarray, int]:
    """The plan at another tempo, starting `lead` seconds later: same rhythm, other speed."""
    q = with_meows(
        p,
        starts=tuple(lead + s * tempo for s in p.starts),
        meows=lambda i, m: replace(m, duration=m.duration * tempo),
    )
    return performed(q)


def without(p: copycat.Phrase, drop: int) -> tuple[np.ndarray, int]:
    keep = [i for i in range(p.length) if i != drop]
    q = replace(p, meows=tuple(p.meows[i] for i in keep), starts=tuple(p.starts[i] for i in keep))
    return performed(q)


def with_extra(p: copycat.Phrase) -> tuple[np.ndarray, int]:
    last = p.meows[-1]
    q = replace(
        p,
        meows=(*p.meows, replace(last, kind="meow", duration=0.3)),
        starts=(*p.starts, p.starts[-1] + last.duration + 0.3),
    )
    return performed(q)


def person(
    p: copycat.Phrase,
    rng: np.random.Generator,
    *,
    jitter: float = 0.04,
    pitch_sd: float = 1.0,
    flat: bool = False,
    room: bool = False,
) -> tuple[np.ndarray, int]:
    """Someone going "meow" back: a human-sized vocal tract, an octave or so below the cat.

    They start whenever they like, copy a little faster or slower, land each
    onset within `jitter` seconds and each note within `pitch_sd` semitones.
    `flat` sings every meow on one note with no contour; `room` adds the
    reverb of a phone at arm's length (direct sound about 10 dB over the room)
    and some microphone hiss.
    """
    transpose = rng.uniform(-19, -9)
    tempo = rng.uniform(0.8, 1.25)
    lead = rng.uniform(0.1, 0.8)
    home = p.meows[0].pitch
    clips = []
    for start, m in zip(p.starts, p.meows, strict=True):
        note = home if flat else m.pitch
        meow = meows.Meow(
            pitch=note * 2 ** ((transpose + rng.normal(0, pitch_sd)) / 12),
            duration=m.duration * tempo * rng.uniform(0.8, 1.1),
            kind="mew" if flat else ("meow" if m.kind == "mrrp" else m.kind),
            contour=(1.0, 1.0) if flat else m.contour,
            formant_scale=0.62,
            breathiness=0.3,
            roughness=0.05,
        )
        at = max(0.0, lead + start * tempo + rng.normal(0, jitter))
        clips.append((at, meows.synthesize(meow, SAMPLE_RATE, seed=int(rng.integers(1 << 20)))))
    x = meows.place(clips, lead + p.duration * tempo + 0.5, SAMPLE_RATE).astype(np.float64)
    if room:
        t = np.arange(int(0.35 * SAMPLE_RATE)) / SAMPLE_RATE
        ir = rng.standard_normal(len(t)) * np.exp(-6.9 * t / 0.35) * 0.012
        ir[0] = 1.0
        x = np.convolve(x, ir)[: len(x)]
        x /= np.max(np.abs(x))
    x += rng.standard_normal(len(x)) * 0.003
    return x.astype(np.float32), SAMPLE_RATE


def other_tune(p: copycat.Phrase, rng: np.random.Generator) -> copycat.Phrase:
    """The same rhythm and shapes, but every meow after the first on another note."""
    home = p.meows[0].pitch

    def move(i, m):
        if i == 0:
            return m
        note = 12 * np.log2(m.pitch / home)
        choices = [x for x in copycat.NOTES if abs(x - note) >= copycat.MIN_STEP]
        return replace(m, pitch=home * 2 ** (choices[int(rng.integers(len(choices)))] / 12))

    return with_meows(p, meows=move)


def other_shapes(p: copycat.Phrase, rng: np.random.Generator) -> copycat.Phrase:
    """The same tune and rhythm, but every meow bent into a different shape."""

    def bend(i, m):
        others = [c for c in copycat.SHAPES.values() if c != m.contour]
        return replace(m, contour=others[int(rng.integers(len(others)))])

    return with_meows(p, meows=bend)


# ---------- phrases ----------


def test_phrases_are_deterministic_and_grow_by_one():
    for cat in CATS:
        a, b = copycat.phrase(7, 5, cat), copycat.phrase(7, 5, cat)
        assert a == b
        assert np.array_equal(copycat.render(a), copycat.render(b))
        longer = copycat.phrase(7, 6, cat)
        assert longer.meows[:5] == a.meows and longer.starts[:5] == a.starts
    assert copycat.phrase(7, 5) != copycat.phrase(8, 5)


def test_phrases_are_playable_tunes():
    for seed in range(40):
        for cat in CATS:
            p = copycat.phrase(seed, copycat.MAX_LENGTH, cat)
            assert p.starts[0] == copycat.LEAD_S
            # Meows never overlap, and every gap is one of the phrase's beats.
            for i in range(p.length - 1):
                s0, m0, s1 = p.starts[i], p.meows[i], p.starts[i + 1]
                assert s1 - (s0 + m0.duration) >= min(copycat.GAPS) - 1e-3
            assert {m.kind for m in p.meows} <= {"meow", "mrrp", "mew"}
            notes = 12 * np.log2(np.array([m.pitch for m in p.meows]) / p.meows[0].pitch)
            assert np.all(np.abs(np.diff(notes)) >= copycat.MIN_STEP - 0.01)
            assert max(m.pitch for m in p.meows) <= copycat.TOP_NOTE_HZ + 0.1


def test_the_performing_cat_keeps_its_voice_but_not_its_growl():
    grumpy = cats.CATS["grumpy"]
    m = copycat.phrase(3, 2, grumpy).meows[0]
    assert m.formant_scale == grumpy.voice.formant_scale
    assert m.roughness == copycat.MAX_PERFORMING_ROUGHNESS < grumpy.voice.roughness
    assert copycat.phrase(3, 2, grumpy).meows[0].pitch == grumpy.pitch


def test_length_is_bounded():
    with pytest.raises(ValueError):
        copycat.phrase(1, 0)
    with pytest.raises(ValueError):
        copycat.phrase(1, copycat.MAX_LENGTH + 1)


def test_target_contour_is_what_the_synth_aims_for():
    m = meows.Meow(pitch=600, duration=0.4, kind="meow", contour=copycat.SHAPES["up"])
    contour = copycat.target_contour(m)
    assert len(contour) == CONTOUR_POINTS
    assert contour[-1] > contour[0]  # an "up" meow ends higher than it starts
    assert np.median(np.array(copycat.target_track(m))) == pytest.approx(
        copycat.target_pitch(m), rel=0.02
    )


# ---------- scoring known copies ----------


@pytest.mark.parametrize("cat", CATS, ids=lambda c: c.id if c else "plain")
@pytest.mark.parametrize("length", LENGTHS)
def test_a_perfect_copy_scores_90_or_more(cat, length):
    p = copycat.phrase(100 + length, length, cat)
    result = copycat.score(p, *performed(p))
    assert result.score >= 90
    assert result.passed
    assert result.extra == 0
    assert [m.verdict for m in result.meows] == ["good"] * length
    assert result.tempo == pytest.approx(1.0, abs=0.02)
    assert abs(result.transpose) < 1.0


def test_a_copy_in_another_cats_voice_scores_90_or_more():
    for i, (cat, singer) in enumerate(zip(CATS, CATS[2:] + CATS[:2], strict=True)):
        p = copycat.phrase(200 + i, 5, cat)
        voice = copycat._voice(singer)

        def sing(_, m, voice=voice):
            return voice.meow(pitch=m.pitch, duration=m.duration, kind=m.kind, contour=m.contour)

        assert copycat.score(p, *performed(with_meows(p, meows=sing))).score >= 90


@pytest.mark.parametrize("semitones", [-12, -7, -5, 2])
def test_a_shifted_pitch_copy_still_scores_well(semitones):
    # Same tune and shapes in another register: what most people do, since few sing like a cat.
    for seed in range(5):
        p = copycat.phrase(300 + seed, 4, CATS[seed % len(CATS)])
        result = copycat.score(p, *performed(transposed(p, semitones)))
        assert result.score >= 85
        assert result.passed
        assert result.transpose == pytest.approx(semitones, abs=1.0)


@pytest.mark.parametrize(("tempo", "lead"), [(0.75, 0.9), (1.3, 0.05), (1.0, 1.6)])
def test_a_global_tempo_or_offset_is_not_held_against_you(tempo, lead):
    p = copycat.phrase(42, 6, cats.CATS["diva"])
    result = copycat.score(p, *retimed(p, tempo, lead))
    assert result.score >= 90
    assert result.tempo == pytest.approx(tempo, rel=0.05)
    assert all(abs(m.onset_error) < copycat.TIME_TOL_S for m in result.meows)


def test_silence_scores_zero():
    p = copycat.phrase(5, 3)
    result = copycat.score(p, np.zeros(2 * SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)
    assert result.score == 0
    assert not result.passed
    assert [m.verdict for m in result.meows] == ["missed"] * 3
    empty = copycat.score(p, np.zeros(0, dtype=np.float32), SAMPLE_RATE)
    assert empty.score == 0 and empty.heard == 0


def test_noise_does_not_pass():
    p = copycat.phrase(5, 4)
    noise = np.random.default_rng(0).standard_normal(3 * SAMPLE_RATE).astype(np.float32) * 0.3
    result = copycat.score(p, noise, SAMPLE_RATE)
    assert not result.passed
    assert result.score < 30


@pytest.mark.parametrize("length", [2, 4, 6])
def test_a_missing_meow_is_penalized_and_fails(length):
    p = copycat.phrase(600 + length, length, cats.CATS["wise"])
    perfect = copycat.score(p, *performed(p)).score
    drop = length // 2
    result = copycat.score(p, *without(p, drop))
    # With two meows and one sung, either could be the one you missed: the offset is free.
    assert [m.verdict for m in result.meows].count("missed") == 1
    if length > 2:
        assert result.meows[drop].verdict == "missed"
    assert result.score <= perfect - 100 / length + 5
    assert not result.passed


@pytest.mark.parametrize("length", [2, 4, 6])
def test_an_extra_meow_is_penalized_and_fails(length):
    p = copycat.phrase(700 + length, length, cats.CATS["anxious"])
    perfect = copycat.score(p, *performed(p)).score
    result = copycat.score(p, *with_extra(p))
    assert result.extra == 1
    assert result.score < perfect
    assert not result.passed


def test_a_wrong_note_is_called_higher_or_lower():
    p = copycat.phrase(11, 5, cats.CATS["diva"])
    pitches = [m.pitch for m in p.meows]
    # Raise the lowest note and lower the highest, so both stay in the tracker's range.
    for i, semitones, verdict in [
        (int(np.argmin(pitches)), 5, "higher"),
        (int(np.argmax(pitches)), -5, "lower"),
    ]:
        q = with_meows(
            p,
            meows=lambda j, m, i=i, s=semitones: (
                replace(m, pitch=m.pitch * 2 ** (s / 12)) if j == i else m
            ),
        )
        result = copycat.score(p, *performed(q))
        assert result.meows[i].verdict == verdict
        assert result.meows[i].pitch_error == pytest.approx(semitones, abs=1.5)
        assert [m.verdict for j, m in enumerate(result.meows) if j != i] == ["good"] * 4


def test_a_meow_an_octave_out_is_the_right_note():
    p = copycat.phrase(12, 4)
    q = with_meows(p, meows=lambda i, m: replace(m, pitch=m.pitch / 2) if i == 1 else m)
    result = copycat.score(p, *performed(q))
    assert result.meows[1].verdict == "good"
    assert abs(result.meows[1].pitch_error) < 1.0


def test_an_early_or_late_meow_is_called_out():
    p = copycat.phrase(13, 8, cats.CATS["wise"])
    ends = [s + m.duration for s, m in zip(p.starts, p.meows, strict=True)]
    # Move a meow into one of the phrase's long beats, so it does not run into a neighbour.
    good = [m.verdict == "good" for m in copycat.score(p, *performed(p)).meows]
    late = next(i for i in range(2, 7) if good[i] and p.starts[i + 1] - ends[i] > 0.5)
    early = next(i for i in range(2, 8) if good[i] and p.starts[i] - ends[i - 1] > 0.5)
    for i, shift, verdict in [(late, 0.22, "late"), (early, -0.22, "early")]:
        starts = list(p.starts)
        starts[i] += shift
        result = copycat.score(p, *performed(replace(p, starts=tuple(starts))))
        assert result.meows[i].verdict == verdict
        assert abs(result.meows[i].onset_error) > copycat.TIME_TOL_S
        assert result.meows[i].onset_error * shift > 0


def test_a_contour_mismatch_lowers_the_score():
    rng = np.random.default_rng(3)
    lowered, called = 0, 0
    for seed in range(10):
        p = copycat.phrase(800 + seed, 5, CATS[seed % len(CATS)])
        good = copycat.score(p, *performed(p))
        bent = copycat.score(p, *performed(other_shapes(p, rng)))
        lowered += bent.score < good.score
        called += sum(m.verdict == "shape" for m in bent.meows)
        assert bent.score <= good.score + 1
    assert lowered >= 9
    assert called >= 10  # of 50 bent meows, the clearly wrong ones are named


def test_up_against_down_is_always_the_wrong_shape():
    up = meows.Meow(pitch=600, duration=0.45, contour=copycat.SHAPES["up"])
    down = replace(up, contour=copycat.SHAPES["down"])
    p = replace(copycat.phrase(1, 3), meows=(up, up, up))
    q = replace(p, meows=(up, down, up))
    result = copycat.score(p, *performed(q))
    assert result.meows[1].verdict == "shape"
    assert result.meows[1].shape_error > copycat.SHAPE_TOL_ST


def test_only_touching_syllables_can_make_one_meow():
    def syl(start, duration):
        return Syllable(start, duration, 1.0, 300.0, (1.0,) * CONTOUR_POINTS)

    # Two pieces split at an energy valley, then a separate sound after a gap.
    units = copycat._units((syl(0.0, 0.2), syl(0.2, 0.15), syl(0.6, 0.2)))
    assert set(units) == {(0, 1), (1, 2), (2, 3), (0, 2)}
    assert units[(0, 2)].start == 0.0
    assert units[(0, 2)].duration == pytest.approx(0.35)
    # The alignment offers no match for a run that is not a unit, so the gap is never bridged.
    onsets = np.array([0.0])
    cost = [[np.zeros(1)] * 4 for _ in range(2)]
    best, how = copycat._cell(cost, 1, 3, onsets, units, np.ones(1), np.array([0.6]))
    assert how[0] == 2 and best[0] == 0.0  # only the lone third syllable is offered as a match


def test_scoring_is_deterministic():
    p = copycat.phrase(21, 4, cats.CATS["grumpy"])
    x, rate = person(p, np.random.default_rng(0))
    assert copycat.score(p, x, rate) == copycat.score(p, x, rate)


def test_heard_meows_are_mapped_onto_the_phrase():
    p = copycat.phrase(22, 4)
    result = copycat.score(p, *retimed(transposed(p, -12), 1.2, 0.5))
    for m, start, target in zip(result.meows, p.starts, p.meows, strict=True):
        assert m.heard_start == pytest.approx(start, abs=0.06)
        assert m.heard_pitch == pytest.approx(copycat.target_pitch(target), rel=0.1)
        assert len(m.heard_contour) == CONTOUR_POINTS


# ---------- calibration ----------

CASES = {
    "honest": lambda p, rng: person(p, rng),
    "honest, in a room": lambda p, rng: person(p, rng, room=True),
    "sloppy": lambda p, rng: person(p, rng, jitter=0.08, pitch_sd=1.8),
    "flat monotone": lambda p, rng: person(p, rng, flat=True, pitch_sd=0.3),
    "wrong tune": lambda p, rng: person(other_tune(p, rng), rng),
    "another phrase": lambda p, rng: person(copycat.phrase(p.seed + 5000, p.length, p.cat), rng),
    "a meow missing": lambda p, rng: without(p, int(rng.integers(p.length))),
    "a meow extra": lambda p, rng: with_extra(p),
}


def calibration(copies: int) -> dict[str, dict[str, float]]:
    """Score `copies` simulated copies for each case; return score percentiles and pass rate."""
    out = {}
    for name, make in CASES.items():
        scores, passed = [], []
        for k in range(copies):
            rng = np.random.default_rng(k)
            p = copycat.phrase(9000 + k, LENGTHS[k % len(LENGTHS)], CATS[k % len(CATS)])
            result = copycat.score(p, *make(p, rng))
            scores.append(result.score)
            passed.append(result.passed)
        p5, p50, p95 = np.percentile(scores, [5, 50, 95])
        out[name] = {"p5": p5, "median": p50, "p95": p95, "passed": float(np.mean(passed))}
    return out


def test_calibration_separates_honest_copies_from_wrong_ones():
    stats = calibration(30)
    assert stats["honest"]["passed"] >= 0.9
    assert stats["honest, in a room"]["passed"] >= 0.85
    assert stats["honest"]["p5"] >= copycat.PASS_SCORE
    for wrong in ("flat monotone", "wrong tune", "another phrase"):
        assert stats[wrong]["passed"] <= 0.25, wrong
        assert stats[wrong]["median"] < copycat.PASS_SCORE, wrong
    assert stats["a meow missing"]["passed"] == 0
    assert stats["a meow extra"]["passed"] == 0


if __name__ == "__main__":
    copies = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    print(f"{'case':20s} {'p5':>5s} {'median':>7s} {'p95':>5s} {'passed':>7s}")
    for name, s in calibration(copies).items():
        print(
            f"{name:20s} {s['p5']:5.0f} {s['median']:7.0f} {s['p95']:5.0f} "
            f"{100 * s['passed']:6.0f}%"
        )
