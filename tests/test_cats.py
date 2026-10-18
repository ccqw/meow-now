import json
from itertools import combinations

import httpx
import numpy as np
import pytest
from conftest import spectral_centroid

from meow_now import cats, meows
from meow_now.audio import SAMPLE_RATE


def test_four_cats_each_with_lines():
    assert set(cats.CATS) == {"grumpy", "wise", "anxious", "diva"}
    for cat in cats.CATS.values():
        assert cat.lines and all(len(line.split()) <= cats.MAX_WORDS for line in cat.lines)
        for line in cat.lines:
            line.encode("ascii")


@pytest.mark.parametrize("cat_id", sorted(cats.CATS))
def test_every_cat_has_plenty_to_say(cat_id):
    lines = cats.CATS[cat_id].lines
    assert len(lines) >= 12
    assert len(set(lines)) == len(lines)
    for line in lines:
        assert 20 <= len(line.split()) <= cats.MAX_WORDS, line
        assert "  " not in line and line == line.strip()


def test_builtin_monologues_rotate_through_every_line(monkeypatch):
    monkeypatch.setattr(cats, "speak", lambda cat, text, seed=0: (np.zeros(1), ()))
    for cat in cats.CATS.values():
        heard = {cats.monologue(cat, seed=seed).text for seed in range(len(cat.lines))}
        assert heard == set(cat.lines)


def test_get_cat(rng):
    assert cats.get_cat("wise", rng).id == "wise"
    assert cats.get_cat("random", rng).id in cats.CATS
    assert cats.get_cat(None, rng).id in cats.CATS
    with pytest.raises(KeyError):
        cats.get_cat("dog", rng)


def test_builtin_monologue_without_ollama():
    result = cats.monologue(cats.CATS["grumpy"], seed=0)
    assert result.source == "builtin"
    assert result.text == cats.CATS["grumpy"].lines[0]
    assert len(result.words) == len(result.text.split())
    assert len(result.audio) > 0


def test_subtitle_times_increase_and_fit_the_audio():
    result = cats.monologue(cats.CATS["anxious"], seed=1)
    starts = [s for _, s in result.words]
    assert starts == sorted(starts)
    assert starts[-1] < len(result.audio) / SAMPLE_RATE


def test_tempo_changes_length():
    text = "one two three four five six seven eight."
    fast, _ = cats.speak(cats.CATS["anxious"], text)
    slow, _ = cats.speak(cats.CATS["wise"], text)
    assert len(slow) > len(fast) * 1.3


def test_lower_cats_meow_lower(monkeypatch):
    planned = []
    real = cats.meows.synthesize

    def spy(meow, *args, **kwargs):
        planned.append(meow)
        return real(meow, *args, **kwargs)

    monkeypatch.setattr(cats.meows, "synthesize", spy)

    def median_pitch(cat_id):
        planned.clear()
        cats.speak(cats.CATS[cat_id], "meow meow meow meow meow meow", seed=2)
        return np.median([m.pitch for m in planned])

    assert median_pitch("grumpy") < median_pitch("wise") < median_pitch("anxious")


def _fake_ollama(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def post(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(cats.httpx, "post", post)


def test_ollama_monologue_is_used_and_cleaned(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        words = " ".join(["meow"] * 80)
        return httpx.Response(200, json={"response": f'  "Feed   me now. {words}"  '})

    _fake_ollama(monkeypatch, handler)
    result = cats.monologue(cats.CATS["diva"], ollama_url="http://ollama:11434/", ollama_model="m")
    assert seen["url"] == "http://ollama:11434/api/generate"
    assert '"model": "m"' in seen["body"] or '"model":"m"' in seen["body"]
    assert result.source == "ollama"
    assert result.text.startswith("Feed me now.")
    assert len(result.text.split()) == cats.MAX_WORDS


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500, text="boom"),
        lambda request: httpx.Response(200, json={"response": "meow"}),
        lambda request: httpx.Response(200, text="not json"),
    ],
)
def test_ollama_failures_fall_back_to_builtin(monkeypatch, handler):
    _fake_ollama(monkeypatch, handler)
    result = cats.monologue(cats.CATS["wise"], seed=2, ollama_url="http://ollama:11434")
    assert result.source == "builtin"
    assert result.text == cats.CATS["wise"].lines[2]


def test_unreachable_ollama_falls_back(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")

    _fake_ollama(monkeypatch, handler)
    assert cats.monologue(cats.CATS["wise"], ollama_url="http://nowhere").source == "builtin"


@pytest.mark.parametrize("cat_id", sorted(cats.CATS))
def test_ollama_prompt_has_no_phone_wording(monkeypatch, cat_id):
    cat = cats.CATS[cat_id]
    seen = {}

    def handler(request):
        seen["prompt"] = json.loads(request.read())["prompt"]
        return httpx.Response(200, json={"response": "Feed me now, human, please."})

    _fake_ollama(monkeypatch, handler)
    cats.ask_ollama(cat, "http://ollama:11434", "m")
    prompt = seen["prompt"].lower()
    assert "call" not in prompt
    assert "phone" not in prompt
    assert cat.name in seen["prompt"]
    assert str(cats.MAX_WORDS) in seen["prompt"]


def _voiced(cat_id: str, **meow) -> meows.Meow:
    voice = cats.CATS[cat_id].voice
    return meows.Meow(
        formant_scale=voice.formant_scale,
        breathiness=voice.breathiness,
        roughness=voice.roughness,
        **meow,
    )


def _voice_features(cat_id: str) -> np.ndarray:
    """(brightness, growl, breath) of a cat's voice, measured at a pitch every cat shares."""
    brightness = np.mean(
        [
            spectral_centroid(meows.synthesize(_voiced(cat_id, pitch=p), seed=1), SAMPLE_RATE)
            for p in range(400, 850, 50)
        ]
    )
    # A held 500 Hz mew: energy halfway between harmonics is growl, a quarter of the way is breath.
    held = meows.synthesize(
        _voiced(cat_id, pitch=500, duration=1.0, kind="mew", vibrato_depth=0), seed=2
    )
    middle = held[int(0.4 * SAMPLE_RATE) : int(0.6 * SAMPLE_RATE)]
    power = np.abs(np.fft.rfft(middle * np.hanning(len(middle)), n=8 * len(middle))) ** 2
    freqs = np.fft.rfftfreq(8 * len(middle), 1 / SAMPLE_RATE)

    def near(multiple):
        return sum(power[np.abs(freqs - (k + multiple) * 500) < 40].sum() for k in range(1, 9))

    harmonics = near(0.0)
    return np.array([brightness, near(0.5) / harmonics, (near(0.25) + near(0.75)) / harmonics])


def test_the_four_voices_differ_measurably():
    features = {cat_id: _voice_features(cat_id) for cat_id in cats.CATS}
    for a, b in combinations(features, 2):
        ratio = features[a] / features[b]
        # Every pair differs by 5 % in brightness, or 2x in growl or breath.
        far_apart = abs(np.log(ratio)) > np.log([1.05, 2.0, 2.0])
        assert far_apart.any(), f"{a} and {b} sound alike: {ratio}"
    # And each difference is in character.
    brightness = {cat_id: f[0] for cat_id, f in features.items()}
    assert min(brightness, key=brightness.get) == "grumpy"  # the biggest cat
    assert max(brightness, key=brightness.get) == "anxious"  # the smallest
    assert max(features, key=lambda c: features[c][1]) == "grumpy"  # the growl
    assert features["wise"][2] > 3 * features["diva"][2]  # airy scholar, clear diva


@pytest.mark.parametrize("cat_id", sorted(cats.CATS))
def test_speak_uses_the_cats_voice(monkeypatch, cat_id):
    cat = cats.CATS[cat_id]
    planned = []
    real = cats.meows.synthesize

    def spy(meow, *args, **kwargs):
        planned.append(meow)
        return real(meow, *args, **kwargs)

    monkeypatch.setattr(cats.meows, "synthesize", spy)
    for line in cat.lines:
        cats.speak(cat, line, seed=0)
    voice = cat.voice
    for meow in planned:
        assert meow.formant_scale == voice.formant_scale
        assert meow.breathiness == voice.breathiness
        assert meow.roughness == voice.roughness
        assert (meow.vibrato_rate, meow.vibrato_depth) == (voice.vibrato_rate, voice.vibrato_depth)
    kinds = [m.kind for m in planned]
    for favourite in voice.kinds:
        assert kinds.count(favourite) >= 3


def test_voice_rejects_unknown_kinds():
    with pytest.raises(ValueError, match="woof"):
        cats.Voice(kinds=("woof",))
