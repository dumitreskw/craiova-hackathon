"""Resolves who is calling, from a session cookie, a workspace API key or a
Keycloak access token, and completes sign-ins."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from starlette.requests import HTTPConnection

from ..models import Database, now
from .context import Principal
from .oidc import OIDCClient, OIDCError
from .settings import AuthSettings
from .store import API_KEY_PREFIX, TenancyStore

log = logging.getLogger(__name__)

SESSION_COOKIE = "osint_session"
STATE_COOKIE = "osint_auth_state"
PLATFORM_ADMIN_ROLE = "platform-admin"


def is_platform_admin(claims: dict) -> bool:
    return PLATFORM_ADMIN_ROLE in (claims.get("realm_access") or {}).get("roles", [])


def _age_seconds(ts: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()


def display_name(claims: dict) -> str:
    full = " ".join(p for p in (claims.get("given_name"), claims.get("family_name")) if p)
    return claims.get("name") or full or claims.get("preferred_username") or ""


class Auth:
    def __init__(self, settings: AuthSettings, db: Database, oidc: OIDCClient | None = None):
        self.s = settings
        self.store = TenancyStore(db)
        self.oidc = oidc or (OIDCClient(settings) if settings.enabled else None)

    @property
    def enabled(self) -> bool:
        return self.s.enabled

    def base_url(self, conn: HTTPConnection) -> str:
        return self.s.public_url or str(conn.base_url).rstrip("/")

    async def aclose(self) -> None:
        if self.oidc:
            await self.oidc.aclose()

    # --- per request -------------------------------------------------------------

    async def resolve(self, conn: HTTPConnection) -> Principal | None:
        if not self.enabled:
            return self.store.local_principal()
        authz = conn.headers.get("authorization", "")
        if authz[:7].lower() == "bearer ":
            token = authz[7:].strip()
            if token.startswith(API_KEY_PREFIX):
                return self.store.principal_for_api_key(token)
            return await self._from_access_token(token, conn.headers.get("x-org"))
        cookie = conn.cookies.get(SESSION_COOKIE)
        return await self._from_session(cookie) if cookie else None

    async def _from_access_token(self, token: str, org: str | None) -> Principal | None:
        try:
            claims = await self.oidc.verify(token, kind="access")
        except OIDCError:
            return None
        user = self.store.db.one("SELECT * FROM users WHERE sub = ?", (claims["sub"],))
        if user is None:
            user = self.store.upsert_user(
                sub=claims["sub"], email=claims.get("email") or claims.get("preferred_username", ""),
                name=display_name(claims), platform_admin=is_platform_admin(claims))
        org_id, role = self.store.pick_org(user, org)
        return Principal(user_id=user["id"], email=user["email"], name=user["name"],
                         org_id=org_id, role=role, platform_admin=is_platform_admin(claims),
                         via="token")

    async def _from_session(self, value: str) -> Principal | None:
        store = self.store
        row = store.session(value)
        if row is None:
            return None
        user = store.user(row["user_id"])
        if user is None:
            store.drop_session(row["id"])
            return None
        updates: dict = {}
        if row["refresh_token"] and _age_seconds(row["checked_at"]) > self.s.recheck_seconds:
            # Still a live Keycloak session? (account disabled, password reset,
            # signed out everywhere -> the refresh fails and so does this session.)
            try:
                tokens = await self.oidc.refresh(row["refresh_token"])
            except httpx.HTTPError as e:
                log.warning("Keycloak unreachable for a session re-check: %s", e)
                tokens = {}
            if tokens is None:
                store.drop_session(row["id"])
                return None
            if tokens:
                try:
                    claims = await self.oidc.verify(tokens["access_token"], kind="access")
                    admin = int(is_platform_admin(claims))
                    if admin != user["platform_admin"]:
                        store.db.update("users", user["id"], {"platform_admin": admin})
                        user["platform_admin"] = admin
                except OIDCError as e:
                    log.warning("refreshed token did not verify: %s", e)
                updates.update(refresh_token=tokens.get("refresh_token") or row["refresh_token"],
                               id_token=tokens.get("id_token") or row["id_token"],
                               checked_at=now())
        org_id, role = store.pick_org(user, row["org_id"])
        if org_id != row["org_id"]:
            updates["org_id"] = org_id
        if updates or _age_seconds(row["last_seen_at"]) > 60:
            store.touch_session(row["id"], **updates)
        return Principal(user_id=user["id"], email=user["email"], name=user["name"],
                         org_id=org_id, role=role, platform_admin=bool(user["platform_admin"]),
                         via="session", session_id=row["id"])

    # --- sign-in -----------------------------------------------------------------

    async def finish_login(self, code: str, state: dict, redirect_uri: str) -> str:
        """Code -> verified tokens -> user (+ first workspace) -> session cookie value."""
        tokens = await self.oidc.exchange(code, state["verifier"], redirect_uri)
        idc = await self.oidc.verify(tokens["id_token"], kind="id", nonce=state["nonce"])
        acc = await self.oidc.verify(tokens["access_token"], kind="access")
        email = idc.get("email") or acc.get("email") or idc.get("preferred_username") or ""
        user = self.store.upsert_user(sub=idc["sub"], email=email, name=display_name(idc),
                                      platform_admin=is_platform_admin(acc))
        if not state["next"].startswith("/invite/"):
            # Joining through an invitation: that workspace is theirs; otherwise
            # every account starts with one of its own.
            self.store.ensure_workspace(user, self.s.default_plan)
        user = self.store.user(user["id"])
        org_id, _ = self.store.pick_org(user)
        value = self.store.create_session(user, org_id, tokens, self.s.session_days)
        self.store.db.audit(user=user["email"], action="auth.login", org_id=org_id,
                            detail={"via": "oidc"})
        return value

    async def logout(self, value: str | None, post_logout: str) -> str:
        """Drop the session; returns where to send the browser (Keycloak's
        end-session endpoint, which signs out there too, then comes back)."""
        session = self.store.session(value) if value else None
        row = self.store.drop_session(session["id"]) if session else None
        if not self.enabled:
            return post_logout
        if row:
            user = self.store.user(row["user_id"])
            self.store.db.audit(user=user["email"] if user else row["user_id"],
                                action="auth.logout", org_id=row["org_id"])
        try:
            return await self.oidc.logout_url(row["id_token"] if row else None, post_logout)
        except httpx.HTTPError:
            return post_logout
