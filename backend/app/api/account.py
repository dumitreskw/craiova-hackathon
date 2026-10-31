"""Accounts and workspaces: who am I, workspace switching, members and roles,
invitations, accounts created by an admin (in Keycloak), API keys, and the
platform admin's view of every workspace."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import plans
from ..auth.context import ROLES, Principal
from ..auth.middleware import get_auth
from ..auth.oidc import AccountExists, OIDCError
from ..auth.store import TenancyError, TenancyStore
from .deps import get_principal, require_org, require_platform_admin, require_role

router = APIRouter(tags=["account"])

_ROLE_PATTERN = "^(" + "|".join(ROLES) + ")$"
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def RoleField(default=...):
    return Field(default, pattern=_ROLE_PATTERN)


def EmailField():
    return Field(min_length=3, max_length=254, pattern=_EMAIL_PATTERN)


def _store(request: Request) -> TenancyStore:
    return get_auth(request.app).store


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except TenancyError as e:
        raise HTTPException(e.status, str(e)) from e


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class OrgSwitch(BaseModel):
    org_id: str


class OrgRename(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class RoleChange(BaseModel):
    role: str = RoleField()


class InvitationCreate(BaseModel):
    email: str = EmailField()
    role: str = RoleField()


class AccountCreate(BaseModel):
    email: str = EmailField()
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field("", max_length=60)
    role: str = RoleField()


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    role: str | None = RoleField(None)


class OrgAdminUpdate(BaseModel):
    plan: str | None = None
    disabled: bool | None = None


# --- me ------------------------------------------------------------------------------

@router.get("/me")
async def me(request: Request, who: Principal = Depends(get_principal)):
    auth = get_auth(request.app)
    store = auth.store
    org = store.org(who.org_id) if who.org_id else None
    return {
        "user": {"id": who.user_id, "email": who.email, "name": who.name,
                 "platform_admin": who.platform_admin, "via": who.via},
        "org": org and {"id": org["id"], "name": org["name"], "plan": org["plan"],
                        "role": who.role, "limits": plans.limits(org["plan"]),
                        "usage": store.usage(org["id"])},
        "orgs": [{"id": o["id"], "name": o["name"], "role": o["role"], "plan": o["plan"]}
                 for o in store.orgs_of(who.user_id) if not o["disabled_at"]],
        "auth": {"enabled": auth.enabled, "account_url": auth.s.account_url,
                 "mcp": bool(auth.s.mcp_upstream)},
    }


@router.post("/me/org")
async def switch_org(body: OrgSwitch, request: Request, who: Principal = Depends(get_principal)):
    role = _guard(_store(request).switch_org, who, body.org_id)
    return {"org_id": body.org_id, "role": role}


@router.get("/plans")
async def list_plans():
    return plans.PLANS


# --- workspaces ----------------------------------------------------------------------

@router.post("/orgs", status_code=201)
async def create_org(body: OrgCreate, request: Request, who: Principal = Depends(get_principal)):
    store = _store(request)
    org = _guard(store.create_org, store.user(who.user_id), body.name,
                 get_auth(request.app).s.default_plan)
    store.switch_org(who, org["id"])
    return org


@router.patch("/orgs/current")
async def rename_org(body: OrgRename, request: Request,
                     who: Principal = Depends(require_role("admin"))):
    store = _store(request)
    store.db.update("orgs", who.org_id, {"name": body.name.strip()})
    store.db.audit(user=who.label, action="org.rename", org_id=who.org_id,
                   detail={"name": body.name})
    return store.org(who.org_id)


@router.get("/orgs/current/members")
async def members(request: Request, who: Principal = Depends(require_org)):
    return _store(request).members(who.org_id)


@router.patch("/orgs/current/members/{user_id}")
async def change_role(user_id: str, body: RoleChange, request: Request,
                      who: Principal = Depends(require_role("admin"))):
    _guard(_store(request).set_role, who, user_id, body.role)
    return {"user_id": user_id, "role": body.role}


@router.delete("/orgs/current/members/{user_id}", status_code=204)
async def remove_member(user_id: str, request: Request, who: Principal = Depends(require_org)):
    """Remove a member (admin), or leave the workspace (yourself)."""
    _guard(_store(request).remove_member, who, user_id)


# --- invitations & accounts ----------------------------------------------------------

@router.get("/orgs/current/invitations")
async def invitations(request: Request, who: Principal = Depends(require_role("admin"))):
    return _store(request).invitations(who.org_id)


@router.post("/orgs/current/invitations", status_code=201)
async def invite(body: InvitationCreate, request: Request,
                 who: Principal = Depends(require_role("admin"))):
    """A single-use link (7 days). Share it with the person; opening it signs
    them up (or in) and adds them to this workspace."""
    auth = get_auth(request.app)
    inv, token = _guard(auth.store.invite, who, str(body.email), body.role)
    return {**inv, "url": f"{auth.base_url(request)}/invite/{token}"}


@router.delete("/orgs/current/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(invitation_id: str, request: Request,
                            who: Principal = Depends(require_role("admin"))):
    _guard(_store(request).revoke_invitation, who, invitation_id)


@router.post("/orgs/current/accounts", status_code=201)
async def create_account(body: AccountCreate, request: Request,
                         who: Principal = Depends(require_role("admin"))):
    """Generate a sign-in account for a team member: created in Keycloak with a
    temporary password (shown once; they must change it at first sign-in) and
    added to this workspace."""
    auth = get_auth(request.app)
    if not auth.enabled:
        raise HTTPException(409, "Sign-in is off on this platform (no identity provider).")
    if body.role == "owner" and not who.can("owner"):
        raise HTTPException(403, "only an owner can create another owner")
    _guard(auth.store.check_limit, who.org_id, "seats")
    email = str(body.email).lower()
    name = " ".join(p for p in (body.first_name.strip(), body.last_name.strip()) if p)
    try:
        sub, password = await auth.oidc.create_user(email, body.first_name.strip(),
                                                    body.last_name.strip())
    except AccountExists as e:
        raise HTTPException(409, str(e)) from e
    except OIDCError as e:
        raise HTTPException(502, str(e)) from e
    user = auth.store.precreate_user(sub=sub, email=email, name=name)
    _guard(auth.store.add_member, who.org_id, user["id"], body.role, who.label)
    if not user.get("default_org_id"):
        auth.store.db.update("users", user["id"], {"default_org_id": who.org_id})
    auth.store.db.audit(user=who.label, action="account.create", org_id=who.org_id,
                        detail={"email": email, "role": body.role})
    return {"user_id": user["id"], "email": email, "role": body.role,
            "temporary_password": password, "sign_in_url": f"{auth.base_url(request)}/app/"}


# --- API keys ------------------------------------------------------------------------

@router.get("/orgs/current/api-keys")
async def api_keys(request: Request, who: Principal = Depends(require_role("analyst"))):
    return _store(request).api_keys(who)


@router.post("/orgs/current/api-keys", status_code=201)
async def create_api_key(body: ApiKeyCreate, request: Request,
                         who: Principal = Depends(require_role("analyst"))):
    """The key is shown once. Use it as ``Authorization: Bearer osk_...`` on the
    REST API or the /mcp endpoint; it acts in this workspace with at most your role."""
    row, key = _guard(_store(request).create_api_key, who, body.name, body.role)
    return {**row, "key": key}


@router.delete("/orgs/current/api-keys/{key_id}", status_code=204)
async def revoke_api_key(key_id: str, request: Request,
                         who: Principal = Depends(require_role("analyst"))):
    _guard(_store(request).revoke_api_key, who, key_id)


# --- platform administration ---------------------------------------------------------

@router.get("/platform/orgs")
async def all_orgs(request: Request, _: Principal = Depends(require_platform_admin)):
    return _store(request).all_orgs()


@router.patch("/platform/orgs/{org_id}")
async def update_org(org_id: str, body: OrgAdminUpdate, request: Request,
                     who: Principal = Depends(require_platform_admin)):
    from ..models import now
    values: dict = {}
    if body.plan is not None:
        values["plan"] = body.plan
    if body.disabled is not None:
        values["disabled_at"] = now() if body.disabled else None
    if not values:
        return _store(request).org(org_id)
    return _guard(_store(request).update_org, org_id, values, who.label)
