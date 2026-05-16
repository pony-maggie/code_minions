"""Code Minions Web — localhost-only dashboard.

Entry: `code-minions web`. Do not deploy this unauthenticated to a public host.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from code_minions.web.routes import events as events_routes
from code_minions.web.routes import runs
from code_minions.web.routes import start as start_routes

_WEB_ROOT = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    app = FastAPI(
        title="code-minions web",
        description="Localhost-only dashboard for code-minions runs.",
        version="0.1.0",
    )

    @app.middleware("http")
    async def inject_local_user(request: Request, call_next):
        request.state.user = "local"
        expected_token = os.environ.get("CODE_MINIONS_WEB_AUTH_TOKEN")
        if expected_token:
            auth = request.headers.get("authorization", "")
            token = request.headers.get("x-code-minions-token", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            if not secrets.compare_digest(token, expected_token):
                return PlainTextResponse(
                    "unauthorized",
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    templates = Jinja2Templates(directory=str(_WEB_ROOT / "templates"))
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(_WEB_ROOT / "static")), name="static")

    app.include_router(runs.router)
    app.include_router(events_routes.router)
    app.include_router(start_routes.router)

    @app.on_event("startup")
    def _startup() -> None:
        from code_minions.web.background import scan_orphans
        from code_minions.web.deps import _project_root, get_store
        pid_file = _project_root() / ".devflow" / "web.pid"
        scan_orphans(store=get_store(), pid_file=pid_file)

    return app


app = create_app()
