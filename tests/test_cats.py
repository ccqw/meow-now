import httpx
import numpy as np
import pytest

from meow_now import cats


def test_four_cats_each_with_lines():
    assert set(cats.CATS) == {"grumpy", "wise", "anxious", "diva"}
    for cat in cats.CATS.values():
        assert cat.lines and all(len(line.split()) <= cats.MAX_WORDS for line in cat.lines)
        for line in cat.lines:
            line.encode("ascii")


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
    assert starts[-1] < len(result.audio) / 16_000


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
