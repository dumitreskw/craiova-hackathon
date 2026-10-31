"""Browser sign-in: ``/auth/login``, ``/auth/register``, ``/auth/callback``,
``/auth/logout`` and invitation links (``/invite/{token}``)."""

from __future__ import annotations

import html
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .middleware import get_auth
from .oidc import OIDCError, pkce_pair
from .service import SESSION_COOKIE, STATE_COOKIE
from .store import TenancyError

router = APIRouter(include_in_schema=False)

APP_HOME = "/app/"


def safe_next(raw: str | None) -> str:
    """Only same-site paths: no scheme, no ``//host``, no backslash tricks."""
    if not raw or not raw.startswith("/") or raw.startswith(("//", "/\\")) or "\\" in raw:
        return APP_HOME
    return raw


def _message(title: str, body: str, action: tuple[str, str] = ("/", "Back to the home page"),
             status: int = 400) -> HTMLResponse:
    t, b = html.escape(title), html.escape(body)
    href, label = html.escape(action[0], quote=True), html.escape(action[1])
    return HTMLResponse(status_code=status, content=f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{t} · Cybnetic</title><style>
:root{{color-scheme:dark light;--bg:#161d25;--panel:#1c252f;--ink:#e8edf2;--muted:#a0aebd;--line:#374554;--accent:#f0b44c}}
@media (prefers-color-scheme:light){{:root{{--bg:#eef1f5;--panel:#fff;--ink:#151d26;--muted:#505d6c;--line:#cbd3dd;--accent:#e8a634}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
font:15px/1.55 "IBM Plex Sans",system-ui,sans-serif;padding:16px}}
main{{max-width:420px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:28px}}
h1{{font-size:20px;margin:0 0 8px}}p{{color:var(--muted);margin:0 0 20px}}
a{{display:inline-block;background:var(--accent);color:#231803;text-decoration:none;font-weight:600;
padding:8px 14px;border-radius:6px}}</style></head>
<body><main><h1>{t}</h1><p>{b}</p><a href="{href}">{label}</a></main></body></html>""")


def _cookie_args(request: Request, max_age: int, path: str = "/") -> dict:
    auth = get_auth(request.app)
    secure = auth.s.secure_cookies or request.url.scheme == "https"
    return {"max_age": max_age, "httponly": True, "secure": secure, "samesite": "lax", "path": path}


async def _start(request: Request, next_path: str | None, register: bool,
                 login_hint: str | None = None):
    auth = get_auth(request.app)
    nxt = safe_next(next_path)
    if not auth.enabled:
        return RedirectResponse(nxt, status_code=303)
    verifier, challenge = pkce_pair()
    nonce = secrets.token_urlsafe(24)
    state = auth.store.new_auth_state(verifier, nonce, nxt)
    try:
        url = await auth.oidc.authorize_url(
            redirect_uri=f"{auth.base_url(request)}/auth/callback", state=state, nonce=nonce,
            challenge=challenge, register=register, login_hint=login_hint)
    except Exception:  # discovery failed: Keycloak down or misconfigured
        return _message("Sign-in is unavailable",
                        "The identity service did not answer. Try again in a minute.",
                        status=503)
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(STATE_COOKIE, state, **_cookie_args(request, 900, "/auth"))
    return resp


@router.get("/auth/login")
async def login(request: Request, next: str | None = None, login_hint: str | None = None):
    return await _start(request, next, register=False, login_hint=login_hint)


@router.get("/auth/register")
async def register(request: Request, next: str | None = None):
    return await _start(request, next, register=True)


@router.get("/auth/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None,
                   error: str | None = None, error_description: str | None = None):
    auth = get_auth(request.app)
    if not auth.enabled:
        return RedirectResponse(APP_HOME, status_code=303)
    retry = ("/auth/login", "Sign in again")
    if error:
        return _message("Sign-in was not completed", error_description or error, retry)
    cookie = request.cookies.get(STATE_COOKIE)
    if not (code and state and cookie and secrets.compare_digest(state, cookie)):
        return _message("Sign-in expired", "This sign-in link is stale or came from another "
                        "browser. Start again.", retry)
    row = auth.store.pop_auth_state(state)
    if row is None:
        return _message("Sign-in expired", "That took too long. Start again.", retry)
    try:
        value = await auth.finish_login(code, row, f"{auth.base_url(request)}/auth/callback")
    except (OIDCError, KeyError) as e:
        return _message("Sign-in failed", f"The identity service's answer was not accepted ({e}).",
                        retry)
    resp = RedirectResponse(row["next"], status_code=303)
    resp.set_cookie(SESSION_COOKIE, value,
                    **_cookie_args(request, auth.s.session_days * 86400))
    resp.delete_cookie(STATE_COOKIE, path="/auth")
    return resp


@router.api_route("/auth/logout", methods=["GET", "POST"])
async def logout(request: Request):
    auth = get_auth(request.app)
    target = await auth.logout(request.cookies.get(SESSION_COOKIE), f"{auth.base_url(request)}/")
    resp = RedirectResponse(target, status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/invite/{token}")
async def accept_invite(token: str, request: Request):
    auth = get_auth(request.app)
    principal = await auth.resolve(request)
    if principal is None:
        # Most invitees are new: open registration (it links to sign-in too).
        return RedirectResponse(f"/auth/register?next=/invite/{token}", status_code=303)
    user = auth.store.user(principal.user_id)
    try:
        org_id = auth.store.accept_invitation(token, user)
    except TenancyError as e:
        return _message("Invitation not accepted", str(e), (APP_HOME, "Open the platform"),
                        status=e.status)
    auth.store.switch_org(principal, org_id)
    return RedirectResponse(APP_HOME, status_code=303)
