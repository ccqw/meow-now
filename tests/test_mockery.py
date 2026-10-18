import numpy as np
from conftest import RATE, speech_like

from meow_now.audio import SAMPLE_RATE
from meow_now.mockery import CAT_CENTER_HZ, CAT_MAX_HZ, CAT_MIN_HZ, cat_pitch, mock


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


def test_silence_gives_no_meows():
    result = mock(np.zeros(RATE, dtype=np.float32), RATE)
    assert result.meows == ()
    assert np.max(np.abs(result.audio)) == 0


def test_high_sample_rate_input_is_analyzed_the_same():
    from meow_now.audio import resample

    x16 = speech_like([120, 160, 200])
    x48 = resample(x16, RATE, 48_000)
    a, b = mock(x16, RATE), mock(x48, 48_000)
    assert len(a.meows) == len(b.meows) == 3
    assert abs(b.analysis.duration - a.analysis.duration) < 0.01
