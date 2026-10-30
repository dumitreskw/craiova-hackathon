"""GitHub connector: public user profile and repositories.

Passive. Works unauthenticated (low rate limit); if ``GITHUB_TOKEN`` is set in
the environment it is used for a higher limit. Produces the user's profile,
linked email/blog, and ``Repository`` entities.
"""

from __future__ import annotations

import os

from .base import (
    ConnectorResult,
    EntityCandidate,
    EntityType,
    RelationshipCandidate,
)
from ._http import get_client

API = "https://api.github.com"


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def github_user(username: str) -> ConnectorResult:
    username = username.strip().lstrip("@")
    client = get_client()
    resp = await client.get(f"{API}/users/{username}", headers=_headers())

    if resp.status_code == 404:
        return ConnectorResult(
            source="github_user", passive=True,
            raw={"username": username, "found": False},
            note="no such GitHub user",
        )
    resp.raise_for_status()
    user = resp.json()

    profile_key = f"github:{username}"
    entities: list[EntityCandidate] = [
        EntityCandidate(EntityType.USERNAME, username, {"platform": "github"}),
        EntityCandidate(EntityType.SOCIAL_PROFILE, profile_key, {
            "platform": "github", "url": user.get("html_url"),
            "display_name": user.get("name"), "bio": user.get("bio"),
            "company": user.get("company"), "location": user.get("location"),
            "followers": user.get("followers"), "public_repos": user.get("public_repos"),
            "created_at": user.get("created_at"),
        }),
    ]
    rels: list[RelationshipCandidate] = [
        RelationshipCandidate("has_profile", EntityType.USERNAME, username,
                              EntityType.SOCIAL_PROFILE, profile_key),
    ]

    if user.get("email"):
        email = user["email"].lower()
        entities.append(EntityCandidate(EntityType.EMAIL, email, match_quality=0.9))
        rels.append(RelationshipCandidate(
            "uses_email", EntityType.USERNAME, username, EntityType.EMAIL, email))

    # Top repositories by recent activity.
    repos_resp = await client.get(
        f"{API}/users/{username}/repos",
        params={"sort": "pushed", "per_page": 20}, headers=_headers())
    repos = repos_resp.json() if repos_resp.status_code == 200 else []
    repo_list = []
    for repo in repos if isinstance(repos, list) else []:
        full = repo.get("full_name")
        if not full:
            continue
        key = f"github:{full}"
        repo_list.append(full)
        entities.append(EntityCandidate(EntityType.REPOSITORY, key, {
            "url": repo.get("html_url"), "language": repo.get("language"),
            "stars": repo.get("stargazers_count"), "fork": repo.get("fork"),
            "pushed_at": repo.get("pushed_at"),
        }))
        rels.append(RelationshipCandidate(
            "owns", EntityType.USERNAME, username, EntityType.REPOSITORY, key))

    return ConnectorResult(
        source="github_user", passive=True,
        raw={"username": username, "found": True, "user": user, "repos": repo_list},
        entities=entities, relationships=rels,
    )
