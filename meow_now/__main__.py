"""`meow-now` / `python -m meow_now`: serve the app, or meow-ify a file offline."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meow-now")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the web app (the default)")
    serve.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))

    convert = sub.add_parser("mock", help="turn a WAV recording into meows")
    convert.add_argument("input", type=Path)
    convert.add_argument("output", type=Path)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

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

    host = getattr(args, "host", os.environ.get("HOST", "0.0.0.0"))
    port = getattr(args, "port", int(os.environ.get("PORT", "8080")))
    uvicorn.run(create_app(), host=host, port=port, proxy_headers=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
