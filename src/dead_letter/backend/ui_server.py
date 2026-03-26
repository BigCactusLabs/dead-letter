"""UI server entrypoint for dead-letter."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from dead_letter.backend.api import create_app


class _NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response


def _default_frontend_root() -> Path:
    return Path(__file__).resolve().parent.parent / "frontend"


def create_ui_app(*, frontend_root: Path | None = None):
    app = create_app()
    root = (frontend_root or _default_frontend_root()).resolve()

    app.add_middleware(_NoCacheMiddleware)
    app.mount("/static", StaticFiles(directory=root / "static"), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(root / "index.html")

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dead-letter-ui",
        description="Run the local dead-letter UI server.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8765, help="Bind port")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    uvicorn.run(create_ui_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
