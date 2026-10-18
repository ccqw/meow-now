import io
import wave

import numpy as np
import pytest

from meow_now.audio import AudioError, decode_wav, encode_wav, resample


def test_wav_round_trip_keeps_samples():
    x = np.sin(np.linspace(0, 20, 1600)).astype(np.float32) * 0.5
    y, rate = decode_wav(encode_wav(x, 16_000))
    assert rate == 16_000
    assert np.allclose(x, y, atol=1e-4)


def test_stereo_is_downmixed():
    left = np.full(100, 8000, dtype="<i2")
    right = np.full(100, -8000, dtype="<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(np.stack([left, right], axis=1).tobytes())
    y, rate = decode_wav(buf.getvalue())
    assert rate == 8000 and len(y) == 100
    assert np.allclose(y, 0.0)


def test_garbage_is_rejected():
    with pytest.raises(AudioError):
        decode_wav(b"definitely not a wav file")


def test_resample_changes_length_not_pitch():
    rate = 48_000
    t = np.arange(rate) / rate
    x = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    y = resample(x, rate, 16_000)
    assert len(y) == 16_000
    spectrum = np.abs(np.fft.rfft(y))
    assert abs(np.argmax(spectrum) - 440) <= 1  # 1 Hz bins at 1 second
