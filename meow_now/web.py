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
from pydantic import BaseModel, Field

from meow_now import __version__, cats, copycat, meows
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

# The longest line /api/speak will read. Anything past MAX_WORDS is cut anyway;
# this only bounds the work of cleaning a body nobody could have typed.
MAX_TYPED_CHARS = 1000


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


class SpeakRequest(BaseModel):
    text: str = Field(max_length=MAX_TYPED_CHARS, description="What the cat should say")
    cat: str = Field("random", description="A cat id, or `random`")


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


async def _read_wav(request: Request, limit: int) -> tuple[np.ndarray, int]:
    """The request body decoded as WAV, or the 400, 413 or 415 that explains why it is not."""
    body = await _read_capped(request, limit)
    if not body:
        raise HTTPException(400, "send a WAV recording as the request body")
    try:
        samples, rate = decode_wav(body)
    except AudioError as exc:
        raise HTTPException(415, str(exc)) from exc
    if rate < 8000:
        raise HTTPException(415, f"sample rate {rate} Hz is too low; use 8000 Hz or more")
    return samples, rate


def _cat_or_404(cat_id: str, seed: int) -> cats.Cat:
    """The cat called `cat_id`; `random` picks one from the seed."""
    try:
        return cats.get_cat(cat_id, np.random.default_rng(seed))
    except KeyError:
        raise HTTPException(404, f"no cat called {cat_id!r}") from None


def _cat_json(cat: cats.Cat) -> dict:
    return {"id": cat.id, "name": cat.name, "tagline": cat.tagline}


def _monologue_json(result: cats.Monologue, seed: int, **extra) -> JSONResponse:
    """The response for anything a cat says: its words with timings, and the audio."""
    return JSONResponse(
        {
            "cat": _cat_json(result.cat),
            "text": result.text,
            "source": result.source,
            "seed": seed,
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
            **extra,
        }
    )


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
        samples, rate = await _read_wav(request, settings.max_upload_bytes)
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
    async def cat_monologue(
        cat_id: str,
        seed: int | None = Query(None, ge=0),
        improvise: bool = Query(True, description="false: always a built-in line"),
    ) -> JSONResponse:
        """A monologue from one cat; `random` picks one.

        A built-in line is fixed by the cat and seed, which is what a shared link
        replays (`improvise=false`, so it holds even when Ollama is configured).
        """
        seed = secrets.randbelow(1 << 30) if seed is None else seed
        cat = _cat_or_404(cat_id, seed)
        # The Ollama call is network wait, not CPU, so it does not hold a render slot.
        text, source = await run_in_threadpool(
            cats.monologue_text,
            cat,
            seed,
            settings.ollama_url if improvise else None,
            settings.ollama_model,
        )
        async with renders.slot():
            result = await run_in_threadpool(cats.render_monologue, cat, text, source, seed)
        return _monologue_json(result, seed)

    @app.post("/api/speak", dependencies=limited)
    async def speak(body: SpeakRequest, seed: int | None = Query(None, ge=0)) -> JSONResponse:
        """Type and the cat says it: your words, in meows, with subtitles.

        The text is cleaned of control characters and capped at the monologue
        word limit (`truncated` says so). It is spoken and sent back, never stored.
        """
        text, truncated = cats.clean_typed(body.text)
        if not text:
            raise HTTPException(422, "give the cat something to say")
        seed = secrets.randbelow(1 << 30) if seed is None else seed
        cat = _cat_or_404(body.cat, seed)
        async with renders.slot():
            result = await run_in_threadpool(cats.render_monologue, cat, text, "typed", seed)
        return _monologue_json(result, seed, truncated=truncated)

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

    copycat_length = Query(
        ..., ge=copycat.MIN_LENGTH, le=copycat.MAX_LENGTH, description="meows in the phrase"
    )
    copycat_cat = Query(None, description="a cat id, or random, to perform the phrase")

    @app.get("/api/copycat/phrase", dependencies=limited)
    async def copycat_phrase(
        length: int = copycat_length,
        seed: int | None = Query(None, ge=0),
        cat: str | None = copycat_cat,
    ) -> JSONResponse:
        """A Copycat phrase to copy: its meows, as heard, and its audio."""
        seed = secrets.randbelow(1 << 30) if seed is None else seed
        performer = _cat_or_404(cat, seed) if cat else None
        phrase = copycat.phrase(seed, length, performer)
        async with renders.slot():
            audio = await run_in_threadpool(copycat.render, phrase)
        return JSONResponse(
            {
                "seed": seed,
                "length": length,
                "cat": _cat_json(performer) if performer else None,
                "duration": round(phrase.duration, 3),
                "pass_score": copycat.PASS_SCORE,
                "meows": [
                    {
                        "start": start,
                        "duration": m.duration,
                        "pitch_hz": round(copycat.target_pitch(m), 1),
                        "kind": m.kind,
                        "contour": [round(c, 3) for c in copycat.target_contour(m)],
                    }
                    for start, m in zip(phrase.starts, phrase.meows, strict=True)
                ],
                "audio": _data_url(audio),
            }
        )

    @app.post("/api/copycat/score", dependencies=limited)
    async def copycat_score(
        request: Request,
        length: int = copycat_length,
        seed: int = Query(..., ge=0, description="the seed the phrase was drawn from"),
        cat: str | None = copycat_cat,
    ) -> JSONResponse:
        """Body: a PCM WAV recording of you copying the phrase, up to 30 seconds."""
        performer = _cat_or_404(cat, seed) if cat else None
        samples, rate = await _read_wav(request, settings.max_upload_bytes)
        phrase = copycat.phrase(seed, length, performer)
        async with renders.slot():
            result = await run_in_threadpool(copycat.score, phrase, samples, rate)

        def num(x: float | None, places: int) -> float | None:
            return None if x is None else round(float(x), places)

        return JSONResponse(
            {
                "seed": seed,
                "length": length,
                "cat": _cat_json(performer) if performer else None,
                "score": result.score,
                "passed": result.passed,
                "pass_score": copycat.PASS_SCORE,
                "extra": result.extra,
                "heard": result.heard,
                "tempo": round(result.tempo, 3),
                "transpose": round(result.transpose, 2),
                "meows": [
                    {
                        "verdict": m.verdict,
                        "score": round(m.score, 3),
                        "onset_error": num(m.onset_error, 3),
                        "pitch_error": num(m.pitch_error, 2),
                        "shape_error": num(m.shape_error, 2),
                        "heard": None
                        if m.heard_start is None
                        else {
                            "start": m.heard_start,
                            "duration": m.heard_duration,
                            "pitch_hz": m.heard_pitch,
                            "contour": [round(c, 3) for c in m.heard_contour],
                        },
                    }
                    for m in result.meows
                ],
            }
        )

    return app
