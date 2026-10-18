import numpy as np
import pytest
from conftest import RATE, speech_like, voiced_burst

from meow_now.analysis import analyze, frame_pitch
from meow_now.audio import ANALYSIS_RATE, SAMPLE_RATE, resample
from meow_now.meows import Meow, pitch_shape, synthesize


@pytest.mark.parametrize("pitch", [90.0, 120.0, 200.0, 310.0])
def test_pitch_of_a_steady_vowel(pitch):
    track = frame_pitch(voiced_burst(pitch, 0.5), RATE)
    voiced = track[track > 0]
    assert len(voiced) > 10
    assert np.median(voiced) == pytest.approx(pitch, rel=0.03)


def test_noise_is_not_voiced(rng):
    track = frame_pitch(rng.standard_normal(RATE).astype(np.float32) * 0.3, RATE)
    assert np.mean(track > 0) < 0.1


def test_counts_and_times_syllables():
    pitches = [120, 150, 180, 140, 110]
    result = analyze(speech_like(pitches), RATE)
    assert len(result.syllables) == len(pitches)
    starts = [s.start for s in result.syllables]
    expected = [0.15 + i * 0.40 for i in range(len(pitches))]
    assert np.allclose(starts, expected, atol=0.08)
    found = [s.pitch for s in result.syllables]
    assert np.allclose(found, pitches, rtol=0.05)


def test_one_long_run_is_split_at_its_valleys():
    # Three syllables run together, with dips but no silence between them.
    t = np.arange(int(1.2 * RATE)) / RATE
    envelope = 0.15 + 0.85 * np.sin(np.pi * 3 * t / 1.2) ** 2
    x = (np.sin(2 * np.pi * 150 * t) * envelope * 0.4).astype(np.float32)
    assert len(analyze(x, RATE).syllables) == 3


def test_silence_has_no_syllables():
    assert analyze(np.zeros(RATE, dtype=np.float32), RATE).syllables == ()
    assert analyze(np.zeros(0, dtype=np.float32), RATE).syllables == ()


def test_contour_follows_a_rising_voice():
    t = np.arange(int(0.5 * RATE)) / RATE
    f0 = np.linspace(110, 220, len(t))
    x = (np.sin(2 * np.pi * np.cumsum(f0) / RATE) * np.hanning(len(t)) * 0.4).astype(np.float32)
    (syl,) = analyze(x, RATE).syllables
    assert syl.contour[0] < 1.0 < syl.contour[-1]


def test_default_ceiling_is_unchanged_by_the_max_pitch_option():
    x = voiced_burst(200.0, 0.5)
    assert np.array_equal(frame_pitch(x, RATE), frame_pitch(x, RATE, max_pitch=500.0))


@pytest.mark.parametrize("seed", range(4))
def test_a_high_meow_is_not_tracked_an_octave_low(seed):
    # Near 1 kHz a 16 kHz recording has about 16 samples per period, and the true
    # autocorrelation peak can fall between samples and lose to its double.
    meow = Meow(pitch=950.0, duration=0.4, kind="mew", breathiness=0.05)
    x = resample(synthesize(meow, seed=seed), SAMPLE_RATE, ANALYSIS_RATE)
    track = frame_pitch(x, ANALYSIS_RATE, max_pitch=1300.0)
    voiced = track[track > 0]
    assert len(voiced) > 20
    expected = np.median(950.0 * pitch_shape("mew", (), np.linspace(0, 1, 50)))
    assert np.mean(voiced < 0.75 * expected) < 0.03  # no octave-low frames to speak of


@pytest.mark.parametrize("pitch", [80.0, 100.0, 140.0])
def test_a_low_voice_under_a_high_ceiling_is_not_its_own_central_lobe(pitch):
    # With the ceiling raised for cats, a low voice's slow-decaying lag-0 lobe reaches the
    # top of the search band; the tracker must still find the real period.
    track = frame_pitch(voiced_burst(pitch, 0.5), RATE, max_pitch=1300.0)
    voiced = track[track > 0]
    assert len(voiced) > 10
    assert np.median(voiced) == pytest.approx(pitch, rel=0.03)
    assert np.max(voiced) < 2 * pitch
