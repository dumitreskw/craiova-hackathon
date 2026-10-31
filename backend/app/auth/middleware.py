"""Authentication for every API and MCP request (pure ASGI, so the principal's
context variable reaches the endpoint, its streaming body and the tasks it starts).

Public: the landing page and app shell (static files), ``/auth/*``,
``/invite/*`` (handles its own sign-in), ``/api/v1/health`` and the Kali worker
API (``/api/v1/worker/*``, which checks its own shared token).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .context import reset_principal, set_principal
from .service import Auth

_PUBLIC_API = ("/api/v1/health", "/api/v1/worker/")
_SAFE = {"GET", "HEAD", "OPTIONS"}
# Self-service account endpoints check roles themselves (a viewer can still
# switch workspace, create one, or leave one).
_ACCOUNT = ("/api/v1/me", "/api/v1/orgs")


def protected(path: str) -> bool:
    if path.startswith("/api/"):
        return not path.startswith(_PUBLIC_API)
    return path == "/mcp" or path.startswith("/mcp/")


def _origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def get_auth(app) -> Auth:
    """The app's Auth, built on first use (tests skip the lifespan)."""
    state = app.state
    auth = getattr(state, "auth", None)
    if auth is None:
        from .settings import load_auth_settings
        settings = getattr(state, "auth_settings", None) or load_auth_settings()
        auth = state.auth = Auth(settings, state.platform.db)
    return auth


class AuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not protected(scope["path"]):
            await self.app(scope, receive, send)
            return
        auth = get_auth(scope["app"])
        conn = HTTPConnection(scope)
        principal = await auth.resolve(conn)
        problem = None
        if principal is None:
            problem = (401, "Not signed in.")
        elif scope["method"] not in _SAFE:
            if principal.via == "session" and not self._same_origin(auth, conn):
                problem = (403, "Cross-site request refused.")
            elif principal.role == "viewer" and not scope["path"].startswith(_ACCOUNT):
                problem = (403, "Viewers have read-only access to this workspace.")
        if problem:
            resp = JSONResponse({"detail": problem[1]}, status_code=problem[0],
                                headers={"WWW-Authenticate": "Bearer"} if problem[0] == 401 else None)
            await resp(scope, receive, send)
            return
        scope.setdefault("state", {})["principal"] = principal
        token = set_principal(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_principal(token)

    @staticmethod
    def _same_origin(auth: Auth, conn: HTTPConnection) -> bool:
        """Cookie-authenticated writes must come from our own pages."""
        origin = conn.headers.get("origin")
        if origin:
            allowed = {_origin(str(conn.base_url))}
            if auth.s.public_url:
                allowed.add(_origin(auth.s.public_url))
            return origin.lower() in allowed
        return conn.headers.get("sec-fetch-site", "same-origin") in ("same-origin", "none")
