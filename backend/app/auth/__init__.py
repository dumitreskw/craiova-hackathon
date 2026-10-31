"""Accounts, sign-in (Keycloak / OpenID Connect) and workspaces (tenants).

See docs/10-auth.md. Pieces:

* ``settings``   environment (issuer, client, public URL)
* ``oidc``       Keycloak: code flow + PKCE, token verification, admin API
* ``store``      users, workspaces, memberships, invitations, sessions, API keys
* ``service``    who is calling (cookie / API key / bearer token), finishing sign-in
* ``middleware`` enforces it on /api and /mcp; sets the request's principal
* ``routes``     /auth/* and /invite/*
* ``mcp_proxy``  /mcp forwarded to the internal MCP server
"""

from .context import Principal, current_principal
from .middleware import AuthMiddleware, get_auth
from .service import Auth
from .settings import AuthSettings, load_auth_settings

__all__ = ["Auth", "AuthMiddleware", "AuthSettings", "Principal", "current_principal",
           "get_auth", "load_auth_settings"]
