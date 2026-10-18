"""WAV in, WAV out, and the small amount of signal plumbing in between.

Everything inside the app is mono float32 in [-1, 1]. Only 16-bit PCM WAV
crosses the wire: the browser decodes whatever it recorded and sends WAV, so
the server never needs ffmpeg.
"""

from __future__ import annotations

import io
import wave

import numpy as np

SAMPLE_RATE = 16_000


class AudioError(ValueError):
    """The upload is not audio this app can read."""


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode a PCM WAV file to (mono float32 samples, sample rate)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError(f"not a PCM WAV file: {exc}") from exc

    if width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioError(f"unsupported sample width: {width * 8} bits")

    if channels > 1:
        usable = len(samples) - len(samples) % channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float32), rate


def encode_wav(samples: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    """Encode mono float samples as 16-bit PCM WAV."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    return buf.getvalue()


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resample. Plenty for speech analysis and playback."""
    if src_rate == dst_rate or len(samples) == 0:
        return samples.astype(np.float32)
    duration = len(samples) / src_rate
    n_out = max(1, int(round(duration * dst_rate)))
    src_t = np.arange(len(samples)) / src_rate
    dst_t = np.arange(n_out) / dst_rate
    return np.interp(dst_t, src_t, samples).astype(np.float32)
