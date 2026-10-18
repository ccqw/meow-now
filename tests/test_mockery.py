import numpy as np
import pytest
from conftest import (
    RATE,
    cat_pitch_track,
    ending_with,
    gliding_burst,
    spectral_centroid,
    speech_like,
)

from meow_now.analysis import Syllable
from meow_now.audio import SAMPLE_RATE
from meow_now.cats import CATS, Voice
from meow_now.mockery import (
    BORED_AFTER_S,
    CAT_CENTER_HZ,
    CAT_MAX_HZ,
    CAT_MIN_HZ,
    MEH_PAUSE_S,
    REACTIONS,
    boredom,
    cat_pitch,
    clock,
    ends_rising,
    mock,
)


def test_one_meow_per_syllable_in_time():
    x = speech_like([120, 150, 180, 140, 110])
    result = mock(x, RATE)
    assert len(result.meows) == 5
    assert len(result.audio) / SAMPLE_RATE >= len(x) / RATE
    # Sound where the syllables were, quiet in the gaps between them.
    for syl in result.analysis.syllables:
        i = int((syl.start + syl.duration / 2) * SAMPLE_RATE)
        assert np.max(np.abs(result.audio[i - 400 : i + 400])) > 0.1
    gap_mid = int((0.15 + 0.25 + 0.07) * SAMPLE_RATE)
    assert np.max(np.abs(result.audio[gap_mid - 100 : gap_mid + 100])) < 0.05


def test_higher_syllables_get_higher_meows():
    result = mock(speech_like([110, 220]), RATE)
    low, high = result.meows
    assert high.pitch > low.pitch


def test_cat_pitch_mapping():
    assert cat_pitch(150, 150) == CAT_CENTER_HZ
    assert cat_pitch(10_000, 100) == CAT_MAX_HZ
    assert cat_pitch(10, 100) == CAT_MIN_HZ
    assert cat_pitch(None, 150) > CAT_CENTER_HZ


def test_input_is_capped_at_sixty_seconds():
    x = np.tile(speech_like([150]), 200)  # about 110 seconds
    result = mock(x, RATE)
    assert result.analysis.duration <= 60.0
    assert max(s.start for s in result.analysis.syllables) < 60.0


def _question():
    return ending_with(speech_like([150, 160, 140]), gliding_burst(140, 230, 0.4))


def _long_talk():
    return np.tile(speech_like([150, 170]), 70)  # about 66 seconds, capped at 60


def test_plain_speech_gets_no_reaction():
    result = mock(speech_like([120, 150, 180, 140, 110]), RATE)
    assert result.reaction == "none"
    assert result.caption is None
    assert result.extras == ()


def test_silence_gets_the_silent_treatment():
    result = mock(np.zeros(RATE, dtype=np.float32), RATE)
    assert result.meows == ()
    assert result.reaction == "silent"
    assert result.caption == "The cat waited. So did you."
    ((start, mrrp),) = result.extras
    assert mrrp.kind == "mrrp"
    assert mrrp.contour[-1] > mrrp.contour[0]  # a questioning lilt
    # Your silence comes back as silence, and the mrrp only after it.
    assert start >= 1.0
    assert np.max(np.abs(result.audio[: int(start * SAMPLE_RATE)])) == 0
    assert np.max(np.abs(result.audio[int(start * SAMPLE_RATE) :])) > 0.1


def test_rising_ending_gets_a_rhetorical_meh():
    result = mock(_question(), RATE, seed=3)
    assert len(result.meows) == 4  # the question is mirrored in full
    assert result.reaction == "question"
    assert result.caption
    ((start, meh),) = result.extras
    last = result.analysis.syllables[-1]
    assert start >= last.start + result.meows[-1].duration + MEH_PAUSE_S - 1e-9
    assert meh.contour[0] >= meh.contour[1] > meh.contour[-1]  # flat, then falling
    i = int(start * SAMPLE_RATE)
    assert np.max(np.abs(result.audio[i - 400 : i])) < 0.05  # the pause before it
    assert np.max(np.abs(result.audio[i : i + int(meh.duration * SAMPLE_RATE)])) > 0.1


def test_falling_ending_is_not_a_question():
    x = ending_with(speech_like([150, 160, 140]), gliding_burst(180, 120, 0.4))
    assert mock(x, RATE).reaction == "none"


def test_ends_rising():
    def syl(contour, duration=0.3):
        return Syllable(start=0.0, duration=duration, loudness=1.0, pitch=150.0, contour=contour)

    assert ends_rising(syl((0.9, 0.92, 0.95, 1.0, 1.05, 1.1)))
    assert not ends_rising(syl((1.1, 1.05, 1.0, 0.95, 0.92, 0.9)))
    assert not ends_rising(syl((1.0, 1.0, 1.01, 1.02)))
    assert not ends_rising(syl(()))  # unvoiced
    # A short beep whose last pitch frame jitters upward is not a question.
    beep = (1.0, 1.0, 1.0, 1.0, 1.0, 1.15, 1.29, 1.21)
    assert not ends_rising(syl(beep, duration=0.08))
    assert not ends_rising(syl((1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.3)))


def test_long_talk_bores_the_cat():
    result = mock(_long_talk(), RATE, seed=1)
    assert result.reaction == "bored"
    pairs = list(zip(result.analysis.syllables, result.meows, strict=True))
    early = [m for s, m in pairs if s.start < BORED_AFTER_S]
    late = [m for s, m in pairs if s.start > BORED_AFTER_S + 10]
    assert early and late
    assert max(m.duration for m in late) < min(m.duration for m in early)
    assert max(m.loudness for m in late) < min(m.loudness for m in early)
    # The caption names the moment the cat checked out.
    stopped = next(s.start for s in result.analysis.syllables if s.start > BORED_AFTER_S)
    assert result.caption == f"The cat stopped listening at {clock(stopped)}."
    assert result.caption.startswith("The cat stopped listening at 0:4")
    # And it ends on one long, falling yawn after everything else.
    ((start, yawn),) = result.extras
    assert start > max(s.start + m.duration for s, m in pairs)
    assert yawn.duration > 1.0
    assert yawn.contour[0] > yawn.contour[-1]


def test_boredom_and_clock():
    assert boredom(0.0) == boredom(BORED_AFTER_S) == 1.0
    assert 0 < boredom(60.0) < boredom(45.0) < 1.0
    assert clock(43.9) == "0:43"
    assert clock(65.0) == "1:05"


@pytest.mark.parametrize(
    ("make", "reaction"),
    [
        (lambda: speech_like([120, 160, 200]), "none"),
        (lambda: np.zeros(RATE, dtype=np.float32), "silent"),
        (_question, "question"),
        (_long_talk, "bored"),
    ],
)
def test_reactions_are_deterministic_per_seed(make, reaction):
    assert reaction in REACTIONS
    x = make()
    a, b = mock(x, RATE, seed=7), mock(x, RATE, seed=7)
    assert a.reaction == b.reaction == reaction
    assert a.caption == b.caption
    assert a.extras == b.extras
    assert np.array_equal(a.audio, b.audio)


def test_high_sample_rate_input_is_analyzed_the_same():
    from meow_now.audio import resample

    x16 = speech_like([120, 160, 200])
    x48 = resample(x16, RATE, 48_000)
    a, b = mock(x16, RATE), mock(x48, 48_000)
    assert len(a.meows) == len(b.meows) == 3
    assert abs(b.analysis.duration - a.analysis.duration) < 0.01


def test_mockery_meows_follow_the_speakers_pitch():
    pitches = [110, 140, 175, 220]
    result = mock(speech_like(pitches), RATE, seed=5)
    track = cat_pitch_track(result.audio, SAMPLE_RATE)
    heard = []
    for syl in result.analysis.syllables:
        frames = track[int(syl.start / 0.01) : int((syl.start + syl.duration) / 0.01)]
        heard.append(float(np.median(frames[frames > 0])))
    planned = [m.pitch for m in result.meows]
    # Measured from the rendered audio: each meow is higher than the last, and
    # sits close to where it was planned (a meow's pitch arc peaks at `pitch`).
    assert heard == sorted(heard)
    for got, want in zip(heard, planned, strict=True):
        assert 0.75 * want < got < 1.05 * want


def _heard_pitch(result):
    track = cat_pitch_track(result.audio, SAMPLE_RATE)
    return float(np.median(track[track > 0]))


def test_mockery_is_in_the_given_cats_voice():
    x = speech_like([120, 150, 180, 140, 110, 160])
    grumpy = mock(x, RATE, seed=4, cat=CATS["grumpy"])
    anxious = mock(x, RATE, seed=4, cat=CATS["anxious"])
    assert len(grumpy.meows) == len(anxious.meows) == 6
    # Planned around each cat's own pitch, in each cat's own voice.
    for result, cat in ((grumpy, CATS["grumpy"]), (anxious, CATS["anxious"])):
        assert np.median([m.pitch for m in result.meows]) == pytest.approx(cat.pitch, rel=0.25)
        for m in result.meows:
            assert (m.formant_scale, m.breathiness, m.roughness) == (
                cat.voice.formant_scale,
                cat.voice.breathiness,
                cat.voice.roughness,
            )
    # And it is heard that way: the big grumpy cat lower and darker than the small anxious one.
    assert _heard_pitch(grumpy) < 0.75 * _heard_pitch(anxious)
    assert spectral_centroid(grumpy.audio, SAMPLE_RATE) < spectral_centroid(
        anxious.audio, SAMPLE_RATE
    )


@pytest.mark.parametrize("make", [lambda: speech_like([120, 160, 200]), _question])
def test_mockery_in_a_cats_voice_is_deterministic_per_seed(make):
    x = make()
    a = mock(x, RATE, seed=9, cat=CATS["diva"])
    b = mock(x, RATE, seed=9, cat=CATS["diva"])
    assert a.meows == b.meows and a.extras == b.extras
    assert np.array_equal(a.audio, b.audio)
    assert not np.array_equal(a.audio, mock(x, RATE, seed=9).audio)


def test_mockery_reactions_use_the_cats_voice():
    result = mock(_question(), RATE, seed=2, cat=CATS["grumpy"])
    assert result.reaction == "question"
    ((_, meh),) = result.extras
    assert meh.roughness == CATS["grumpy"].voice.roughness
    assert meh.pitch == pytest.approx(CATS["grumpy"].pitch * 0.9)


def test_mockery_without_a_cat_is_the_plain_voice():
    plain = Voice()
    result = mock(speech_like([120, 160, 200]), RATE, seed=1)
    for m in result.meows:
        assert (m.formant_scale, m.breathiness, m.roughness) == (
            plain.formant_scale,
            plain.breathiness,
            plain.roughness,
        )
    assert np.median([m.pitch for m in result.meows]) == pytest.approx(CAT_CENTER_HZ, rel=0.25)
