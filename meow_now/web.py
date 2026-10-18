"""HTTP API and the single-page UI."""

from __future__ import annotations

import base64
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response

from meow_now import __version__, cats, meows
from meow_now.audio import SAMPLE_RATE, AudioError, decode_wav, encode_wav
from meow_now.mockery import MAX_INPUT_S, mock

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@dataclass(frozen=True)
class Settings:
    ollama_url: str | None
    ollama_model: str
    max_upload_bytes: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            ollama_url=os.environ.get("OLLAMA_URL") or None,
            ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
            max_upload_bytes=int(float(os.environ.get("MAX_UPLOAD_MB", "8")) * 1024 * 1024),
        )


def _data_url(samples) -> str:
    return "data:audio/wav;base64," + base64.b64encode(encode_wav(samples)).decode("ascii")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="meow-now", version=__version__, docs_url="/api/docs", redoc_url=None)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

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

    @app.post("/api/mockery")
    async def mockery(request: Request) -> JSONResponse:
        """Body: a PCM WAV recording (Content-Type audio/wav), up to 60 seconds."""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes:
            raise HTTPException(413, "recording too large")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > settings.max_upload_bytes:
                raise HTTPException(413, "recording too large")
        if not body:
            raise HTTPException(400, "send a WAV recording as the request body")
        try:
            samples, rate = decode_wav(bytes(body))
        except AudioError as exc:
            raise HTTPException(415, str(exc)) from exc
        if rate < 8000:
            raise HTTPException(415, f"sample rate {rate} Hz is too low; use 8000 Hz or more")

        result = await run_in_threadpool(mock, samples, rate, secrets.randbelow(1 << 30))
        return JSONResponse(
            {
                "audio": _data_url(result.audio),
                "input_seconds": round(min(result.analysis.duration, MAX_INPUT_S), 2),
                "truncated": result.analysis.duration > MAX_INPUT_S,
                "meows": [
                    {
                        "start": round(s.start, 3),
                        "duration": round(m.duration, 3),
                        "pitch_hz": round(m.pitch, 1),
                        "your_pitch_hz": round(s.pitch, 1) if s.pitch else None,
                        "kind": m.kind,
                    }
                    for s, m in zip(result.analysis.syllables, result.meows, strict=True)
                ],
            }
        )

    @app.post("/api/cats/{cat_id}/monologue")
    async def cat_monologue(cat_id: str, seed: int | None = Query(None, ge=0)) -> JSONResponse:
        """A monologue from one cat; `random` picks one."""
        seed = secrets.randbelow(1 << 30) if seed is None else seed
        try:
            cat = cats.get_cat(cat_id, np.random.default_rng(seed))
        except KeyError:
            raise HTTPException(404, f"no cat called {cat_id!r}") from None
        result = await run_in_threadpool(
            cats.monologue, cat, seed, settings.ollama_url, settings.ollama_model
        )
        return JSONResponse(
            {
                "cat": {"id": cat.id, "name": cat.name, "tagline": cat.tagline},
                "text": result.text,
                "source": result.source,
                "words": [{"word": w, "start": s} for w, s in result.words],
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
