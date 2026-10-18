"""Synthesize cat sounds: four kinds of meow, and a purr.

A meow is a vowel glide, roughly "m-ee-aa-oo", over a pitch that rises and
falls. It is built source-filter style:

- **Source.** A stack of harmonics on the pitch track. The pitch carries
  jitter (a small random wander) and, on longer meows, a 5 to 6 Hz vibrato;
  the amplitude carries shimmer. Roughness weakens every other glottal pulse
  (period doubling), which puts subharmonics between the harmonics: a growl.
- **Vocal tract.** Five formants glide through the vowel path, scaled by the
  size of the cat, with a nasal anti-formant while the mouth is still closed
  on the "m". Each harmonic is weighted by the tract's gain at its frequency.
- **Breath.** Aspiration noise, pulsed with the glottal cycle and filtered
  through the same tract, loudest on the attack and release with a little
  throughout.

A purr is a different animal: a train of 20 to 30 Hz pulses from the larynx,
rung through the closed-mouth tract so the pulses carry energy at a few
hundred Hz to a few kHz, where even a phone speaker can play them.

Synthesis is pure numpy and deterministic for a given seed, so tests can pin
the output and the same request always sounds the same.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from meow_now.audio import SAMPLE_RATE

KINDS = ("mew", "meow", "mrrp", "yowl", "purr")

# ---------- vocal tract ----------

# Formant targets (F1 to F5 in Hz) along the meow, for a medium-sized cat
# (formant_scale 1). Cats are small, so these sit well above human vowel
# formants. F4 and F5 barely move; they are what carries a meow above 8 kHz.
_VOWEL_PATH = np.array(
    [
        [450.0, 1500.0, 3200.0, 5200.0, 7600.0],  # m (mouth closed, nasal)
        [700.0, 2600.0, 3800.0, 5600.0, 8000.0],  # ee
        [1100.0, 1900.0, 3600.0, 5400.0, 7800.0],  # aa
        [600.0, 1100.0, 3000.0, 5000.0, 7400.0],  # oo / w
    ]
)
_BANDWIDTHS = np.array([160.0, 220.0, 350.0, 600.0, 900.0])  # Hz, at formant_scale 1
_FORMANT_GAIN = np.array([1.0, 0.7, 0.3, 0.15, 0.1])

# The nasal anti-formant: a spectral dip that the open nasal passage cuts
# into the "m" before the mouth opens.
NASAL_ZERO_HZ = 1300.0
NASAL_ZERO_BANDWIDTH_HZ = 300.0
NASAL_ZERO_DEPTH = 0.9  # 1 would cancel the tract's output at the zero completely

# ---------- voice defaults ----------

DEFAULT_PITCH_HZ = 600.0
DEFAULT_BREATHINESS = 0.15  # breath noise RMS at its peak, relative to the voice
DEFAULT_VIBRATO_HZ = 5.5
DEFAULT_VIBRATO_DEPTH = 0.015  # fraction of the pitch, so about +/- a quarter tone

# ---------- natural variation ----------

JITTER = 0.01  # RMS pitch wander, as a fraction of the pitch
SHIMMER = 0.04  # RMS amplitude wander, as a fraction of the amplitude
VARIATION_STEP_S = 0.01  # jitter and shimmer pick a new random target this often
ROUGH_VARIATION = 1.5  # at roughness 1, jitter and shimmer are this much larger again

VIBRATO_MIN_S = 0.4  # meows shorter than this get no vibrato
VIBRATO_ONSET_S = 0.15  # vibrato grows in over this long, as a held note does
VIBRATO_DEPTH_SPREAD = (0.5, 1.5)  # each meow's depth is the voice's depth times this
YOWL_VIBRATO_DEPTH = 0.04  # a yowl always wavers, at least this much, at any length

SUBHARMONIC_DEPTH = 0.8  # at roughness 1, every other pulse is this much weaker

# ---------- source ----------

SPECTRAL_TILT = 0.6  # harmonic k has amplitude k ** -tilt: a voice's natural tilt
BREATHY_TILT = 0.6  # extra tilt at breathiness 1: breathy voices lose their top end
CONTROL_STEP_S = 0.002  # formant weights are computed this often, then interpolated
ALIAS_FADE = (0.85, 0.95)  # harmonics fade out between these fractions of Nyquist

# ---------- breath ----------

BREATH_FLOOR = 0.25  # breath level through the middle of a meow, relative to its peak
BREATH_ATTACK_S = 0.05  # breath falls away over this long after the onset
BREATH_RELEASE_S = 0.08  # and rises again over this long before the end
BREATH_PULSING = 0.6  # how strongly the breath noise pulses with each glottal cycle
FILTER_FRAME_S = 0.02  # STFT frame for shaping noise through the tract

# ---------- purr ----------

PURR_RATE_HZ = 25.0  # pulse rate at the default pitch
PURR_RATE_RANGE = (20.0, 30.0)  # real purrs sit here; `pitch` moves the rate within it
PURR_CYCLE_S = 1.6  # one exhale plus one inhale
PURR_EXHALE_FRACTION = 0.55  # of each breath cycle
PURR_INHALE_GAIN = 0.7  # purring on the in-breath is quieter
PURR_INHALE_RATE = 1.08  # and a little faster
PURR_TURN_S = 0.05  # the brief dip where the breath turns around
PURR_PULSE_S = 0.001  # width of each laryngeal pulse; narrower pulses are brighter
PURR_NOISE = 0.5  # breath noise riding on each pulse, relative to the pulse
PURR_BREATHY_NOISE = 2.0  # at breathiness 1, that noise is this much louder again
PURR_NOISE_PULSE_S = 0.012  # that noise dies away to about 2 % over this long
PURR_CHEST_HZ = 140.0  # a low resonance that gives the rumble on a good speaker
PURR_CHEST_BANDWIDTH_HZ = 120.0
PURR_CHEST_GAIN = 0.3  # kept low: most of a purr should sit where small speakers play
PURR_ATTACK_S = 0.06
PURR_RELEASE_S = 0.12

PEAK = 0.9  # every clip is normalized to this peak, times its loudness


@dataclass(frozen=True)
class Meow:
    pitch: float = DEFAULT_PITCH_HZ  # Hz at the peak of the meow; sets the rate of a purr
    duration: float = 0.5  # seconds
    loudness: float = 1.0  # 0..1
    kind: str = "meow"
    contour: tuple[float, ...] = ()  # optional pitch multipliers over time
    # The voice: what makes one cat sound unlike another at the same pitch.
    formant_scale: float = 1.0  # vocal tract size: below 1 is a bigger cat
    breathiness: float = DEFAULT_BREATHINESS  # 0..1, breath noise and a softer top end
    roughness: float = 0.0  # 0..1, subharmonics and a less steady voice: a growl
    vibrato_rate: float = DEFAULT_VIBRATO_HZ  # Hz
    vibrato_depth: float = DEFAULT_VIBRATO_DEPTH  # fraction of the pitch


# Shortest clip, in seconds, that still sounds like each kind.
MIN_KIND_S = {"mew": 0.0, "meow": 0.18, "mrrp": 0.18, "yowl": 0.45, "purr": 0.4}
PREFERRED_KIND_CHANCE = 0.35  # how often a voice's favourite kinds win out


def kind_for(duration: float, rng: np.random.Generator, preferred: tuple[str, ...] = ()) -> str:
    """Pick a kind that suits the length of what it is replacing.

    `preferred` are kinds a voice reaches for more often than lengths alone
    suggest; each is only used where the clip is long enough for it.
    """
    usable = [k for k in preferred if duration >= MIN_KIND_S[k]]
    if usable and rng.random() < PREFERRED_KIND_CHANCE:
        return usable[int(rng.integers(len(usable)))]
    if duration < 0.18:
        return "mew"
    if duration > 0.75:
        return "yowl"
    return "mrrp" if rng.random() < 0.15 else "meow"


def _wander(n: int, rate: int, rng: np.random.Generator, rms: float) -> np.ndarray:
    """Smooth random variation around 0, a new target every VARIATION_STEP_S."""
    step = max(1, int(VARIATION_STEP_S * rate))
    knots = rng.standard_normal(n // step + 2) * rms
    return np.interp(np.arange(n) / step, np.arange(len(knots)), knots)


def _pitch_track(meow: Meow, t: np.ndarray, rate: int, rng: np.random.Generator) -> np.ndarray:
    x = t / max(meow.duration, 1e-3)  # 0..1 through the meow
    # The basic shape: start a bit low, peak about a third in, fall away.
    shape = 0.82 + 0.18 * np.exp(-(((x - 0.35) / 0.3) ** 2)) - 0.12 * x
    if meow.kind == "mew":
        shape = 0.9 + 0.1 * np.sin(np.pi * x)
    if meow.contour:
        follow = np.interp(x, np.linspace(0, 1, len(meow.contour)), meow.contour)
        # Follow the speaker's intonation, but keep it recognisably a meow.
        shape = shape * follow**0.8

    depth = meow.vibrato_depth * rng.uniform(*VIBRATO_DEPTH_SPREAD)
    if meow.kind == "yowl":
        depth = max(depth, YOWL_VIBRATO_DEPTH)
    elif meow.duration <= VIBRATO_MIN_S:
        depth = 0.0
    onset = np.clip(t / VIBRATO_ONSET_S, 0.0, 1.0)
    vibrato = depth * onset * np.sin(2 * np.pi * meow.vibrato_rate * t + rng.uniform(0, 2 * np.pi))

    jitter = _wander(len(t), rate, rng, JITTER * (1 + ROUGH_VARIATION * meow.roughness))
    return meow.pitch * shape * (1 + vibrato) * (1 + jitter)


def _formants(meow: Meow, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Formant frequencies over time, shape (len(x), 5), and how nasal each moment is."""
    if meow.kind == "mew":
        stops = np.array([0.0, 0.25, 0.7, 1.0])
        path = _VOWEL_PATH[[0, 1, 1, 3]] * 1.15
    elif meow.kind == "mrrp":
        stops = np.array([0.0, 0.45, 0.75, 1.0])
        path = _VOWEL_PATH[[0, 0, 2, 3]]
    else:
        stops = np.array([0.0, 0.12, 0.5, 1.0])
        path = _VOWEL_PATH
    formants = np.stack([np.interp(x, stops, path[:, i]) for i in range(path.shape[1])], axis=1)
    # Nasal while the lips are closed, gone once the mouth opens on the vowel.
    nasal = np.interp(x, [0.0, stops[1] * 0.5, stops[1]], [1.0, 1.0, 0.0])
    return formants * meow.formant_scale, nasal


def _tract_gain(
    freq: np.ndarray, formants: np.ndarray, nasal: np.ndarray, scale: float
) -> np.ndarray:
    """Gain of the vocal tract at `freq`, shape (T, F), given formants (T, 5) and nasal (T,).

    Each formant is a Lorentzian peak. `scale` is the formant scale, which
    the formants already carry; it widens the bandwidths and moves the nasal
    zero to match.
    """
    gain = np.zeros_like(freq)
    for i, (bandwidth, level) in enumerate(zip(_BANDWIDTHS * scale, _FORMANT_GAIN, strict=True)):
        gain += level / (1 + ((freq - formants[:, i : i + 1]) / bandwidth) ** 2)
    zero = 1 / (1 + ((freq - NASAL_ZERO_HZ * scale) / (NASAL_ZERO_BANDWIDTH_HZ * scale)) ** 2)
    return gain * (1 - NASAL_ZERO_DEPTH * nasal[:, None] * zero)


GainAt = Callable[[np.ndarray, np.ndarray], np.ndarray]


def _shape_noise(noise: np.ndarray, rate: int, gain_at: GainAt) -> np.ndarray:
    """Filter noise through a time-varying response, by overlap-add in the STFT domain.

    `gain_at(times, freqs)` returns the gain, shape (len(times), len(freqs)),
    at each frame's centre time. Frames overlap by half under a square-root
    Hann window on both sides, which sums back to unity.
    """
    size = 1 << int(np.ceil(np.log2(FILTER_FRAME_S * rate)))
    hop = size // 2
    n = len(noise)
    count = -(-n // hop) + 1
    padded = np.zeros((count + 1) * hop)
    padded[hop : hop + n] = noise
    window = np.sqrt(np.hanning(size + 1)[:size])
    idx = np.arange(size)[None, :] + hop * np.arange(count)[:, None]
    spectra = np.fft.rfft(padded[idx] * window, axis=1)
    times = np.arange(count) * hop / rate  # frame i is centred on sample i * hop
    freqs = np.fft.rfftfreq(size, 1 / rate)
    frames = np.fft.irfft(spectra * gain_at(times, freqs), n=size, axis=1) * window
    out = np.zeros((count + 1, hop))
    out[:-1] += frames[:, :hop]
    out[1:] += frames[:, hop:]
    return out.reshape(-1)[hop : hop + n]


def _envelope(meow: Meow, t: np.ndarray) -> np.ndarray:
    attack = min(0.04, meow.duration * 0.2)
    release = min(0.12, meow.duration * 0.35)
    env = np.ones_like(t)
    env = np.minimum(env, t / attack)
    env = np.minimum(env, (meow.duration - t) / release)
    env = np.clip(env, 0.0, 1.0) ** 1.5
    if meow.kind == "mrrp":
        # The rolled "rrr" at the start: fast amplitude flutter that fades out.
        roll = 0.5 + 0.5 * np.cos(2 * np.pi * 28.0 * t)
        fade = np.clip(t / (meow.duration * 0.45), 0, 1)
        env = env * (roll * (1 - fade) + fade)
    return env


def _voiced(
    meow: Meow, t: np.ndarray, rate: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The harmonic part of a meow, plus what the breath shares with it.

    Returns (samples, glottal phase, formants, nasality), so the breath can
    pulse with the voice and pass through the same tract.
    """
    n = len(t)
    x = t / max(meow.duration, 1e-3)
    f0 = _pitch_track(meow, t, rate, rng)
    phase = 2 * np.pi * np.cumsum(f0) / rate
    formants, nasal = _formants(meow, x)

    # Harmonic weights at a control rate, then linearly interpolated: the
    # formants, vibrato and jitter all move slowly next to the sample rate.
    step = max(1, int(CONTROL_STEP_S * rate))
    blocks = -(-n // step)
    ctrl = np.minimum(np.arange(blocks + 1) * step, n - 1)  # block edges; the last is held
    nyquist = rate / 2
    max_k = int(nyquist * ALIAS_FADE[1] / max(float(f0.min()), 50.0))
    k = np.arange(1, max_k + 1)
    freq = f0[ctrl, None] * k[None, :]  # (controls, harmonics)
    tilt = SPECTRAL_TILT + BREATHY_TILT * meow.breathiness
    alias = np.clip(
        (nyquist * ALIAS_FADE[1] - freq) / (nyquist * (ALIAS_FADE[1] - ALIAS_FADE[0])), 0, 1
    )
    weight = _tract_gain(freq, formants[ctrl], nasal[ctrl], meow.formant_scale) * k**-tilt * alias

    ramp = np.arange(step) / step
    edges = weight.T  # (harmonics, controls)
    smooth = edges[:, :-1, None] + np.diff(edges, axis=1)[:, :, None] * ramp
    smooth = smooth.reshape(max_k, -1)[:, :n]  # (harmonics, samples)

    # sin(k * phase + offset) for each harmonic k, stepping k up by angle
    # addition rather than calling sin per harmonic, which is much slower.
    offsets = rng.uniform(0, 2 * np.pi, max_k)
    sin1, cos1 = np.sin(phase), np.cos(phase)
    sin_k, cos_k = sin1, cos1
    out = np.zeros(n)
    for j in range(max_k):
        out += smooth[j] * (sin_k * np.cos(offsets[j]) + cos_k * np.sin(offsets[j]))
        sin_k, cos_k = sin_k * cos1 + cos_k * sin1, cos_k * cos1 - sin_k * sin1

    if meow.roughness > 0:
        # Period doubling: every other glottal pulse comes out weaker.
        doubling = 0.5 + 0.5 * np.cos(phase / 2 + rng.uniform(0, 2 * np.pi))
        out *= 1 - SUBHARMONIC_DEPTH * meow.roughness * doubling
    return out, phase, formants, nasal


def _breath(
    meow: Meow,
    t: np.ndarray,
    rate: int,
    rng: np.random.Generator,
    phase: np.ndarray,
    formants: np.ndarray,
    nasal: np.ndarray,
) -> np.ndarray:
    """Aspiration noise through the same tract, at unit RMS before gating."""
    noise = rng.standard_normal(len(t))
    noise *= 1 - BREATH_PULSING * (0.5 + 0.5 * np.cos(phase))

    def gain_at(times, freqs):
        i = np.clip((times * rate).astype(int), 0, len(t) - 1)
        grid = np.broadcast_to(freqs, (len(times), len(freqs)))
        return _tract_gain(grid, formants[i], nasal[i], meow.formant_scale)

    shaped = _shape_noise(noise, rate, gain_at)
    shaped /= max(float(np.sqrt(np.mean(shaped**2))), 1e-12)
    edges = np.maximum(
        np.exp(-t / BREATH_ATTACK_S), np.exp(-(meow.duration - t) / BREATH_RELEASE_S)
    )
    return shaped * (BREATH_FLOOR + (1 - BREATH_FLOOR) * edges)


def _purr(meow: Meow, t: np.ndarray, rate: int, rng: np.random.Generator) -> np.ndarray:
    """Laryngeal pulses at 20 to 30 Hz through the closed-mouth tract, over a breath cycle."""
    n = len(t)
    cycle = (t + rng.uniform(0, PURR_CYCLE_S)) % PURR_CYCLE_S
    exhale_s = PURR_CYCLE_S * PURR_EXHALE_FRACTION
    inhale = cycle >= exhale_s
    # A dip where the breath turns around, at both ends of the exhale.
    turn = np.minimum(np.abs(cycle - exhale_s), np.minimum(cycle, PURR_CYCLE_S - cycle))
    breath = np.where(inhale, PURR_INHALE_GAIN, 1.0) * np.clip(turn / PURR_TURN_S, 0, 1)

    variation = 1 + ROUGH_VARIATION * meow.roughness
    base = PURR_RATE_HZ * meow.pitch / DEFAULT_PITCH_HZ
    pulse_rate = np.clip(base * np.where(inhale, PURR_INHALE_RATE, 1.0), *PURR_RATE_RANGE)
    pulse_rate = pulse_rate * (1 + _wander(n, rate, rng, JITTER * variation))
    count = np.floor(np.cumsum(pulse_rate) / rate)
    at = np.flatnonzero(np.diff(count, prepend=0) > 0)
    impulses = np.zeros(n)
    impulses[at] = breath[at] * (1 + rng.standard_normal(len(at)) * SHIMMER * variation)

    pulse = np.hanning(max(3, int(PURR_PULSE_S * rate)))
    tail_n = int(PURR_NOISE_PULSE_S * rate)
    tail = np.exp(-4.0 * np.arange(tail_n) / tail_n)  # e ** -4 is about 2 %
    source = np.convolve(impulses, pulse, mode="same")
    noise_level = np.convolve(impulses, tail)[:n]
    noisiness = PURR_NOISE * (1 + PURR_BREATHY_NOISE * meow.breathiness)
    noise = rng.standard_normal(n) * noise_level * noisiness
    source = source / max(float(np.max(np.abs(source))), 1e-12) + noise

    closed = _VOWEL_PATH[0] * meow.formant_scale  # a purr is made with the mouth shut

    def gain_at(times, freqs):
        grid = np.broadcast_to(freqs, (len(times), len(freqs)))
        tract = _tract_gain(
            grid, np.tile(closed, (len(times), 1)), np.ones(len(times)), meow.formant_scale
        )
        chest = PURR_CHEST_GAIN / (1 + ((grid - PURR_CHEST_HZ) / PURR_CHEST_BANDWIDTH_HZ) ** 2)
        return tract + chest

    out = _shape_noise(source, rate, gain_at)
    env = np.minimum(t / PURR_ATTACK_S, (meow.duration - t) / PURR_RELEASE_S)
    return out * np.clip(env, 0.0, 1.0)


def synthesize(meow: Meow, rate: int = SAMPLE_RATE, seed: int = 0) -> np.ndarray:
    """Render one meow (or purr) to mono float32 samples."""
    rng = np.random.default_rng(seed)
    n = max(1, int(meow.duration * rate))
    t = np.arange(n) / rate

    if meow.kind == "purr":
        out = _purr(meow, t, rate, rng)
    else:
        voiced, phase, formants, nasal = _voiced(meow, t, rate, rng)
        breath = _breath(meow, t, rate, rng, phase, formants, nasal)
        level = float(np.sqrt(np.mean(voiced**2)))
        shimmer = 1 + _wander(n, rate, rng, SHIMMER * (1 + ROUGH_VARIATION * meow.roughness))
        out = (voiced * shimmer + breath * meow.breathiness * level) * _envelope(meow, t)

    top = float(np.max(np.abs(out)))
    if top > 0:
        out = out / top
    return (out * PEAK * float(np.clip(meow.loudness, 0.05, 1.0))).astype(np.float32)


def place(meows: list[tuple[float, np.ndarray]], duration: float, rate: int = SAMPLE_RATE):
    """Mix (start_seconds, samples) clips onto one timeline of `duration` seconds."""
    end = max([duration] + [start + len(clip) / rate for start, clip in meows])
    timeline = np.zeros(int(np.ceil(end * rate)) + 1, dtype=np.float32)
    for start, clip in meows:
        i = int(start * rate)
        timeline[i : i + len(clip)] += clip
    peak = float(np.max(np.abs(timeline))) if len(timeline) else 0.0
    if peak > 1.0:
        timeline /= peak
    return timeline
