"""Gravatar connector: public profile for an email address.

Passive. Gravatar hashes the email (MD5 of the trimmed, lowercased address)
and exposes a public profile if one exists — avatar, display name, and
sometimes linked accounts. Produces a ``SocialProfile`` and any linked
``Username`` / profile URLs.
"""

from __future__ import annotations

import hashlib

from .base import (
    ConnectorResult,
    EntityCandidate,
    EntityType,
    RelationshipCandidate,
)
from ._http import get_client


def gravatar_hash(email: str) -> str:
    return hashlib.md5(email.strip().lower().encode()).hexdigest()


async def gravatar(email: str) -> ConnectorResult:
    email = email.strip().lower()
    digest = gravatar_hash(email)
    client = get_client()
    resp = await client.get(f"https://www.gravatar.com/{digest}.json")

    if resp.status_code == 404:
        return ConnectorResult(
            source="gravatar", passive=True,
            raw={"email": email, "hash": digest, "found": False},
            note="no public Gravatar profile",
        )
    resp.raise_for_status()
    data = resp.json()
    profile = (data.get("entry") or [{}])[0]

    entities: list[EntityCandidate] = [EntityCandidate(EntityType.EMAIL, email)]
    rels: list[RelationshipCandidate] = []

    profile_url = profile.get("profileUrl")
    display = profile.get("displayName") or profile.get("preferredUsername")
    if profile_url:
        key = f"gravatar:{digest}"
        entities.append(EntityCandidate(
            EntityType.SOCIAL_PROFILE, key,
            {"platform": "gravatar", "url": profile_url, "display_name": display,
             "avatar": profile.get("thumbnailUrl")}))
        rels.append(RelationshipCandidate(
            "has_profile", EntityType.EMAIL, email, EntityType.SOCIAL_PROFILE, key))

    for acct in profile.get("accounts", []):
        shortname = acct.get("shortname") or acct.get("domain")
        username = acct.get("username")
        if username and shortname:
            key = f"{shortname}:{username}"
            entities.append(EntityCandidate(
                EntityType.SOCIAL_PROFILE, key,
                {"platform": shortname, "url": acct.get("url"), "username": username}))
            rels.append(RelationshipCandidate(
                "has_profile", EntityType.EMAIL, email,
                EntityType.SOCIAL_PROFILE, key))

    return ConnectorResult(
        source="gravatar", passive=True,
        raw={"email": email, "hash": digest, "found": True, "profile": profile},
        entities=entities, relationships=rels,
    )
