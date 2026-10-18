"""HTTP API and the single-page UI."""

from __future__ import annotations

import base64
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from meow_now import __version__, cats, meows
from meow_now.audio import SAMPLE_RATE, AudioError, decode_wav, encode_wav
from meow_now.limits import RateLimiter, RenderSlots, rate_limit
from meow_now.middleware import (
    AccessLogMiddleware,
    SecurityHeadersMiddleware,
    apply_nonce,
    csp_nonce,
)
from meow_now.mockery import MAX_INPUT_S, mock

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@dataclass(frozen=True)
class Settings:
    ollama_url: str | None
    ollama_model: str
    max_upload_bytes: int
    rate_limit_per_min: int = 30
    trust_proxy_headers: bool = False
    max_concurrent_renders: int = 4
    render_wait_s: float = 5.0

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            ollama_url=os.environ.get("OLLAMA_URL") or None,
            ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
            max_upload_bytes=int(float(os.environ.get("MAX_UPLOAD_MB", "8")) * 1024 * 1024),
            rate_limit_per_min=int(os.environ.get("RATE_LIMIT_PER_MIN", "30")),
            trust_proxy_headers=os.environ.get("TRUST_PROXY_HEADERS", "").lower()
            in ("1", "true", "yes"),
            max_concurrent_renders=int(os.environ.get("MAX_CONCURRENT_RENDERS", "4")),
            render_wait_s=float(os.environ.get("RENDER_WAIT_S", "5")),
        )


def _data_url(samples) -> str:
    return "data:audio/wav;base64," + base64.b64encode(encode_wav(samples)).decode("ascii")


async def _read_capped(request: Request, limit: int) -> bytes:
    """The request body, or 413 as soon as it is known to exceed `limit` bytes."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "recording too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(413, "recording too large")
    return bytes(body)


def _cat_or_404(cat_id: str, seed: int) -> cats.Cat:
    """The cat called `cat_id`; `random` picks one from the seed."""
    try:
        return cats.get_cat(cat_id, np.random.default_rng(seed))
    except KeyError:
        raise HTTPException(404, f"no cat called {cat_id!r}") from None


def _cat_json(cat: cats.Cat) -> dict:
    return {"id": cat.id, "name": cat.name, "tagline": cat.tagline}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="meow-now", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)  # added last, so outermost: it times everything
    limited = [
        Depends(rate_limit(RateLimiter(settings.rate_limit_per_min), settings.trust_proxy_headers))
    ]
    renders = RenderSlots(settings.max_concurrent_renders, settings.render_wait_s)
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        return HTMLResponse(apply_nonce(page, csp_nonce(request)))

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/cats")
    def list_cats() -> dict:
        return {
            "cats": [
                {"id": c.id, "name": c.name, "tagline": c.tagline} for c in cats.CATS.values()
            ],
            "ollama": settings.ollama_url is not None,
        }

    @app.post("/api/mockery", dependencies=limited)
    async def mockery(
        request: Request,
        seed: int | None = Query(None, ge=0),
        cat: str | None = Query(None, description="a cat id, or random, to mock you in its voice"),
    ) -> JSONResponse:
        """Body: a PCM WAV recording (Content-Type audio/wav), up to 60 seconds."""
        seed = secrets.randbelow(1 << 30) if seed is None else seed
        mocker = _cat_or_404(cat, seed) if cat else None
        body = await _read_capped(request, settings.max_upload_bytes)
        if not body:
            raise HTTPException(400, "send a WAV recording as the request body")
        try:
            samples, rate = decode_wav(body)
        except AudioError as exc:
            raise HTTPException(415, str(exc)) from exc
        if rate < 8000:
            raise HTTPException(415, f"sample rate {rate} Hz is too low; use 8000 Hz or more")

        async with renders.slot():
            result = await run_in_threadpool(mock, samples, rate, seed, mocker)
        return JSONResponse(
            {
                "audio": _data_url(result.audio),
                "seed": seed,
                "cat": _cat_json(mocker) if mocker else None,
                "input_seconds": round(min(result.analysis.duration, MAX_INPUT_S), 2),
                "truncated": result.analysis.duration > MAX_INPUT_S,
                "meows": [
                    {
                        "start": round(s.start, 3),
                        "duration": round(m.duration, 3),
                        "pitch_hz": round(m.pitch, 1),
                        "your_pitch_hz": round(s.pitch, 1) if s.pitch else None,
                        "kind": m.kind,
                        "contour": [round(c, 3) for c in s.contour],
                        "loudness": round(s.loudness, 3),
                    }
                    for s, m in zip(result.analysis.syllables, result.meows, strict=True)
                ],
                "reaction": result.reaction,
                "caption": result.caption,
                "reaction_meows": [
                    {
                        "start": round(start, 3),
                        "duration": round(m.duration, 3),
                        "pitch_hz": round(m.pitch, 1),
                        "kind": m.kind,
                    }
                    for start, m in result.extras
                ],
            }
        )

    @app.post("/api/cats/{cat_id}/monologue", dependencies=limited)
    async def cat_monologue(cat_id: str, seed: int | None = Query(None, ge=0)) -> JSONResponse:
        """A monologue from one cat; `random` picks one."""
        seed = secrets.randbelow(1 << 30) if seed is None else seed
        cat = _cat_or_404(cat_id, seed)
        # The Ollama call is network wait, not CPU, so it does not hold a render slot.
        text, source = await run_in_threadpool(
            cats.monologue_text, cat, seed, settings.ollama_url, settings.ollama_model
        )
        async with renders.slot():
            result = await run_in_threadpool(cats.render_monologue, cat, text, source, seed)
        return JSONResponse(
            {
                "cat": _cat_json(cat),
                "text": result.text,
                "source": result.source,
                "words": [
                    {
                        "word": w.word,
                        "start": w.start,
                        "duration": round(w.duration, 3),
                        "kind": w.kind,
                    }
                    for w in result.words
                ],
                "audio": _data_url(result.audio),
            }
        )

    @app.get("/api/meow")
    def one_meow(
        pitch: float = Query(600.0, ge=200, le=1500),
        duration: float = Query(0.5, ge=0.08, le=2.0),
        kind: str = Query("meow", pattern="^(" + "|".join(meows.KINDS) + ")$"),
        seed: int = Query(0, ge=0),
    ) -> Response:
        """A single meow as audio/wav, for the soundboard (or your ringtone)."""
        clip = meows.synthesize(meows.Meow(pitch=pitch, duration=duration, kind=kind), seed=seed)
        return Response(encode_wav(clip, SAMPLE_RATE), media_type="audio/wav")

    return app
