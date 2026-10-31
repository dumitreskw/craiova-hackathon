"""Workspace plans and their limits. ``None`` = unlimited.

A platform admin sets a workspace's plan (``PATCH /api/v1/platform/orgs/{id}``);
billing can drive the same field later.
"""

from __future__ import annotations

PLANS: dict[str, dict] = {
    # No free tier: new workspaces start on a limited trial until a plan is set.
    "free": {"label": "Trial", "seats": 3, "investigations": 5, "api_keys": 2},
    "team": {"label": "Team", "seats": 15, "investigations": 100, "api_keys": 20},
    "enterprise": {"label": "Enterprise", "seats": None, "investigations": None,
                   "api_keys": None},
}

# Workspaces one user may own (stops sign-up loops from minting free quota).
MAX_OWNED_ORGS = 5


def limits(plan: str) -> dict:
    return PLANS.get(plan) or PLANS["free"]


class LimitReached(Exception):
    pass


def check(plan: str, what: str, used: int) -> None:
    cap = limits(plan).get(what)
    if cap is not None and used >= cap:
        raise LimitReached(f"The {limits(plan)['label']} plan allows {cap} {what.replace('_', ' ')}; "
                           "remove one or ask for a bigger plan.")
