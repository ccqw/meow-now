import json
import logging
import re
import threading

import pytest
from conftest import RATE, speech_like
from fastapi.testclient import TestClient

from meow_now import web
from meow_now.audio import encode_wav
from meow_now.limits import RateLimiter
from meow_now.middleware import JsonFormatter, apply_nonce
from meow_now.web import Settings, create_app

MB = 1024 * 1024


def _client(**overrides) -> TestClient:
    settings = Settings(ollama_url=None, ollama_model="x", max_upload_bytes=4 * MB, **overrides)
    return TestClient(create_app(settings))


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# Rate limiting


def test_limiter_allows_a_burst_then_refills():
    clock = FakeClock()
    limiter = RateLimiter(per_minute=3, clock=clock)
    assert [limiter.hit("a") for _ in range(3)] == [0, 0, 0]
    wait = limiter.hit("a")
    assert wait == pytest.approx(20.0)
    assert limiter.hit("b") == 0  # other clients have their own bucket
    clock.now += 20
    assert limiter.hit("a") == 0
    assert limiter.hit("a") > 0


def test_limiter_disabled_at_zero():
    limiter = RateLimiter(per_minute=0)
    assert not limiter.enabled
    assert all(limiter.hit("a") == 0 for _ in range(1000))
    assert len(limiter) == 0


def test_limiter_forgets_the_least_recently_seen_client():
    limiter = RateLimiter(per_minute=1, max_clients=3)
    for key in ["a", "b", "c"]:
        limiter.hit(key)
    assert limiter.hit("a") > 0  # touches "a", so "b" is now the oldest
    limiter.hit("d")
    assert len(limiter) == 3
    assert limiter.hit("b") == 0  # forgotten, so it starts with a fresh bucket
    assert limiter.hit("a") > 0  # still tracked
    for i in range(1000):
        limiter.hit(f"flood-{i}")
    assert len(limiter) == 3


def test_api_returns_429_with_retry_after():
    client = _client(rate_limit_per_min=2)
    # An empty body costs nothing to reject, but still counts against the limit.
    assert [client.post("/api/mockery").status_code for _ in range(2)] == [400, 400]
    resp = client.post("/api/mockery")
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) == 30
    assert resp.headers["content-security-policy"]
    # Monologues share the same budget; cheap endpoints are not limited.
    assert client.post("/api/cats/dog/monologue").status_code == 429
    assert client.get("/api/cats").status_code == 200
    assert client.get("/api/meow").status_code == 200


def test_api_rate_limit_disabled_at_zero():
    client = _client(rate_limit_per_min=0)
    assert {client.post("/api/mockery").status_code for _ in range(50)} == {400}


def test_proxy_headers_only_count_when_trusted():
    untrusted = _client(rate_limit_per_min=1)
    assert (
        untrusted.post("/api/mockery", headers={"x-forwarded-for": "192.0.2.1"}).status_code == 400
    )
    resp = untrusted.post("/api/mockery", headers={"x-forwarded-for": "192.0.2.2"})
    assert resp.status_code == 429  # the header is ignored: same client

    trusted = _client(rate_limit_per_min=1, trust_proxy_headers=True)
    for addr in ["192.0.2.1", "192.0.2.2"]:
        assert trusted.post("/api/mockery", headers={"x-forwarded-for": addr}).status_code == 400
    # A client cannot dodge the limit by prepending its own entry: the proxy's last one counts.
    spoofed = {"x-forwarded-for": "198.51.100.7, 192.0.2.1"}
    assert trusted.post("/api/mockery", headers=spoofed).status_code == 429


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "0")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("MAX_CONCURRENT_RENDERS", "2")
    monkeypatch.setenv("RENDER_WAIT_S", "0.5")
    settings = Settings.from_env()
    assert settings.rate_limit_per_min == 0
    assert settings.trust_proxy_headers is True
    assert settings.max_concurrent_renders == 2
    assert settings.render_wait_s == 0.5
    monkeypatch.delenv("TRUST_PROXY_HEADERS")
    assert Settings.from_env().trust_proxy_headers is False


# Concurrency cap


def test_returns_503_when_every_render_slot_is_busy(monkeypatch):
    started, release = threading.Event(), threading.Event()
    real_mock = web.mock

    def slow_mock(*args):
        started.set()
        release.wait(10)
        return real_mock(*args)

    monkeypatch.setattr(web, "mock", slow_mock)
    client = _client(max_concurrent_renders=1, render_wait_s=0.2)
    wav = encode_wav(speech_like([150]), RATE)
    first = {}
    worker = threading.Thread(
        target=lambda: first.update(resp=client.post("/api/mockery?seed=1", content=wav))
    )
    worker.start()
    try:
        assert started.wait(10)
        busy = client.post("/api/mockery?seed=1", content=wav)
        assert busy.status_code == 503
        assert busy.headers["retry-after"] == "2"
        # Monologues share the slots.
        assert client.post("/api/cats/grumpy/monologue?seed=1").status_code == 503
    finally:
        release.set()
        worker.join(10)
    assert first["resp"].status_code == 200
    # The slot is free again afterwards.
    assert client.post("/api/cats/grumpy/monologue?seed=1").status_code == 200


# Security headers

STATIC = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
}


@pytest.mark.parametrize("path", ["/", "/healthz", "/api/cats", "/api/meow", "/nope"])
def test_security_headers_on_every_response(path):
    resp = _client().get(path)
    for name, value in STATIC.items():
        assert resp.headers[name] == value
    assert resp.headers["permissions-policy"].startswith("microphone=(self)")
    csp = resp.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "media-src 'self' data: blob:" in csp
    assert "unsafe-inline" not in csp


def test_page_inline_code_carries_a_fresh_nonce():
    client = _client()
    nonces = []
    for _ in range(2):
        resp = client.get("/")
        csp = resp.headers["content-security-policy"]
        nonce = re.search(r"script-src 'self' 'nonce-([\w-]+)'", csp).group(1)
        assert f"style-src 'self' 'nonce-{nonce}'" in csp
        assert "https://fonts.googleapis.com" in csp and "https://fonts.gstatic.com" in csp
        tags = re.findall(r"<(?:script|style)\b[^>]*>", resp.text)
        assert tags and all(f'nonce="{nonce}"' in t for t in tags)
        nonces.append(nonce)
    assert nonces[0] != nonces[1]


def test_apply_nonce_tags_every_script_and_style():
    html = '<style>a{}</style><SCRIPT src="/x.js"></SCRIPT><script>1</script><scripts>'
    assert apply_nonce(html, "n") == (
        '<style nonce="n">a{}</style><SCRIPT nonce="n" src="/x.js"></SCRIPT>'
        '<script nonce="n">1</script><scripts>'
    )


def test_api_docs_get_their_own_policy():
    resp = _client().get("/api/docs")
    assert resp.status_code == 200
    assert "https://cdn.jsdelivr.net" in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"


# Access log


def test_one_access_line_per_request_without_client_details(caplog):
    client = _client()
    with caplog.at_level(logging.INFO, logger="meow_now.access"):
        client.get("/api/meow?seed=3", headers={"x-forwarded-for": "192.0.2.9"})
        client.post("/api/mockery", content=b"secret-body")
    records = [r for r in caplog.records if r.name == "meow_now.access"]
    assert len(records) == 2
    lines = [json.loads(JsonFormatter().format(r)) for r in records]
    assert [(e["method"], e["path"], e["status"]) for e in lines] == [
        ("GET", "/api/meow", 200),
        ("POST", "/api/mockery", 415),
    ]
    for entry in lines:
        assert set(entry) == {
            "ts",
            "level",
            "logger",
            "msg",
            "method",
            "path",
            "status",
            "duration_ms",
        }
        assert entry["duration_ms"] >= 0
        text = json.dumps(entry)
        assert "testclient" not in text and "192.0.2.9" not in text
        assert "seed" not in text and "secret" not in text
