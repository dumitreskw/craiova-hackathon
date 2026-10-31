"""Accounts and tenancy in the platform database: users (mirrors of Keycloak
identities), workspaces, memberships, invitations, browser sessions and API keys.

Secrets (session cookies, invitation tokens, API keys) are random 256-bit
values; only their sha256 is stored.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from ..models import Database, new_id, now
from . import plans
from .context import ROLES, Principal, lower_role

LOCAL_ORG = "org_local"
LOCAL_USER = "usr_local"
API_KEY_PREFIX = "osk_"
INVITE_DAYS = 7
AUTH_STATE_MINUTES = 15


class TenancyError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _later(**delta) -> str:
    return _iso(datetime.now(timezone.utc) + timedelta(**delta))


def _expired(ts: str | None) -> bool:
    return bool(ts) and datetime.fromisoformat(ts) <= datetime.now(timezone.utc)


class TenancyStore:
    def __init__(self, db: Database):
        self.db = db

    # --- users -----------------------------------------------------------------

    def user(self, user_id: str) -> dict | None:
        return self.db.get("users", user_id)

    def upsert_user(self, *, sub: str, email: str, name: str,
                    platform_admin: bool) -> dict:
        """Create or refresh the mirror of a Keycloak identity at sign-in."""
        row = self.db.one("SELECT * FROM users WHERE sub = ?", (sub,))
        values = {"email": email.lower(), "name": name or email.split("@")[0],
                  "platform_admin": int(platform_admin), "last_login_at": now()}
        if row:
            self.db.update("users", row["id"], values)
            return self.db.get("users", row["id"])
        uid = new_id("usr")
        self.db.insert("users", {"id": uid, "sub": sub, "created_at": now(), **values})
        self.db.audit(user=values["email"], action="user.create", detail={"sub": sub})
        return self.db.get("users", uid)

    def local_principal(self) -> Principal:
        """Auth off: one local owner of one local workspace."""
        if not self.db.get("users", LOCAL_USER):
            self.db.insert("users", {"id": LOCAL_USER, "sub": "local", "email": "",
                                     "name": "analyst", "platform_admin": 1,
                                     "default_org_id": LOCAL_ORG, "created_at": now()})
        if not self.db.get("orgs", LOCAL_ORG):
            self.db.insert("orgs", {"id": LOCAL_ORG, "name": "Local workspace",
                                    "plan": "enterprise", "created_by": LOCAL_USER,
                                    "created_at": now()})
            self.db.insert("memberships", {"org_id": LOCAL_ORG, "user_id": LOCAL_USER,
                                           "role": "owner", "created_at": now()})
            self._claim_legacy(LOCAL_ORG)
        return Principal(user_id=LOCAL_USER, email="", name="analyst", org_id=LOCAL_ORG,
                         role="owner", platform_admin=True, via="local")

    # --- workspaces --------------------------------------------------------------

    def org(self, org_id: str) -> dict | None:
        return self.db.get("orgs", org_id)

    def role(self, org_id: str | None, user_id: str) -> str | None:
        if not org_id:
            return None
        row = self.db.one("SELECT m.role FROM memberships m JOIN orgs o ON o.id = m.org_id "
                          "WHERE m.org_id = ? AND m.user_id = ? AND o.disabled_at IS NULL",
                          (org_id, user_id))
        return row["role"] if row else None

    def orgs_of(self, user_id: str) -> list[dict]:
        return self.db.query(
            "SELECT o.id, o.name, o.plan, o.disabled_at, m.role FROM memberships m "
            "JOIN orgs o ON o.id = m.org_id WHERE m.user_id = ? ORDER BY o.created_at",
            (user_id,))

    def pick_org(self, user: dict, preferred: str | None = None) -> tuple[str | None, str | None]:
        """The workspace to act in: ``preferred`` if a member, else the default, else any."""
        for org_id in (preferred, user.get("default_org_id")):
            role = self.role(org_id, user["id"])
            if role:
                return org_id, role
        for o in self.orgs_of(user["id"]):
            if not o["disabled_at"]:
                return o["id"], o["role"]
        return None, None

    def create_org(self, user: dict, name: str, plan: str | None = None) -> dict:
        owned = self.db.one("SELECT count(*) AS n FROM memberships WHERE user_id = ? "
                            "AND role = 'owner'", (user["id"],))["n"]
        if owned >= plans.MAX_OWNED_ORGS and not user["platform_admin"]:
            raise TenancyError(f"You already own {owned} workspaces.", 409)
        org_id = new_id("org")
        self.db.insert("orgs", {"id": org_id, "name": name.strip()[:80] or "Workspace",
                                "plan": plan if plan in plans.PLANS else "free",
                                "created_by": user["id"], "created_at": now()})
        self.db.insert("memberships", {"org_id": org_id, "user_id": user["id"],
                                       "role": "owner", "created_at": now()})
        if not user.get("default_org_id"):
            self.db.update("users", user["id"], {"default_org_id": org_id})
        self.db.audit(user=user["email"], action="org.create", org_id=org_id,
                      detail={"name": name, "plan": plan})
        if user["platform_admin"]:
            self._claim_legacy(org_id)
        return self.db.get("orgs", org_id)

    def ensure_workspace(self, user: dict, default_plan: str) -> None:
        """First sign-in: every account starts with a workspace of its own."""
        if self.orgs_of(user["id"]):
            if user["platform_admin"]:
                org_id, _ = self.pick_org(user)
                if org_id:
                    self._claim_legacy(org_id)
            return
        first = (user["name"] or user["email"].split("@")[0]).split()[0]
        self.create_org(user, f"{first}'s workspace",
                        "enterprise" if user["platform_admin"] else default_plan)

    def _claim_legacy(self, org_id: str) -> None:
        """Investigations from before accounts existed go to the first platform
        admin's workspace (once; afterwards none are left without one)."""
        n = self.db.conn.execute("UPDATE investigations SET org_id = ? WHERE org_id IS NULL",
                                 (org_id,)).rowcount
        if n:
            # The audit log is append-only: old rows keep org NULL, and the
            # audit view also matches them through their investigation.
            self.db.update("orgs", org_id, {"plan": "enterprise"})
            self.db.audit(user="system", action="org.claim_legacy", org_id=org_id,
                          detail={"investigations": n})

    def usage(self, org_id: str) -> dict:
        one = lambda sql: self.db.one(sql, (org_id,))["n"]
        return {
            "seats": one("SELECT count(*) AS n FROM memberships WHERE org_id = ?"),
            "investigations": one("SELECT count(*) AS n FROM investigations "
                                  "WHERE org_id = ? AND deleted_at IS NULL"),
            "api_keys": one("SELECT count(*) AS n FROM api_keys "
                            "WHERE org_id = ? AND revoked_at IS NULL"),
            "pending_invitations": one("SELECT count(*) AS n FROM invitations WHERE org_id = ? "
                                       "AND accepted_at IS NULL AND revoked_at IS NULL"),
        }

    def check_limit(self, org_id: str, what: str) -> None:
        org = self.org(org_id)
        try:
            plans.check(org["plan"], what, self.usage(org_id)[what])
        except plans.LimitReached as e:
            raise TenancyError(str(e), 402) from e

    # --- members -----------------------------------------------------------------

    def members(self, org_id: str) -> list[dict]:
        return self.db.query(
            "SELECT u.id, u.email, u.name, u.last_login_at, m.role, m.created_at AS joined_at "
            "FROM memberships m JOIN users u ON u.id = m.user_id WHERE m.org_id = ? "
            "ORDER BY m.created_at", (org_id,))

    def add_member(self, org_id: str, user_id: str, role: str, by: str) -> None:
        if self.role(org_id, user_id):
            return
        self.check_limit(org_id, "seats")
        self.db.insert("memberships", {"org_id": org_id, "user_id": user_id, "role": role,
                                       "created_at": now()})
        self.db.audit(user=by, action="member.add", org_id=org_id,
                      detail={"user_id": user_id, "role": role})

    def _owners(self, org_id: str) -> int:
        return self.db.one("SELECT count(*) AS n FROM memberships WHERE org_id = ? "
                           "AND role = 'owner'", (org_id,))["n"]

    def set_role(self, actor: Principal, user_id: str, role: str) -> None:
        if role not in ROLES:
            raise TenancyError(f"role must be one of {', '.join(ROLES)}", 422)
        current = self.role(actor.org_id, user_id)
        if current is None:
            raise TenancyError("not a member of this workspace", 404)
        if "owner" in (role, current) and not actor.can("owner"):
            raise TenancyError("only an owner can grant or remove the owner role", 403)
        if current == "owner" and role != "owner" and self._owners(actor.org_id) <= 1:
            raise TenancyError("a workspace needs at least one owner", 409)
        self.db.conn.execute("UPDATE memberships SET role = ? WHERE org_id = ? AND user_id = ?",
                             (role, actor.org_id, user_id))
        self.db.conn.commit()
        self.db.audit(user=actor.label, action="member.role", org_id=actor.org_id,
                      detail={"user_id": user_id, "from": current, "to": role})

    def remove_member(self, actor: Principal, user_id: str) -> None:
        current = self.role(actor.org_id, user_id)
        if current is None:
            raise TenancyError("not a member of this workspace", 404)
        if user_id != actor.user_id and not actor.can("admin"):
            raise TenancyError("only an admin can remove members", 403)
        if current == "owner" and user_id != actor.user_id and not actor.can("owner"):
            raise TenancyError("only an owner can remove an owner", 403)
        if current == "owner" and self._owners(actor.org_id) <= 1:
            raise TenancyError("a workspace needs at least one owner", 409)
        c = self.db.conn
        c.execute("DELETE FROM memberships WHERE org_id = ? AND user_id = ?",
                  (actor.org_id, user_id))
        c.execute("UPDATE api_keys SET revoked_at = ? WHERE org_id = ? AND user_id = ? "
                  "AND revoked_at IS NULL", (now(), actor.org_id, user_id))
        c.execute("UPDATE sessions SET org_id = NULL WHERE org_id = ? AND user_id = ?",
                  (actor.org_id, user_id))
        c.commit()
        self.db.audit(user=actor.label, action="member.remove", org_id=actor.org_id,
                      detail={"user_id": user_id})

    # --- invitations -------------------------------------------------------------

    def invite(self, actor: Principal, email: str, role: str) -> tuple[dict, str]:
        if role not in ROLES or (role == "owner" and not actor.can("owner")):
            raise TenancyError("you cannot invite with that role", 403)
        usage = self.usage(actor.org_id)
        cap = plans.limits(self.org(actor.org_id)["plan"])["seats"]
        if cap is not None and usage["seats"] + usage["pending_invitations"] >= cap:
            raise TenancyError(f"Your plan allows {cap} seats, counting pending invitations.", 402)
        token = "inv_" + secrets.token_urlsafe(32)
        inv = {"id": new_id("invite"), "org_id": actor.org_id, "email": email.lower(),
               "role": role, "token_hash": sha(token), "created_by": actor.user_id,
               "created_at": now(), "expires_at": _later(days=INVITE_DAYS)}
        self.db.insert("invitations", inv)
        self.db.audit(user=actor.label, action="invitation.create", org_id=actor.org_id,
                      detail={"email": inv["email"], "role": role})
        return self.invitation_view(inv), token

    @staticmethod
    def invitation_view(inv: dict) -> dict:
        return {k: inv.get(k) for k in ("id", "email", "role", "created_at", "expires_at",
                                        "accepted_at", "revoked_at")}

    def invitations(self, org_id: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM invitations WHERE org_id = ? AND accepted_at IS NULL "
                             "AND revoked_at IS NULL ORDER BY created_at DESC", (org_id,))
        return [self.invitation_view(r) for r in rows if not _expired(r["expires_at"])]

    def revoke_invitation(self, actor: Principal, invitation_id: str) -> None:
        inv = self.db.get("invitations", invitation_id)
        if not inv or inv["org_id"] != actor.org_id:
            raise TenancyError("invitation not found", 404)
        self.db.update("invitations", invitation_id, {"revoked_at": now()})
        self.db.audit(user=actor.label, action="invitation.revoke", org_id=actor.org_id,
                      detail={"email": inv["email"]})

    def accept_invitation(self, token: str, user: dict) -> str:
        inv = self.db.one("SELECT * FROM invitations WHERE token_hash = ?", (sha(token),))
        if not inv or inv["revoked_at"]:
            raise TenancyError("This invitation is not valid (revoked or mistyped).", 404)
        if inv["accepted_at"]:
            if inv["accepted_by"] == user["id"]:
                return inv["org_id"]
            raise TenancyError("This invitation was already used.", 409)
        if _expired(inv["expires_at"]):
            raise TenancyError("This invitation has expired; ask for a new one.", 410)
        if not self.role(inv["org_id"], user["id"]):
            self.db.insert("memberships", {"org_id": inv["org_id"], "user_id": user["id"],
                                           "role": inv["role"], "created_at": now()})
        self.db.update("invitations", inv["id"], {"accepted_by": user["id"],
                                                  "accepted_at": now()})
        if not user.get("default_org_id"):
            self.db.update("users", user["id"], {"default_org_id": inv["org_id"]})
        self.db.audit(user=user["email"], action="invitation.accept", org_id=inv["org_id"],
                      detail={"invited_as": inv["email"], "role": inv["role"]})
        return inv["org_id"]

    def precreate_user(self, *, sub: str, email: str, name: str) -> dict:
        """A Keycloak account created by a workspace admin, before its first sign-in."""
        return self.upsert_user(sub=sub, email=email, name=name, platform_admin=False)

    # --- sign-in flow state ---------------------------------------------------------

    def new_auth_state(self, verifier: str, nonce: str, next_path: str) -> str:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(minutes=AUTH_STATE_MINUTES))
        self.db.conn.execute("DELETE FROM auth_states WHERE created_at < ?", (cutoff,))
        state = secrets.token_urlsafe(32)
        self.db.insert("auth_states", {"id": state, "verifier": verifier, "nonce": nonce,
                                       "next": next_path, "created_at": now()})
        return state

    def pop_auth_state(self, state: str) -> dict | None:
        row = self.db.get("auth_states", state)
        if row:
            self.db.conn.execute("DELETE FROM auth_states WHERE id = ?", (state,))
            self.db.conn.commit()
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])
            if age > timedelta(minutes=AUTH_STATE_MINUTES):
                return None
        return row

    # --- sessions ------------------------------------------------------------------

    def create_session(self, user: dict, org_id: str | None, tokens: dict, days: int) -> str:
        value = secrets.token_urlsafe(32)
        self.db.insert("sessions", {"id": sha(value), "user_id": user["id"], "org_id": org_id,
                                    "id_token": tokens.get("id_token"),
                                    "refresh_token": tokens.get("refresh_token"),
                                    "created_at": now(), "last_seen_at": now(),
                                    "checked_at": now(), "expires_at": _later(days=days)})
        return value

    def session(self, value: str) -> dict | None:
        row = self.db.get("sessions", sha(value))
        if row and _expired(row["expires_at"]):
            self.drop_session(row["id"])
            return None
        return row

    def touch_session(self, session_id: str, **values) -> None:
        self.db.update("sessions", session_id, {"last_seen_at": now(), **values})

    def drop_session(self, session_id: str) -> dict | None:
        row = self.db.get("sessions", session_id)
        self.db.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.db.conn.commit()
        return row

    def switch_org(self, principal: Principal, org_id: str) -> str:
        role = self.role(org_id, principal.user_id)
        if not role:
            raise TenancyError("not a member of that workspace", 404)
        if principal.session_id:
            self.db.update("sessions", principal.session_id, {"org_id": org_id})
        self.db.update("users", principal.user_id, {"default_org_id": org_id})
        return role

    # --- API keys -------------------------------------------------------------------

    def create_api_key(self, actor: Principal, name: str, role: str | None) -> tuple[dict, str]:
        self.check_limit(actor.org_id, "api_keys")
        role = lower_role(role or "analyst", actor.role)
        key = API_KEY_PREFIX + secrets.token_urlsafe(32)
        row = {"id": new_id("key"), "org_id": actor.org_id, "user_id": actor.user_id,
               "name": name.strip()[:60] or "API key", "prefix": key[:12],
               "key_hash": sha(key), "role": role, "created_at": now()}
        self.db.insert("api_keys", row)
        self.db.audit(user=actor.label, action="api_key.create", org_id=actor.org_id,
                      detail={"name": row["name"], "role": role, "prefix": row["prefix"]})
        return self.api_key_view(row), key

    @staticmethod
    def api_key_view(row: dict) -> dict:
        return {k: row.get(k) for k in ("id", "name", "prefix", "role", "user_id",
                                        "created_at", "last_used_at", "revoked_at")}

    def api_keys(self, actor: Principal) -> list[dict]:
        sql = ("SELECT k.*, u.email AS owner_email FROM api_keys k JOIN users u ON u.id = k.user_id "
               "WHERE k.org_id = ? AND k.revoked_at IS NULL")
        params: list = [actor.org_id]
        if not actor.can("admin"):
            sql += " AND k.user_id = ?"
            params.append(actor.user_id)
        return [{**self.api_key_view(r), "owner_email": r["owner_email"]}
                for r in self.db.query(sql + " ORDER BY k.created_at DESC", params)]

    def revoke_api_key(self, actor: Principal, key_id: str) -> None:
        row = self.db.get("api_keys", key_id)
        if not row or row["org_id"] != actor.org_id or row["revoked_at"]:
            raise TenancyError("API key not found", 404)
        if row["user_id"] != actor.user_id and not actor.can("admin"):
            raise TenancyError("only its owner or an admin can revoke this key", 403)
        self.db.update("api_keys", key_id, {"revoked_at": now()})
        self.db.audit(user=actor.label, action="api_key.revoke", org_id=actor.org_id,
                      detail={"name": row["name"], "prefix": row["prefix"]})

    def principal_for_api_key(self, key: str) -> Principal | None:
        row = self.db.one("SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
                          (sha(key),))
        if not row:
            return None
        user = self.user(row["user_id"])
        member_role = self.role(row["org_id"], row["user_id"])
        if not user or not member_role:
            return None  # the owner left the workspace (or it is suspended)
        if not row["last_used_at"] or row["last_used_at"][:16] != now()[:16]:
            self.db.update("api_keys", row["id"], {"last_used_at": now()})
        return Principal(user_id=user["id"], email=user["email"], name=user["name"],
                         org_id=row["org_id"], role=lower_role(row["role"], member_role),
                         platform_admin=False, via="api_key")

    # --- platform admin ------------------------------------------------------------

    def all_orgs(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM orgs ORDER BY created_at")
        return [{**r, "usage": self.usage(r["id"])} for r in rows]

    def update_org(self, org_id: str, values: dict, by: str) -> dict:
        if not self.org(org_id):
            raise TenancyError("workspace not found", 404)
        if "plan" in values and values["plan"] not in plans.PLANS:
            raise TenancyError(f"plan must be one of {', '.join(plans.PLANS)}", 422)
        self.db.update("orgs", org_id, values)
        self.db.audit(user=by, action="org.update", org_id=org_id, detail=values)
        return self.org(org_id)
