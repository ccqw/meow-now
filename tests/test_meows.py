from dataclasses import replace

import numpy as np
import pytest
from conftest import band_fraction, cat_pitch_track, spectral_centroid

from meow_now import meows
from meow_now.analysis import frame_pitch
from meow_now.audio import SAMPLE_RATE, encode_wav
from meow_now.meows import KINDS, PEAK, Meow, kind_for, place, synthesize


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_renders_a_bounded_clip(kind):
    clip = synthesize(Meow(pitch=600, duration=0.5, kind=kind))
    assert clip.dtype == np.float32
    assert len(clip) == int(0.5 * SAMPLE_RATE)
    assert 0.5 < np.max(np.abs(clip)) <= 0.9 + 1e-6
    # Starts and ends quietly: no clicks.
    assert np.max(np.abs(clip[:16])) < 0.1
    assert np.max(np.abs(clip[-16:])) < 0.1


def test_synthesis_is_deterministic_per_seed():
    a = synthesize(Meow(), seed=3)
    b = synthesize(Meow(), seed=3)
    c = synthesize(Meow(), seed=4)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_meow_is_pitched_where_asked():
    clip = synthesize(Meow(pitch=400, duration=0.8, kind="meow"))
    track = frame_pitch(clip, SAMPLE_RATE)
    voiced = track[track > 0]
    # The pitch track rises and falls around the requested peak.
    assert 300 < np.median(voiced) < 420


def test_loudness_scales_the_clip():
    loud = synthesize(Meow(loudness=1.0))
    quiet = synthesize(Meow(loudness=0.3))
    assert np.max(np.abs(quiet)) < np.max(np.abs(loud)) * 0.4


def test_kind_follows_duration(rng):
    assert kind_for(0.1, rng) == "mew"
    assert kind_for(1.0, rng) == "yowl"
    assert kind_for(0.4, rng) in ("meow", "mrrp")


def test_place_puts_clips_on_a_timeline():
    clip = np.ones(1600, dtype=np.float32) * 0.5
    timeline = place([(0.0, clip), (1.0, clip)], 2.0)
    assert len(timeline) >= 2 * SAMPLE_RATE
    assert timeline[800] == pytest.approx(0.5)
    assert timeline[int(0.5 * SAMPLE_RATE)] == 0
    assert timeline[SAMPLE_RATE + 800] == pytest.approx(0.5)


def test_place_never_clips():
    clip = np.ones(1600, dtype=np.float32)
    timeline = place([(0.0, clip), (0.0, clip)], 0.2)
    assert np.max(np.abs(timeline)) <= 1.0


# ---------- realism: measured, since nobody can listen in CI ----------

EXTREME_VOICES = [
    {},
    {"formant_scale": 0.8, "roughness": 1.0, "breathiness": 1.0},
    {"formant_scale": 1.25, "roughness": 0.0, "breathiness": 0.0, "vibrato_depth": 0.05},
]


@pytest.mark.parametrize("voice", EXTREME_VOICES)
@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_and_voice_is_byte_identical_per_seed_and_never_clips(kind, voice):
    meow = Meow(pitch=700, duration=0.9, kind=kind, contour=(0.8, 1.3, 0.9), **voice)
    first = synthesize(meow, seed=11)
    assert encode_wav(first) == encode_wav(synthesize(meow, seed=11))
    assert not np.array_equal(first, synthesize(meow, seed=12))
    assert np.all(np.isfinite(first))
    assert np.max(np.abs(first)) <= PEAK + 1e-6


@pytest.mark.parametrize("kind", KINDS)
def test_energy_above_8khz_is_present(kind):
    clip = synthesize(Meow(pitch=600, duration=0.6, kind=kind), seed=1)
    # Nothing at all could live up there at the old 16 kHz rate.
    assert SAMPLE_RATE >= 24_000
    assert band_fraction(clip, SAMPLE_RATE, 8_000, 12_000) > 1e-4  # above -40 dB


def _detrended(track: np.ndarray) -> np.ndarray:
    """Remove the slow rise and fall of a pitch track, leaving the wobble."""
    frames = np.arange(len(track))
    return track - np.polyval(np.polyfit(frames, track, 3), frames)


def test_jitter_is_measurable(monkeypatch):
    def relative_wobble(seed):
        # A long mew holds its pitch; with no vibrato what wobbles is jitter.
        clip = synthesize(Meow(pitch=400, duration=1.0, kind="mew", vibrato_depth=0), seed=seed)
        steady = cat_pitch_track(clip, SAMPLE_RATE)[20:80]
        return np.std(_detrended(steady)) / np.mean(steady)

    jittered = [relative_wobble(seed) for seed in range(4)]
    monkeypatch.setattr(meows, "JITTER", 0.0)
    clean = [relative_wobble(seed) for seed in range(4)]
    # About 1 % jitter, partly averaged away by the 40 ms analysis window.
    assert min(jittered) > 0.0025
    assert min(jittered) > 4 * max(clean)


def _vibrato_band_power(clip: np.ndarray) -> tuple[float, float]:
    """(power of the pitch wobble at 4 to 7 Hz, frequency of the strongest wobble)."""
    wobble = _detrended(cat_pitch_track(clip, SAMPLE_RATE)[30:-15])
    spectrum = np.abs(np.fft.rfft(wobble * np.hanning(len(wobble)), n=1024)) ** 2
    freqs = np.fft.rfftfreq(1024, 0.01)  # the tracker hops 10 ms
    band = (freqs > 2) & (freqs < 15)
    vibrato = (freqs >= 4) & (freqs <= 7)
    return float(spectrum[vibrato].sum()), float(freqs[band][np.argmax(spectrum[band])])


@pytest.mark.parametrize("seed", range(4))
def test_long_meows_get_vibrato(seed):
    long_meow = Meow(pitch=400, duration=1.2)
    with_vibrato, _ = _vibrato_band_power(synthesize(long_meow, seed=seed))
    flat = Meow(pitch=400, duration=1.2, vibrato_depth=0)
    without, _ = _vibrato_band_power(synthesize(flat, seed=seed))
    assert with_vibrato > 3 * without


@pytest.mark.parametrize("rate", [5.0, 6.0])
def test_vibrato_runs_at_the_voice_rate(rate):
    meow = Meow(pitch=400, duration=1.5, vibrato_rate=rate, vibrato_depth=0.04)
    _, peak = _vibrato_band_power(synthesize(meow, seed=1))
    assert abs(peak - rate) < 0.5


def test_short_meows_get_no_vibrato():
    short = Meow(pitch=600, duration=0.35)
    assert np.array_equal(synthesize(short), synthesize(replace(short, vibrato_depth=0)))


def _steady_partials(clip: np.ndarray, f0: float) -> tuple[float, float]:
    """Energy halfway between harmonics, and a quarter of the way, relative to the harmonics.

    Halfway is where period doubling puts subharmonics; a quarter of the way
    is clear of both, so it measures noise.
    """
    middle = clip[int(0.4 * SAMPLE_RATE) : int(0.6 * SAMPLE_RATE)]
    power = np.abs(np.fft.rfft(middle * np.hanning(len(middle)), n=8 * len(middle))) ** 2
    freqs = np.fft.rfftfreq(8 * len(middle), 1 / SAMPLE_RATE)

    def near(hz):
        return power[np.abs(freqs - hz) < 40].sum()

    ks = range(1, 9)
    harmonics = sum(near(k * f0) for k in ks)
    halfway = sum(near((k + 0.5) * f0) for k in ks)
    between = sum(near((k + 0.25) * f0) + near((k + 0.75) * f0) for k in ks)
    return halfway / harmonics, between / harmonics


def _steady(**voice) -> np.ndarray:
    """A held 500 Hz mew in the given voice, without vibrato."""
    return synthesize(Meow(pitch=500, duration=1.0, kind="mew", vibrato_depth=0, **voice), seed=2)


def test_roughness_adds_subharmonics():
    smooth, _ = _steady_partials(_steady(roughness=0.0), 500)
    growl, _ = _steady_partials(_steady(roughness=0.6), 500)
    assert growl > 10 * smooth


def test_breathiness_adds_noise_between_harmonics():
    _, clear = _steady_partials(_steady(breathiness=0.0), 500)
    _, airy = _steady_partials(_steady(breathiness=0.5), 500)
    assert airy > 3 * clear


def test_formant_scale_moves_the_spectrum():
    def brightness(scale):
        pitches = range(400, 850, 50)
        clips = [synthesize(Meow(pitch=p, formant_scale=scale), seed=1) for p in pitches]
        return np.mean([spectral_centroid(c, SAMPLE_RATE) for c in clips])

    big, medium, small = brightness(0.85), brightness(1.0), brightness(1.15)
    assert big < medium * 0.95 and small > medium * 1.05


def test_nasal_anti_formant_dips_the_opening():
    freqs = np.array([[meows.NASAL_ZERO_HZ, meows.NASAL_ZERO_HZ * 2]])
    formants = meows._VOWEL_PATH[:1]
    nasal = meows._tract_gain(freqs, formants, np.ones(1), 1.0)
    oral = meows._tract_gain(freqs, formants, np.zeros(1), 1.0)
    assert nasal[0, 0] < 0.2 * oral[0, 0]  # a deep dip at the zero
    assert nasal[0, 1] > 0.8 * oral[0, 1]  # and little effect an octave up


def _pulse_rate(clip: np.ndarray) -> float:
    """The dominant amplitude-modulation rate between 15 and 40 Hz."""
    width = int(0.004 * SAMPLE_RATE)
    envelope = np.convolve(np.abs(clip), np.ones(width) / width, mode="same")
    envelope -= envelope.mean()
    ac = np.correlate(envelope, envelope, mode="full")[len(envelope) - 1 :]
    shortest, longest = SAMPLE_RATE // 40, SAMPLE_RATE // 15
    return SAMPLE_RATE / (shortest + int(np.argmax(ac[shortest:longest])))


@pytest.mark.parametrize("pitch, low, high", [(300, 20, 22), (600, 23, 27), (1200, 28, 30.5)])
def test_purr_pulses_at_20_to_30_hz(pitch, low, high):
    clip = synthesize(Meow(pitch=pitch, duration=1.5, kind="purr"), seed=2)
    assert low <= _pulse_rate(clip) <= high


def test_purr_reads_on_a_phone_speaker():
    clip = synthesize(Meow(duration=1.5, kind="purr"), seed=2)
    # Most of the energy sits in the telephone band, not in a 25 Hz rumble.
    assert band_fraction(clip, SAMPLE_RATE, 300, 3_400) > 0.6
    assert band_fraction(clip, SAMPLE_RATE, 0, 60) < 0.05


def test_preferred_kinds_come_up_more_often(rng):
    plain = [kind_for(0.5, rng) for _ in range(400)]
    fond = [kind_for(0.5, rng, ("purr",)) for _ in range(400)]
    assert "purr" not in plain
    assert 0.25 < fond.count("purr") / len(fond) < 0.45
    # A kind is only preferred where the clip is long enough for it.
    assert all(kind_for(0.2, rng, ("yowl", "purr")) != "yowl" for _ in range(100))
