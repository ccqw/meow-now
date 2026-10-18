"""Find the syllables in a recording, with their timing, loudness and pitch.

This is deliberately simple signal processing, not speech recognition: an
energy envelope finds where the caller is making noise, valleys in that
envelope split runs of speech into syllable-sized pieces, and a normalized
autocorrelation per frame estimates pitch. It only has to be good enough for
a cat to mock you convincingly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

HOP_S = 0.010  # 10 ms between analysis frames
WINDOW_S = 0.040  # 40 ms window: long enough for a 70 Hz pitch period
MIN_PITCH_HZ = 70.0
MAX_PITCH_HZ = 500.0
VOICING_THRESHOLD = 0.45  # autocorrelation peak needed to call a frame voiced
OCTAVE_PREFERENCE = 0.9  # half the lag wins if it correlates at least this well
MIN_SYLLABLE_S = 0.08
MAX_SYLLABLE_S = 1.2
MAX_GAP_S = 0.06  # quieter dips shorter than this do not end a syllable
CONTOUR_POINTS = 8


@dataclass(frozen=True)
class Syllable:
    start: float  # seconds from the start of the recording
    duration: float  # seconds
    loudness: float  # 0..1, relative to the loudest syllable
    pitch: float | None  # median voiced pitch in Hz, None if unvoiced
    contour: tuple[float, ...] = field(default=())  # pitch / median pitch over time


@dataclass(frozen=True)
class Analysis:
    duration: float
    syllables: tuple[Syllable, ...]

    @property
    def median_pitch(self) -> float | None:
        pitches = [s.pitch for s in self.syllables if s.pitch]
        return float(np.median(pitches)) if pitches else None


def _frames(samples: np.ndarray, rate: int) -> np.ndarray:
    hop = int(rate * HOP_S)
    win = int(rate * WINDOW_S)
    if len(samples) < win:
        samples = np.pad(samples, (0, win - len(samples)))
    n = 1 + (len(samples) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return samples[idx]


def frame_energy(samples: np.ndarray, rate: int) -> np.ndarray:
    """RMS per frame, lightly smoothed so one frame of noise is not a syllable."""
    rms = np.sqrt(np.mean(_frames(samples, rate) ** 2, axis=1))
    kernel = np.ones(3) / 3
    return np.convolve(rms, kernel, mode="same")


def frame_pitch(samples: np.ndarray, rate: int, max_pitch: float = MAX_PITCH_HZ) -> np.ndarray:
    """Pitch per frame in Hz, or 0 where the frame is not clearly voiced."""
    frames = _frames(samples, rate)
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = frames * np.hanning(frames.shape[1])[None, :]
    size = 1 << int(np.ceil(np.log2(2 * frames.shape[1])))
    spec = np.fft.rfft(frames, n=size, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, n=size, axis=1)[:, : frames.shape[1]]
    energy = ac[:, :1]
    with np.errstate(divide="ignore", invalid="ignore"):
        ac = np.where(energy > 1e-9, ac / energy, 0.0)

    lo = int(rate / max_pitch)
    hi = min(int(rate / MIN_PITCH_HZ), ac.shape[1] - 2)
    rows = np.arange(len(ac))
    # Every frame correlates with itself at small lags. A low voice keeps that
    # central lobe up long enough to reach a high ceiling, so search only past
    # the point where the correlation first goes negative.
    below = ac[:, :hi] < 0
    first = np.where(below.any(axis=1), np.argmax(below, axis=1), hi)
    band = np.where(np.arange(lo, hi)[None, :] >= first[:, None], ac[:, lo:hi], -np.inf)
    best = np.argmax(band, axis=1)
    peak = band[rows, best]
    # A period's multiples correlate too, and on a high voice sampled at a few
    # samples per period the true peak can fall between samples and lose to its
    # double: an octave too low. Prefer half the lag when it correlates nearly
    # as well, up to twice (for a quadruple).
    for _ in range(2):
        half = np.rint((best + lo) / 2).astype(np.int64) - lo
        near = np.stack([half - 1, half, half + 1], axis=1)
        ok = near >= 0
        values = np.where(ok, band[rows[:, None], np.clip(near, 0, None)], -np.inf)
        pick = np.argmax(values, axis=1)
        value = values[rows, pick]
        better = (half >= 1) & (value >= OCTAVE_PREFERENCE * peak)
        best = np.where(better, near[rows, pick], best)
        peak = np.where(better, value, peak)
    lag = (best + lo).astype(np.float64)

    # Parabolic interpolation around the peak for sub-sample lag accuracy.
    left = ac[rows, np.clip(best + lo - 1, 0, None)]
    right = ac[rows, best + lo + 1]
    denom = left - 2 * peak + right
    with np.errstate(divide="ignore", invalid="ignore"):
        shift = np.where(np.abs(denom) > 1e-9, 0.5 * (left - right) / denom, 0.0)
    lag = lag + np.clip(shift, -1, 1)

    pitch = np.where(peak >= VOICING_THRESHOLD, rate / lag, 0.0)
    return pitch.astype(np.float32)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of the True runs in a boolean array."""
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return list(zip(edges[::2], edges[1::2], strict=True))


def _split_at_valleys(energy: np.ndarray, start: int, end: int) -> list[tuple[int, int]]:
    """Split one run of sound at energy valleys, so "hello there" is 4 meows, not 1."""
    max_frames = int(MAX_SYLLABLE_S / HOP_S)
    min_frames = int(MIN_SYLLABLE_S / HOP_S)
    seg = energy[start:end]
    cuts = [0]
    for i in range(min_frames, len(seg) - min_frames):
        window = seg[max(0, i - min_frames) : i + min_frames + 1]
        if seg[i] != window.min():
            continue
        left_peak = seg[cuts[-1] : i].max()
        right_peak = seg[i:].max()
        if seg[i] < 0.7 * min(left_peak, right_peak) and i - cuts[-1] >= min_frames:
            cuts.append(i)
    cuts.append(len(seg))

    pieces: list[tuple[int, int]] = []
    for a, b in zip(cuts, cuts[1:], strict=False):
        # A syllable that is still too long gets cut into even pieces.
        n = max(1, int(np.ceil((b - a) / max_frames)))
        bounds = np.linspace(a, b, n + 1).astype(int)
        pieces.extend((start + x, start + y) for x, y in zip(bounds, bounds[1:], strict=False))
    return pieces


def analyze(samples: np.ndarray, rate: int, max_pitch: float = MAX_PITCH_HZ) -> Analysis:
    """Break a recording into syllables.

    `max_pitch` is the highest pitch the tracker looks for. The default suits
    speech; raise it to follow a cat, or a person doing a cat voice.
    """
    duration = len(samples) / rate if rate else 0.0
    if len(samples) == 0:
        return Analysis(duration=0.0, syllables=())

    energy = frame_energy(samples, rate)
    pitch = frame_pitch(samples, rate, max_pitch)
    peak = float(energy.max())
    if peak < 1e-4:  # digital silence or near enough
        return Analysis(duration=duration, syllables=())

    floor = float(np.percentile(energy, 20))
    threshold = max(floor * 3.0, peak * 0.12)
    loud = energy > threshold

    # Bridge short dips so a consonant does not split a syllable in two.
    max_gap = int(MAX_GAP_S / HOP_S)
    for a, b in _runs(~loud):
        if a > 0 and b < len(loud) and b - a <= max_gap:
            loud[a:b] = True

    pieces: list[tuple[int, int]] = []
    for a, b in _runs(loud):
        if (b - a) * HOP_S >= MIN_SYLLABLE_S:
            pieces.extend(_split_at_valleys(energy, a, b))

    loudest = max((float(energy[a:b].max()) for a, b in pieces), default=1.0)
    syllables = []
    for a, b in pieces:
        voiced = pitch[a:b][pitch[a:b] > 0]
        median = float(np.median(voiced)) if len(voiced) >= 2 else None
        contour: tuple[float, ...] = ()
        if median:
            track = pitch[a:b].astype(np.float64)
            idx = np.flatnonzero(track > 0)
            track = np.interp(np.arange(len(track)), idx, track[idx])
            points = np.interp(
                np.linspace(0, len(track) - 1, CONTOUR_POINTS), np.arange(len(track)), track
            )
            contour = tuple(float(np.clip(p / median, 0.5, 2.0)) for p in points)
        syllables.append(
            Syllable(
                start=a * HOP_S,
                duration=(b - a) * HOP_S,
                loudness=float(energy[a:b].max()) / loudest,
                pitch=median,
                contour=contour,
            )
        )
    return Analysis(duration=duration, syllables=tuple(syllables))
