"""OpenID Connect against Keycloak: the authorization-code flow (with PKCE),
token verification, refresh, logout, and the bits of the admin API used to
create accounts for a workspace.

The app is a confidential client ("backend for frontend"): Keycloak's tokens
stay on the server, and the browser only holds an HttpOnly session cookie.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import string
import time
from urllib.parse import urlencode

import httpx
import jwt

from .settings import AuthSettings

_ALGS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"]


class OIDCError(Exception):
    pass


class AccountExists(OIDCError):
    pass


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def temporary_password() -> str:
    """16 characters with every class, so any sane password policy accepts it."""
    alphabet = string.ascii_letters + string.digits + "-_.!@#"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "-_.!@#" for c in pw)):
            return pw


class OIDCClient:
    def __init__(self, settings: AuthSettings, http: httpx.AsyncClient | None = None):
        self.s = settings
        self.http = http or httpx.AsyncClient(timeout=10.0)
        self._disc: dict | None = None
        self._disc_at = 0.0
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_at = 0.0
        self._admin: tuple[str, float] | None = None

    async def aclose(self) -> None:
        await self.http.aclose()

    # --- metadata ------------------------------------------------------------

    async def discovery(self) -> dict:
        if self._disc is None or time.monotonic() - self._disc_at > 3600:
            r = await self.http.get(f"{self.s.issuer}/.well-known/openid-configuration")
            r.raise_for_status()
            self._disc, self._disc_at = r.json(), time.monotonic()
        return self._disc

    async def _key(self, kid: str | None) -> jwt.PyJWK:
        if kid not in self._keys and time.monotonic() - self._keys_at > 30:
            # Unknown key id: Keycloak rotated its keys (rate-limited refetch).
            r = await self.http.get((await self.discovery())["jwks_uri"])
            r.raise_for_status()
            self._keys = {}
            for k in r.json().get("keys", []):
                if k.get("use", "sig") == "sig" and k.get("kid"):
                    try:
                        self._keys[k["kid"]] = jwt.PyJWK(k)
                    except jwt.PyJWKError:
                        continue  # an algorithm we don't verify with
            self._keys_at = time.monotonic()
        if kid not in self._keys:
            raise OIDCError("token signed with an unknown key")
        return self._keys[kid]

    async def verify(self, token: str, *, kind: str = "access",
                     nonce: str | None = None) -> dict:
        """Verify signature, issuer, expiry and that the token is for this client."""
        try:
            header = jwt.get_unverified_header(token)
            key = await self._key(header.get("kid"))
            claims = jwt.decode(token, key.key, algorithms=_ALGS, issuer=self.s.issuer,
                                options={"verify_aud": False, "require": ["exp", "iat", "sub"]},
                                leeway=30)
        except (jwt.PyJWTError, httpx.HTTPError) as e:
            raise OIDCError(f"invalid token: {e}") from e
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud] if aud else []
        if kind == "id":
            if self.s.client_id not in auds:
                raise OIDCError("ID token is for another client")
            if nonce is not None and not secrets.compare_digest(claims.get("nonce", ""), nonce):
                raise OIDCError("ID token nonce mismatch")
        elif claims.get("azp") != self.s.client_id and self.s.client_id not in auds:
            raise OIDCError("access token is for another client")
        return claims

    # --- authorization-code flow -----------------------------------------------

    async def authorize_url(self, *, redirect_uri: str, state: str, nonce: str,
                            challenge: str, register: bool = False,
                            login_hint: str | None = None) -> str:
        params = {"client_id": self.s.client_id, "response_type": "code",
                  "scope": "openid email profile", "redirect_uri": redirect_uri,
                  "state": state, "nonce": nonce, "code_challenge": challenge,
                  "code_challenge_method": "S256"}
        if register:
            params["prompt"] = "create"  # OIDC "initiating user registration"
        if login_hint:
            params["login_hint"] = login_hint
        return f"{(await self.discovery())['authorization_endpoint']}?{urlencode(params)}"

    async def _token(self, data: dict) -> httpx.Response:
        data = {**data, "client_id": self.s.client_id}
        if self.s.client_secret:
            data["client_secret"] = self.s.client_secret
        return await self.http.post((await self.discovery())["token_endpoint"], data=data)

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        r = await self._token({"grant_type": "authorization_code", "code": code,
                               "redirect_uri": redirect_uri, "code_verifier": verifier})
        if r.status_code != 200:
            raise OIDCError(f"code exchange failed: {r.status_code} {r.text[:200]}")
        return r.json()

    async def refresh(self, refresh_token: str) -> dict | None:
        """New tokens, or None when Keycloak says the session is over.

        Network trouble raises ``httpx.HTTPError`` so the caller can tell
        "revoked" from "Keycloak unreachable".
        """
        r = await self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})
        if r.status_code == 200:
            return r.json()
        if r.status_code in (400, 401):
            return None
        r.raise_for_status()
        return None

    async def logout_url(self, id_token: str | None, post_logout_redirect_uri: str) -> str:
        params = {"client_id": self.s.client_id,
                  "post_logout_redirect_uri": post_logout_redirect_uri}
        if id_token:
            params["id_token_hint"] = id_token
        return f"{(await self.discovery())['end_session_endpoint']}?{urlencode(params)}"

    async def revoke(self, refresh_token: str) -> None:
        """End the Keycloak session behind a refresh token (best effort)."""
        endpoint = (await self.discovery()).get("revocation_endpoint")
        if not endpoint:
            return
        data = {"client_id": self.s.client_id, "token": refresh_token,
                "token_type_hint": "refresh_token"}
        if self.s.client_secret:
            data["client_secret"] = self.s.client_secret
        try:
            await self.http.post(endpoint, data=data)
        except httpx.HTTPError:
            pass

    # --- admin API (the client's service account) --------------------------------

    async def _admin_headers(self) -> dict:
        if self._admin is None or self._admin[1] - time.monotonic() < 30:
            r = await self._token({"grant_type": "client_credentials"})
            if r.status_code != 200:
                raise OIDCError("Keycloak refused the service account; give the client "
                                "realm-management manage-users (scripts/keycloak_bootstrap.py)")
            body = r.json()
            self._admin = (body["access_token"], time.monotonic() + body.get("expires_in", 60))
        return {"Authorization": f"Bearer {self._admin[0]}"}

    async def create_user(self, email: str, first_name: str, last_name: str) -> tuple[str, str]:
        """Create a Keycloak account with a temporary password.

        Returns ``(keycloak user id, temporary password)``; the user must set a
        new password at first sign-in. Raises :class:`AccountExists`.
        """
        password = temporary_password()
        r = await self.http.post(f"{self.s.admin_base}/users", headers=await self._admin_headers(),
                                 json={"username": email, "email": email,
                                       "firstName": first_name, "lastName": last_name,
                                       "enabled": True, "emailVerified": False,
                                       "requiredActions": ["UPDATE_PASSWORD"],
                                       "credentials": [{"type": "password", "value": password,
                                                        "temporary": True}]})
        if r.status_code == 409:
            raise AccountExists(f"{email} already has an account; invite them instead")
        if r.status_code != 201:
            raise OIDCError(f"Keycloak did not create the account: {r.status_code} {r.text[:200]}")
        return r.headers["Location"].rstrip("/").rsplit("/", 1)[-1], password
