import numpy as np
import pytest

from meow_now.analysis import frame_pitch
from meow_now.audio import SAMPLE_RATE
from meow_now.meows import KINDS, Meow, kind_for, place, synthesize


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
