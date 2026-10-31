"""Who is acting: the principal of the current request.

The auth middleware (app/auth/middleware.py) resolves it once per request and
stores it in a context variable, so ``load_investigation``, the audit log and
the scans and agents a request starts (asyncio tasks copy the context) all see
the same user and workspace without threading it through every call.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace

ROLES = ("viewer", "analyst", "admin", "owner")
_RANK = {r: i for i, r in enumerate(ROLES)}


def role_at_least(role: str | None, minimum: str) -> bool:
    return role is not None and _RANK.get(role, -1) >= _RANK[minimum]


def lower_role(a: str, b: str) -> str:
    return a if _RANK[a] <= _RANK[b] else b


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    email: str
    name: str
    org_id: str | None            # active workspace; None until the user has one
    role: str | None              # role in the active workspace
    platform_admin: bool = False
    via: str = "session"          # session | token | api_key | local
    session_id: str | None = None

    @property
    def label(self) -> str:
        """The audit log's "who"."""
        return self.email or self.name or self.user_id

    def can(self, minimum: str) -> bool:
        return role_at_least(self.role, minimum)

    def with_org(self, org_id: str | None, role: str | None) -> "Principal":
        return replace(self, org_id=org_id, role=role)


_current: ContextVar[Principal | None] = ContextVar("osint_principal", default=None)


def current_principal() -> Principal | None:
    return _current.get()


def set_principal(p: Principal | None):
    return _current.set(p)


def reset_principal(token) -> None:
    _current.reset(token)
