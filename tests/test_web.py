import asyncio
import base64

import numpy as np
import pytest
from conftest import RATE, ending_with, gliding_burst, speech_like
from fastapi.testclient import TestClient

from meow_now.audio import SAMPLE_RATE, decode_wav, encode_wav
from meow_now.web import Settings, create_app


@pytest.fixture
def client():
    settings = Settings(ollama_url=None, ollama_model="x", max_upload_bytes=4 * 1024 * 1024)
    return TestClient(create_app(settings))


def _decode_data_url(url):
    prefix = "data:audio/wav;base64,"
    assert url.startswith(prefix)
    return decode_wav(base64.b64decode(url[len(prefix) :]))


def test_index_and_health(client):
    page = client.get("/")
    assert page.status_code == 200 and "Meow Now" in page.text
    assert client.get("/healthz").json()["status"] == "ok"


def test_list_cats(client):
    data = client.get("/api/cats").json()
    assert [c["id"] for c in data["cats"]] == ["grumpy", "wise", "anxious", "diva"]
    assert data["ollama"] is False


def test_mockery_round_trip(client):
    wav = encode_wav(speech_like([120, 160, 200]), RATE)
    resp = client.post("/api/mockery", content=wav, headers={"content-type": "audio/wav"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["meows"]) == 3
    assert data["truncated"] is False
    assert data["reaction"] == "none"
    assert data["caption"] is None
    assert data["reaction_meows"] == []
    samples, rate = _decode_data_url(data["audio"])
    assert rate == SAMPLE_RATE and len(samples) > 0


def test_mockery_silent_upload_is_the_silent_treatment(client):
    wav = encode_wav(np.zeros(RATE, dtype=np.float32), RATE)
    resp = client.post("/api/mockery?seed=5", content=wav, headers={"content-type": "audio/wav"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["meows"] == []
    assert data["reaction"] == "silent"
    assert data["caption"] == "The cat waited. So did you."
    (mrrp,) = data["reaction_meows"]
    assert mrrp["kind"] == "mrrp" and mrrp["start"] >= 1.0
    samples, rate = _decode_data_url(data["audio"])
    assert rate == SAMPLE_RATE
    assert len(samples) / rate > mrrp["start"]


def test_mockery_question_reaction(client):
    x = ending_with(speech_like([150, 160, 140]), gliding_burst(140, 230, 0.4))
    data = client.post("/api/mockery?seed=2", content=encode_wav(x, RATE)).json()
    assert data["reaction"] == "question"
    assert isinstance(data["caption"], str) and data["caption"]
    assert len(data["meows"]) == 4 and len(data["reaction_meows"]) == 1


def test_mockery_seed_is_reproducible(client):
    wav = encode_wav(speech_like([120, 160, 200]), RATE)
    headers = {"content-type": "audio/wav"}
    first = client.post("/api/mockery?seed=42", content=wav, headers=headers)
    second = client.post("/api/mockery?seed=42", content=wav, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["seed"] == 42
    assert first.json() == second.json()
    assert first.content == second.content


def test_mockery_random_seed_is_echoed(client):
    wav = encode_wav(speech_like([150]), RATE)
    data = client.post("/api/mockery", content=wav, headers={"content-type": "audio/wav"}).json()
    assert isinstance(data["seed"], int) and data["seed"] >= 0


def test_mockery_meows_carry_contour_and_loudness(client):
    wav = encode_wav(speech_like([120, 160, 200]), RATE)
    data = client.post("/api/mockery?seed=1", content=wav).json()
    assert data["meows"]
    for meow in data["meows"]:
        assert 0.0 <= meow["loudness"] <= 1.0
        assert meow["loudness"] == round(meow["loudness"], 3)
        if meow["your_pitch_hz"] is None:
            assert meow["contour"] == []
        else:
            assert meow["contour"]
        assert all(0.5 <= c <= 2.0 and c == round(c, 3) for c in meow["contour"])
    assert max(m["loudness"] for m in data["meows"]) == 1.0


def test_mockery_in_a_cats_voice(client):
    wav = encode_wav(speech_like([120, 160, 200]), RATE)
    plain = client.post("/api/mockery?seed=3", content=wav).json()
    assert plain["cat"] is None
    grumpy = client.post("/api/mockery?seed=3&cat=grumpy", content=wav).json()
    assert grumpy["cat"]["id"] == "grumpy"
    assert grumpy == client.post("/api/mockery?seed=3&cat=grumpy", content=wav).json()
    assert grumpy["audio"] != plain["audio"]
    anxious = client.post("/api/mockery?seed=3&cat=anxious", content=wav).json()
    # Same recording, same seed: each meow pitched around its own cat's voice.
    for low, high in zip(grumpy["meows"], anxious["meows"], strict=True):
        assert low["pitch_hz"] < 0.7 * high["pitch_hz"]
    picked = client.post("/api/mockery?seed=3&cat=random", content=wav).json()
    assert picked["cat"]["id"] in ("grumpy", "wise", "anxious", "diva")


def test_mockery_unknown_cat_is_404(client):
    wav = encode_wav(speech_like([120, 160]), RATE)
    resp = client.post("/api/mockery?cat=dog", content=wav)
    assert resp.status_code == 404
    assert "dog" in resp.json()["detail"]


def test_mockery_rejects_negative_seed(client):
    wav = encode_wav(speech_like([150]), RATE)
    assert client.post("/api/mockery?seed=-1", content=wav).status_code == 422


def test_mockery_rejects_bad_input(client):
    assert client.post("/api/mockery", content=b"").status_code == 400
    assert client.post("/api/mockery", content=b"nope").status_code == 415
    too_big = b"\0" * (5 * 1024 * 1024)
    assert client.post("/api/mockery", content=too_big).status_code == 413
    low_rate = encode_wav(speech_like([150], rate=4000), 4000)
    assert client.post("/api/mockery", content=low_rate).status_code == 415


def test_mockery_stops_reading_a_chunked_upload_past_the_limit():
    settings = Settings(ollama_url=None, ollama_model="x", max_upload_bytes=1024)
    app = create_app(settings)
    chunk, available, calls, sent = b"\0" * 256, 1000, 0, []

    async def receive():
        nonlocal calls
        calls += 1
        return {"type": "http.request", "body": chunk, "more_body": calls < available}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/mockery",
        "raw_path": b"/api/mockery",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"audio/wav"), (b"transfer-encoding", b"chunked")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert calls < 20


def test_monologue(client):
    resp = client.post("/api/cats/grumpy/monologue?seed=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cat"]["id"] == "grumpy"
    assert data["source"] == "builtin"
    assert [w["word"] for w in data["words"]] == data["text"].split()
    for w in data["words"]:
        assert 0 < w["duration"] <= 0.8
        assert w["kind"] in ("mew", "meow", "mrrp", "yowl", "purr")
    _decode_data_url(data["audio"])


def test_random_monologue_and_unknown_cat(client):
    assert client.post("/api/cats/random/monologue").status_code == 200
    assert client.post("/api/cats/dog/monologue").status_code == 404


def test_single_meow(client):
    resp = client.get("/api/meow?pitch=700&duration=0.3&kind=mrrp")
    assert resp.headers["content-type"] == "audio/wav"
    samples, rate = decode_wav(resp.content)
    assert len(samples) == int(0.3 * rate)
    assert client.get("/api/meow?kind=woof").status_code == 422


def test_single_purr(client):
    resp = client.get("/api/meow?duration=1.0&kind=purr&seed=3")
    assert resp.status_code == 200
    samples, rate = decode_wav(resp.content)
    assert rate == SAMPLE_RATE
    assert len(samples) == int(1.0 * rate)
    assert 0.5 < max(abs(samples)) <= 0.9 + 1e-3
