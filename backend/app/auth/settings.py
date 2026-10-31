"""Sign-in settings, from the environment.

Production sets ``OSINT_OIDC_ISSUER`` (the Keycloak realm URL) and the client
credentials; without an issuer, auth is off and every request acts as one local
owner (laptop development and the test suite).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuthSettings:
    # e.g. https://auth.dumisoft.ro/realms/osint ; None = auth off.
    issuer: str | None = None
    client_id: str = "osint-web"
    client_secret: str | None = None
    # Where the platform is reached, e.g. https://osint.dumisoft.ro (redirect
    # URIs, cookie Secure flag, Origin check). None = taken from each request.
    public_url: str | None = None
    session_days: int = 14
    # Re-check a session against Keycloak (refresh grant) this often, so a
    # disabled account or a logout elsewhere ends the session within minutes.
    recheck_seconds: int = 300
    # Plan of a workspace created at sign-up (app/auth/plans.py).
    default_plan: str = "free"
    # Internal URL of the MCP server the app proxies /mcp to (authenticated).
    mcp_upstream: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.issuer)

    @property
    def secure_cookies(self) -> bool:
        return bool(self.public_url and self.public_url.startswith("https://"))

    @property
    def account_url(self) -> str | None:
        """Keycloak's account console (profile, password, sessions, 2FA)."""
        return f"{self.issuer}/account" if self.issuer else None

    @property
    def admin_base(self) -> str | None:
        """Keycloak admin REST API base for this realm."""
        return self.issuer.replace("/realms/", "/admin/realms/", 1) if self.issuer else None


def load_auth_settings() -> AuthSettings:
    # The repo .env (local runs) is already loaded by the platform's settings
    # (app/config.py), which the lifespan builds first.
    env = os.environ.get
    issuer = (env("OSINT_OIDC_ISSUER") or "").rstrip("/") or None
    s = AuthSettings(
        issuer=issuer,
        client_id=env("OSINT_OIDC_CLIENT_ID") or "osint-web",
        client_secret=env("OSINT_OIDC_CLIENT_SECRET") or None,
        public_url=(env("OSINT_PUBLIC_URL") or "").rstrip("/") or None,
        session_days=int(env("OSINT_SESSION_DAYS") or 14),
        default_plan=env("OSINT_DEFAULT_PLAN") or "free",
        mcp_upstream=(env("OSINT_MCP_UPSTREAM") or "").rstrip("/") or None,
    )
    if not s.enabled:
        log.warning("OSINT_OIDC_ISSUER is not set: sign-in is OFF and every request acts "
                    "as the local owner. Never run like this on a public host.")
    return s
