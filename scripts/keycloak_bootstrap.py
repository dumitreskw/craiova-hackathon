#!/usr/bin/env python3
"""Provision Keycloak for the OSINT platform (idempotent; safe to re-run).

Creates or updates:
  * realm ``osint``: self-service sign-up with email as username, brute-force
    protection, a password policy;
  * realm role ``platform-admin`` (operators: every workspace, plans, personas);
  * confidential client ``osint-web`` (authorization code + PKCE) whose service
    account may create users (workspace admins "generate" accounts);
  * optionally an operator account with ``platform-admin`` and a temporary password.

Prints the client secret (set it as OSINT_OIDC_CLIENT_SECRET on the app).

    KC_ADMIN_USER=... KC_ADMIN_PASSWORD=... python scripts/keycloak_bootstrap.py \\
        --keycloak https://auth.dumisoft.ro --app https://osint.dumisoft.ro \\
        [--app http://localhost:8000 --app http://localhost:5173] \\
        [--operator-email you@example.com]

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
import urllib.error
import urllib.parse
import urllib.request

REALM = "osint"
CLIENT_ID = "osint-web"
ADMIN_ROLE = "platform-admin"
SA_ROLES = ("manage-users", "view-users", "query-users")


class KC:
    def __init__(self, base: str, user: str, password: str):
        self.base = base.rstrip("/")
        body = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                       "username": user, "password": password}).encode()
        status, text, _ = self._raw(
            "POST", f"{self.base}/realms/master/protocol/openid-connect/token", body,
            {"Content-Type": "application/x-www-form-urlencoded"})
        if status != 200:
            hint = (" (wrong KC_ADMIN_USER / KC_ADMIN_PASSWORD?)" if status in (400, 401)
                    else " (blocked before Keycloak: a proxy/WAF answered)")
            sys.exit(f"master-realm admin sign-in failed: HTTP {status}{hint}: {text[:300]}")
        self.token = json.loads(text)["access_token"]

    def _raw(self, method, url, body=None, headers=None):
        # Cloudflare in front of Keycloak rejects urllib's default User-Agent.
        headers = {"User-Agent": "osint-keycloak-bootstrap/1.0 (+python)",
                   "Accept": "application/json", **(headers or {})}
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode() or "null", {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode() or "null", {k.lower(): v for k, v in e.headers.items()}

    def call(self, method, path, data=None, ok=(200, 201, 204)):
        body = json.dumps(data).encode() if data is not None else None
        status, text, headers = self._raw(
            method, f"{self.base}/admin/realms{path}", body,
            {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        if status not in ok:
            sys.exit(f"{method} {path} -> {status}: {text[:300]}")
        return (json.loads(text) if text.strip() not in ("", "null") else None), headers, status


def realm_settings(display: str) -> dict:
    return {
        "realm": REALM, "enabled": True, "displayName": display,
        "displayNameHtml": f"<strong>{display}</strong>",
        "registrationAllowed": True, "registrationEmailAsUsername": True,
        "loginWithEmailAllowed": True, "duplicateEmailsAllowed": False,
        "editUsernameAllowed": False, "rememberMe": True,
        # No SMTP yet: turn these on once the realm has an email server.
        "verifyEmail": False, "resetPasswordAllowed": False,
        "bruteForceProtected": True, "failureFactor": 8, "permanentLockout": False,
        "maxFailureWaitSeconds": 900, "waitIncrementSeconds": 60,
        "passwordPolicy": "length(10) and notUsername and notEmail and maxLength(128)",
        "sslRequired": "external",
        "accessTokenLifespan": 300, "ssoSessionIdleTimeout": 7 * 86400,
        "ssoSessionMaxLifespan": 30 * 86400, "revokeRefreshToken": False,
        "loginTheme": "keycloak.v2", "accountTheme": "keycloak.v3",
        "internationalizationEnabled": False,
    }


def client_settings(apps: list[str]) -> dict:
    return {
        "clientId": CLIENT_ID, "name": "OSINT platform", "enabled": True,
        "protocol": "openid-connect", "publicClient": False,
        "clientAuthenticatorType": "client-secret",
        "standardFlowEnabled": True, "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False, "serviceAccountsEnabled": True,
        "frontchannelLogout": False,
        "redirectUris": [f"{a}/auth/callback" for a in apps],
        "webOrigins": apps, "rootUrl": apps[0], "baseUrl": "/app/",
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": "##".join(f"{a}/" for a in apps),
            "backchannel.logout.session.required": "true",
        },
    }


def temp_password() -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!@#"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if all(any(c in s for c in pw) for s in (string.ascii_lowercase, string.ascii_uppercase,
                                                 string.digits, "-_.!@#")):
            return pw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keycloak", required=True, help="e.g. https://auth.dumisoft.ro")
    ap.add_argument("--app", action="append", required=True,
                    help="platform base URL (repeat for dev URLs); the first is canonical")
    ap.add_argument("--display-name", default="Cybnetic OSINT")
    ap.add_argument("--operator-email", help="create/flag this account as platform-admin")
    ap.add_argument("--secret-out", help="write the client secret to this file instead of stdout")
    a = ap.parse_args()
    user, password = os.environ.get("KC_ADMIN_USER"), os.environ.get("KC_ADMIN_PASSWORD")
    if not user or not password:
        sys.exit("set KC_ADMIN_USER and KC_ADMIN_PASSWORD (the master-realm admin)")
    apps = [u.rstrip("/") for u in a.app]
    kc = KC(a.keycloak, user, password)

    # Realm.
    _, _, status = kc.call("GET", f"/{REALM}", ok=(200, 404))
    if status == 404:
        kc.call("POST", "", realm_settings(a.display_name))
        print(f"realm {REALM}: created")
    else:
        kc.call("PUT", f"/{REALM}", realm_settings(a.display_name))
        print(f"realm {REALM}: updated")

    # Platform-admin role.
    _, _, status = kc.call("GET", f"/{REALM}/roles/{ADMIN_ROLE}", ok=(200, 404))
    if status == 404:
        kc.call("POST", f"/{REALM}/roles", {
            "name": ADMIN_ROLE, "description": "Operator: every workspace, plans, personas"})
    print(f"role {ADMIN_ROLE}: ok")

    # Client.
    found, _, _ = kc.call("GET", f"/{REALM}/clients?clientId={CLIENT_ID}")
    if found:
        cid = found[0]["id"]
        kc.call("PUT", f"/{REALM}/clients/{cid}", {**found[0], **client_settings(apps),
                                                   "attributes": {**found[0].get("attributes", {}),
                                                                  **client_settings(apps)["attributes"]}})
        print(f"client {CLIENT_ID}: updated")
    else:
        _, headers, _ = kc.call("POST", f"/{REALM}/clients", client_settings(apps))
        cid = headers["location"].rstrip("/").rsplit("/", 1)[-1]
        print(f"client {CLIENT_ID}: created")

    # Service account: may create and look up users (account generation).
    sa, _, _ = kc.call("GET", f"/{REALM}/clients/{cid}/service-account-user")
    rm, _, _ = kc.call("GET", f"/{REALM}/clients?clientId=realm-management")
    rm_id = rm[0]["id"]
    roles = [r for r in kc.call("GET", f"/{REALM}/clients/{rm_id}/roles")[0]
             if r["name"] in SA_ROLES]
    kc.call("POST", f"/{REALM}/users/{sa['id']}/role-mappings/clients/{rm_id}", roles)
    print(f"service account: {', '.join(SA_ROLES)}")

    # Operator account.
    if a.operator_email:
        email = a.operator_email.lower()
        users, _, _ = kc.call("GET", f"/{REALM}/users?email={urllib.parse.quote(email)}&exact=true")
        if users:
            uid = users[0]["id"]
            print(f"operator {email}: exists (password unchanged)")
        else:
            pw = temp_password()
            _, headers, _ = kc.call("POST", f"/{REALM}/users", {
                "username": email, "email": email, "enabled": True, "emailVerified": True,
                "firstName": email.split("@")[0], "requiredActions": ["UPDATE_PASSWORD"],
                "credentials": [{"type": "password", "value": pw, "temporary": True}]})
            uid = headers["location"].rstrip("/").rsplit("/", 1)[-1]
            print(f"operator {email}: created, temporary password: {pw}")
        role, _, _ = kc.call("GET", f"/{REALM}/roles/{ADMIN_ROLE}")
        kc.call("POST", f"/{REALM}/users/{uid}/role-mappings/realm", [role])
        print(f"operator {email}: {ADMIN_ROLE}")

    secret, _, _ = kc.call("GET", f"/{REALM}/clients/{cid}/client-secret")
    if a.secret_out:
        with open(a.secret_out, "w", encoding="utf-8") as f:
            f.write(secret["value"])
        print(f"client secret written to {a.secret_out}")
    else:
        print(f"OSINT_OIDC_CLIENT_SECRET={secret['value']}")
    print(f"OSINT_OIDC_ISSUER={a.keycloak.rstrip('/')}/realms/{REALM}")


if __name__ == "__main__":
    main()
