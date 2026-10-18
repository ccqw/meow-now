"""ASGI middleware: security headers on every response, and one JSON access log line per request."""

from __future__ import annotations

import json
import logging
import re
import secrets
import time

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

access_logger = logging.getLogger("meow_now.access")

# The page is one file with its script and style inline. A hash per block would
# change with every edit to the page and is easy to forget, so each response
# gets a fresh nonce instead: the middleware puts it in the CSP header and the
# index route stamps it on the page's <script> and <style> tags (see
# `apply_nonce`). Nothing else on the page can run inline code.
PAGE_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'nonce-{nonce}'",
        "style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        # Results come back as data: URLs; recordings may be played from blob: URLs.
        "media-src 'self' data: blob:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

# FastAPI's interactive docs load Swagger UI from a CDN and boot it with an
# inline script we do not render, so those two pages get their own, looser policy.
DOCS_PATHS = ("/api/docs", "/api/docs/oauth2-redirect")
DOCS_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "img-src 'self' data: https://fastapi.tiangolo.com",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ]
)

STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=(), usb=()",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}

_OPEN_TAG = re.compile(r"<(script|style)(?=[\s>])", re.IGNORECASE)


def csp_nonce(request: Request) -> str:
    """The nonce the CSP header of this response allows."""
    return request.state.csp_nonce


def apply_nonce(html: str, nonce: str) -> str:
    """Stamp `nonce` on every <script> and <style> tag in `html`."""
    return _OPEN_TAG.sub(lambda m: f'<{m.group(1)} nonce="{nonce}"', html)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        nonce = secrets.token_urlsafe(18)
        scope.setdefault("state", {})["csp_nonce"] = nonce
        csp = DOCS_CSP if scope["path"] in DOCS_PATHS else PAGE_CSP.format(nonce=nonce)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = csp
                for name, value in STATIC_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class AccessLogMiddleware:
    """Logs method, path, status and duration. Never the client address, query or body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 500

        async def send_and_record(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_and_record)
        finally:
            access_logger.info(
                "request",
                extra={
                    "http": {
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": status,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    }
                },
            )


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Access lines carry their request fields at the top level."""

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update(getattr(record, "http", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, separators=(",", ":"))
