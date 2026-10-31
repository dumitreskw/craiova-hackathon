"""Sign-in, sessions, workspaces (tenants), roles, invitations and API keys,
against a fake Keycloak: real RS256 tokens served over an httpx mock transport."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import Auth, AuthSettings
from app.auth.oidc import OIDCClient
from app.main import create_app
from app.models import new_id, now

ISSUER = "https://auth.test/realms/osint"
CLIENT = "osint-web"
BASE = "https://osint.test"


class FakeKeycloak:
    """Discovery, JWKS, token (code + refresh + client credentials), admin users."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        self.jwks = {"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]}
        self.codes: dict[str, dict] = {}       # code -> {user claims, nonce}
        self.revoked: set[str] = set()         # subs whose refresh tokens stopped working
        self.created: list[dict] = []

    def sign(self, claims: dict) -> str:
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})

    def tokens(self, user: dict, nonce: str | None = None) -> dict:
        t = int(time.time())
        base = {"iss": ISSUER, "sub": user["sub"], "iat": t, "exp": t + 300,
                "email": user["email"], "name": user.get("name", "")}
        access = {**base, "azp": CLIENT, "aud": "account",
                  "realm_access": {"roles": user.get("roles", [])}}
        idt = {**base, "aud": CLIENT, "nonce": nonce}
        return {"access_token": self.sign(access), "id_token": self.sign(idt),
                "refresh_token": f"rt:{user['sub']}", "expires_in": 300}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            p = f"{ISSUER}/protocol/openid-connect"
            return httpx.Response(200, json={
                "issuer": ISSUER, "authorization_endpoint": f"{p}/auth",
                "token_endpoint": f"{p}/token", "jwks_uri": f"{p}/certs",
                "end_session_endpoint": f"{p}/logout", "revocation_endpoint": f"{p}/revoke"})
        if path.endswith("/certs"):
            return httpx.Response(200, json=self.jwks)
        if path.endswith("/token"):
            form = parse_qs(request.content.decode())
            grant = form["grant_type"][0]
            if grant == "authorization_code":
                entry = self.codes.pop(form["code"][0], None)
                if not entry or not form.get("code_verifier"):
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self.tokens(entry["user"], entry["nonce"]))
            if grant == "refresh_token":
                sub = form["refresh_token"][0].removeprefix("rt:")
                if sub in self.revoked:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self.tokens({"sub": sub, "email": f"{sub}@x.test"}))
            if grant == "client_credentials":
                return httpx.Response(200, json={"access_token": "admin", "expires_in": 300})
        if path.endswith("/admin/realms/osint/users") and request.method == "POST":
            body = __import__("json").loads(request.content)
            if any(u["email"] == body["email"] for u in self.created):
                return httpx.Response(409)
            self.created.append(body)
            return httpx.Response(201, headers={
                "Location": f"https://auth.test/admin/realms/osint/users/kc-{len(self.created)}"})
        return httpx.Response(404)


@pytest.fixture
def kc():
    return FakeKeycloak()


@pytest.fixture
def auth_app(platform, kc):
    settings = AuthSettings(issuer=ISSUER, client_id=CLIENT, client_secret="s3cret",
                            public_url=BASE)
    app = create_app(platform, auth_settings=settings)
    app.state.platform = platform
    oidc = OIDCClient(settings, http=httpx.AsyncClient(transport=httpx.MockTransport(kc.handler)))
    app.state.auth = Auth(settings, platform.db, oidc)
    return app


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE,
                             headers={"Origin": BASE})


async def sign_in(app, kc, sub: str, email: str, *, roles=(), next_path="/app/",
                  client: httpx.AsyncClient | None = None) -> httpx.AsyncClient:
    """Run the browser flow: /auth/login -> (Keycloak) -> /auth/callback."""
    c = client or _client(app)
    r = await c.get("/auth/login", params={"next": next_path})
    assert r.status_code == 303
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == [f"{BASE}/auth/callback"]
    code = new_id("code")
    kc.codes[code] = {"user": {"sub": sub, "email": email, "name": sub.title(),
                               "roles": list(roles)}, "nonce": q["nonce"][0]}
    r = await c.get("/auth/callback", params={"code": code, "state": q["state"][0]})
    assert r.status_code == 303, r.text
    assert r.headers["location"] == next_path
    assert "osint_session" in c.cookies
    return c


NEW_INV = {"name": "Example SRL", "purpose": "Vendor due diligence before contract"}


async def test_api_needs_sign_in_but_health_and_shell_do_not(auth_app):
    async with _client(auth_app) as c:
        assert (await c.get("/api/v1/investigations")).status_code == 401
        assert (await c.get("/api/v1/me")).status_code == 401
        assert (await c.get("/mcp")).status_code == 401
        assert (await c.get("/api/v1/health")).status_code == 200
        # The worker API is outside user sign-in (its own token; off here).
        assert (await c.get("/api/v1/worker/jobs/next")).status_code == 503


async def test_sign_in_creates_account_workspace_and_session(auth_app, kc):
    c = await sign_in(auth_app, kc, "alice", "Alice@Example.com")
    me = (await c.get("/api/v1/me")).json()
    assert me["user"]["email"] == "alice@example.com"
    assert me["org"]["role"] == "owner" and me["org"]["plan"] == "free"
    assert me["org"]["name"] == "Alice's workspace"
    r = await c.post("/api/v1/investigations", json=NEW_INV)
    assert r.status_code == 201 and r.json()["created_by"] == "alice@example.com"


async def test_state_must_match_the_browser(auth_app, kc):
    async with _client(auth_app) as c:
        r = await c.get("/auth/login")
        state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    async with _client(auth_app) as other:  # no state cookie: another browser
        r = await other.get("/auth/callback", params={"code": "x", "state": state})
        assert r.status_code == 400 and "osint_session" not in other.cookies


async def test_open_redirects_are_refused(auth_app):
    async with _client(auth_app) as c:
        r = await c.get("/auth/login", params={"next": "//evil.example/x"})
        state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        row = auth_app.state.auth.store.db.get("auth_states", state)
        assert row["next"] == "/app/"


async def test_workspaces_are_isolated(auth_app, kc):
    alice = await sign_in(auth_app, kc, "alice", "alice@x.test")
    bob = await sign_in(auth_app, kc, "bob", "bob@x.test")
    inv = (await alice.post("/api/v1/investigations", json=NEW_INV)).json()
    assert (await bob.get("/api/v1/investigations")).json() == []
    assert (await bob.get(f"/api/v1/investigations/{inv['id']}")).status_code == 404
    assert (await bob.get(f"/api/v1/investigations/{inv['id']}/graph")).status_code == 404
    assert (await bob.delete(f"/api/v1/investigations/{inv['id']}")).status_code == 404
    audit = (await bob.get("/api/v1/audit")).json()
    assert not any(a["investigation_id"] == inv["id"] for a in audit)
    assert [i["id"] for i in (await alice.get("/api/v1/investigations")).json()] == [inv["id"]]


async def test_cross_site_writes_are_refused(auth_app, kc):
    c = await sign_in(auth_app, kc, "alice", "alice@x.test")
    r = await c.post("/api/v1/investigations", json=NEW_INV,
                     headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


async def test_invitation_roles_and_viewer_is_read_only(auth_app, kc):
    alice = await sign_in(auth_app, kc, "alice", "alice@x.test")
    inv = (await alice.post("/api/v1/investigations", json=NEW_INV)).json()
    r = await alice.post("/api/v1/orgs/current/invitations",
                         json={"email": "vic@x.test", "role": "viewer"})
    assert r.status_code == 201
    path = urlsplit(r.json()["url"]).path
    # Not signed in: the link sends the invitee to registration, then back.
    async with _client(auth_app) as anon:
        r2 = await anon.get(path)
        assert r2.status_code == 303 and r2.headers["location"].startswith("/auth/register")
    vic = await sign_in(auth_app, kc, "vic", "vic@x.test", next_path=path)
    r3 = await vic.get(path)
    assert r3.status_code == 303 and r3.headers["location"] == "/app/"
    me = (await vic.get("/api/v1/me")).json()
    assert me["org"]["id"] == (await alice.get("/api/v1/me")).json()["org"]["id"]
    assert me["org"]["role"] == "viewer" and len(me["orgs"]) == 1  # no personal workspace
    assert (await vic.get(f"/api/v1/investigations/{inv['id']}")).status_code == 200
    assert (await vic.post("/api/v1/investigations", json=NEW_INV)).status_code == 403
    # Single use.
    eve = await sign_in(auth_app, kc, "eve", "eve@x.test")
    assert (await eve.get(path)).status_code == 409
    # Promote, then the viewer can write.
    vic_id = me["user"]["id"]
    assert (await alice.patch(f"/api/v1/orgs/current/members/{vic_id}",
                              json={"role": "analyst"})).status_code == 200
    assert (await vic.post("/api/v1/investigations", json=NEW_INV)).status_code == 201
    # The last owner cannot leave.
    alice_id = (await alice.get("/api/v1/me")).json()["user"]["id"]
    assert (await alice.delete(f"/api/v1/orgs/current/members/{alice_id}")).status_code == 409


async def test_api_keys_scope_and_revocation(auth_app, kc):
    alice = await sign_in(auth_app, kc, "alice", "alice@x.test")
    inv = (await alice.post("/api/v1/investigations", json=NEW_INV)).json()
    r = await alice.post("/api/v1/orgs/current/api-keys", json={"name": "Claude Code",
                                                                "role": "viewer"})
    key = r.json()["key"]
    assert key.startswith("osk_") and r.json()["role"] == "viewer"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_app), base_url=BASE,
                                 headers={"Authorization": f"Bearer {key}"}) as api:
        assert [i["id"] for i in (await api.get("/api/v1/investigations")).json()] == [inv["id"]]
        assert (await api.post("/api/v1/investigations", json=NEW_INV)).status_code == 403
        await alice.delete(f"/api/v1/orgs/current/api-keys/{r.json()['id']}")
        assert (await api.get("/api/v1/investigations")).status_code == 401


async def test_bearer_access_token(auth_app, kc):
    await sign_in(auth_app, kc, "alice", "alice@x.test")
    token = kc.tokens({"sub": "alice", "email": "alice@x.test"})["access_token"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_app), base_url=BASE,
                                 headers={"Authorization": f"Bearer {token}"}) as api:
        assert (await api.get("/api/v1/me")).json()["user"]["email"] == "alice@x.test"
    forged = jwt.encode({"iss": ISSUER, "sub": "alice", "azp": CLIENT, "iat": 0,
                         "exp": 2**31}, "not-the-key", algorithm="HS256")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_app), base_url=BASE,
                                 headers={"Authorization": f"Bearer {forged}"}) as api:
        assert (await api.get("/api/v1/me")).status_code == 401


async def test_session_ends_when_keycloak_revokes_it(auth_app, kc, platform):
    alice = await sign_in(auth_app, kc, "alice", "alice@x.test")
    assert (await alice.get("/api/v1/me")).status_code == 200
    kc.revoked.add("alice")
    platform.db.conn.execute("UPDATE sessions SET checked_at = '2000-01-01T00:00:00+00:00'")
    platform.db.conn.commit()
    assert (await alice.get("/api/v1/me")).status_code == 401


async def test_logout_ends_session_and_signs_out_of_keycloak(auth_app, kc):
    alice = await sign_in(auth_app, kc, "alice", "alice@x.test")
    r = await alice.post("/auth/logout")
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"{ISSUER}/protocol/openid-connect/logout")
    assert "id_token_hint" in r.headers["location"]
    assert (await alice.get("/api/v1/me")).status_code == 401


async def test_admin_generates_an_account(auth_app, kc):
    alice = await sign_in(auth_app, kc, "alice", "alice@x.test")
    r = await alice.post("/api/v1/orgs/current/accounts",
                         json={"email": "Dan@X.test", "first_name": "Dan", "role": "analyst"})
    assert r.status_code == 201, r.text
    assert len(r.json()["temporary_password"]) == 16
    assert kc.created[0]["requiredActions"] == ["UPDATE_PASSWORD"]
    assert kc.created[0]["credentials"][0]["temporary"] is True
    # Dan's first sign-in (Keycloak's sub = the id it created) lands in Alice's workspace.
    dan = await sign_in(auth_app, kc, "kc-1", "dan@x.test")
    me = (await dan.get("/api/v1/me")).json()
    assert me["org"]["name"] == "Alice's workspace" and me["org"]["role"] == "analyst"
    # Same email again: Keycloak says it exists.
    r = await alice.post("/api/v1/orgs/current/accounts",
                         json={"email": "dan@x.test", "first_name": "Dan", "role": "analyst"})
    assert r.status_code == 409


async def test_plan_limits(auth_app, kc):
    alice = await sign_in(auth_app, kc, "alice", "alice@x.test")
    for _ in range(5):  # free plan: 5 investigations
        assert (await alice.post("/api/v1/investigations", json=NEW_INV)).status_code == 201
    r = await alice.post("/api/v1/investigations", json=NEW_INV)
    assert r.status_code == 402 and "Free plan" in r.json()["detail"]


async def test_platform_admin_claims_legacy_data_and_manages_plans(auth_app, kc, platform):
    platform.db.insert("investigations", {
        "id": "inv_legacy", "name": "Old", "purpose": "From before accounts existed",
        "mode": "passive", "settings": {}, "retention_days": 90, "legal_hold": 0,
        "created_by": "analyst", "created_at": now()})
    bob = await sign_in(auth_app, kc, "bob", "bob@x.test")       # not an admin: no claim
    assert (await bob.get("/api/v1/investigations")).json() == []
    assert (await bob.get("/api/v1/platform/orgs")).status_code == 403
    op = await sign_in(auth_app, kc, "op", "op@x.test", roles=["platform-admin"])
    assert [i["id"] for i in (await op.get("/api/v1/investigations")).json()] == ["inv_legacy"]
    me = (await op.get("/api/v1/me")).json()
    assert me["org"]["plan"] == "enterprise" and me["user"]["platform_admin"]
    bob_org = (await bob.get("/api/v1/me")).json()["org"]["id"]
    r = await op.patch(f"/api/v1/platform/orgs/{bob_org}", json={"plan": "team"})
    assert r.status_code == 200 and r.json()["plan"] == "team"
    await op.patch(f"/api/v1/platform/orgs/{bob_org}", json={"disabled": True})
    assert (await bob.get("/api/v1/me")).json()["org"] is None  # suspended
