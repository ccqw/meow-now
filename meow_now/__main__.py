"""`meow-now` / `python -m meow_now`: serve the app, or meow-ify a file offline."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Loopback unless told otherwise. The container image sets HOST=0.0.0.0,
# because there the port is only reachable through the container runtime.
DEFAULT_HOST = "127.0.0.1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meow-now")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the web app (the default)")
    serve.add_argument("--host", default=os.environ.get("HOST", DEFAULT_HOST))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))

    convert = sub.add_parser("mock", help="turn a WAV recording into meows")
    convert.add_argument("input", type=Path)
    convert.add_argument("output", type=Path)

    args = parser.parse_args(argv)
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if args.command == "mock":
        logging.basicConfig(level=level, format="%(levelname)s %(name)s %(message)s")
    else:
        from meow_now.middleware import JsonFormatter

        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.basicConfig(level=level, handlers=[handler])

    if args.command == "mock":
        from meow_now.audio import decode_wav, encode_wav
        from meow_now.mockery import mock

        samples, rate = decode_wav(args.input.read_bytes())
        result = mock(samples, rate)
        args.output.write_bytes(encode_wav(result.audio))
        print(f"{len(result.meows)} meows -> {args.output}")
        return 0

    import uvicorn

    from meow_now.web import create_app

    host = getattr(args, "host", os.environ.get("HOST", DEFAULT_HOST))
    port = getattr(args, "port", int(os.environ.get("PORT", "8080")))
    # uvicorn's own access log includes the client address; ours (JSON, no address)
    # replaces it, and log_config=None sends uvicorn's other logs through the JSON handler.
    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        proxy_headers=True,
        access_log=False,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
