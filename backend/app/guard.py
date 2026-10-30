"""Authorization gate for connector calls (docs/06-legal-ethics.md).

Every connector call goes through :func:`check`. Passive connectors are always
allowed (subject to the investigation's connector allow/deny lists). An
**active** connector — one that sends traffic to the target's own
infrastructure — runs only when all of these hold:

1. the investigation is in ``active`` mode,
2. it records who authorized the work (``authorization``), and
3. the host being touched is in **this investigation's** scope list (the host
   itself or a parent domain).

The scope is per investigation on purpose. Taking the union of every
engagement's scope (as the Kali workspace's ``scope.txt`` gate did) lets an
analyst scan a host for case B because case A authorized it.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse


class GateError(PermissionError):
    """Raised when a connector call is not allowed. The message is user-facing."""


def host_of(target: str) -> str:
    t = target.strip().lower()
    if "://" in t:
        t = urlparse(t).hostname or ""
    t = t.split("/", 1)[0]
    # strip a port, but leave IPv6 literals alone
    if t.count(":") == 1:
        t = t.split(":", 1)[0]
    return t.strip("[]").rstrip(".")


def in_scope(target: str, scope: list[str]) -> bool:
    """True if ``target`` (or one of its parent domains / an IP in a listed CIDR) is in scope."""
    host = host_of(target)
    if not host:
        return False
    entries = {s.strip().lower().rstrip(".") for s in scope if s.strip()}
    if host in entries:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        parts = host.split(".")
        return any(".".join(parts[i:]) in entries for i in range(1, len(parts) - 1))
    for entry in entries:
        if "/" in entry:
            try:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
    return False


def check(investigation: dict[str, Any], connector: str, passive: bool, target: str) -> None:
    """Raise :class:`GateError` unless ``connector`` may run on ``target``."""
    settings = investigation.get("settings") or {}
    allow = settings.get("connectors") or ["*"]
    if "*" not in allow and connector not in allow:
        raise GateError(f"connector '{connector}' is not enabled for this investigation")
    if connector in (settings.get("exclude_connectors") or []):
        raise GateError(f"connector '{connector}' is excluded for this investigation")
    if passive:
        return
    if investigation.get("mode") != "active":
        raise GateError(
            f"'{connector}' is ACTIVE (it contacts the target). The investigation is "
            "passive; switch it to active mode with a recorded authorization first.")
    if not (investigation.get("authorization") or "").strip():
        raise GateError("active mode needs a recorded authorization (who, when, reference)")
    if not in_scope(target, investigation.get("scope") or []):
        raise GateError(
            f"'{host_of(target)}' is not in this investigation's authorized scope; "
            "add it to the scope (with authorization) before running active connectors")
