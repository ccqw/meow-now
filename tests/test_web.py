import base64

import pytest
from conftest import RATE, speech_like
from fastapi.testclient import TestClient

from meow_now.audio import decode_wav, encode_wav
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
    samples, rate = _decode_data_url(data["audio"])
    assert rate == 16_000 and len(samples) > 0


def test_mockery_rejects_bad_input(client):
    assert client.post("/api/mockery", content=b"").status_code == 400
    assert client.post("/api/mockery", content=b"nope").status_code == 415
    too_big = b"\0" * (5 * 1024 * 1024)
    assert client.post("/api/mockery", content=too_big).status_code == 413
    low_rate = encode_wav(speech_like([150], rate=4000), 4000)
    assert client.post("/api/mockery", content=low_rate).status_code == 415


def test_monologue(client):
    resp = client.post("/api/cats/grumpy/monologue?seed=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cat"]["id"] == "grumpy"
    assert data["source"] == "builtin"
    assert [w["word"] for w in data["words"]] == data["text"].split()
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
