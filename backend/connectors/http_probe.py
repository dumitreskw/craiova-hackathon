"""HTTP probe: fetch a site's front page and extract contact signals.

ACTIVE — this is the one connector here that sends traffic to the target's own
infrastructure. ``passive=False`` so the orchestrator only runs it when the
investigation allows active connectors. Produces the page title, a light tech
guess, and ``Email`` / ``SocialProfile`` entities found in the HTML.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import (
    ConnectorResult,
    EntityCandidate,
    EntityType,
    RelationshipCandidate,
)
from ._http import get_client

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# host -> platform; matched against links on the page.
_SOCIAL_HOSTS = {
    "twitter.com": "twitter", "x.com": "twitter", "facebook.com": "facebook",
    "instagram.com": "instagram", "linkedin.com": "linkedin", "github.com": "github",
    "youtube.com": "youtube", "t.me": "telegram", "tiktok.com": "tiktok",
}

_TECH_HINTS = {
    "wp-content": "WordPress", "/_next/": "Next.js", "drupal": "Drupal",
    "cdn.shopify.com": "Shopify", "wix.com": "Wix", "squarespace": "Squarespace",
}


async def http_probe(domain: str) -> ConnectorResult:
    domain = domain.strip().lower().rstrip("/")
    url = domain if domain.startswith("http") else f"https://{domain}"
    host = urlparse(url).netloc.lower()

    client = get_client()
    resp = await client.get(url)
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.string.strip() if soup.title and soup.title.string else None
    tech = sorted({name for needle, name in _TECH_HINTS.items() if needle in html})
    emails = {e.lower() for e in _EMAIL_RE.findall(html)
              if not e.lower().endswith((".png", ".jpg", ".svg", ".gif", ".webp"))}

    socials: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        link = a["href"]
        netloc = urlparse(link).netloc.lower().removeprefix("www.")
        if netloc in _SOCIAL_HOSTS:
            socials.setdefault(f"{_SOCIAL_HOSTS[netloc]}:{link}", link)

    entities: list[EntityCandidate] = [
        EntityCandidate(EntityType.URL, url,
                        {"status": resp.status_code, "title": title, "tech": tech}),
    ]
    rels: list[RelationshipCandidate] = []

    for email in sorted(emails):
        entities.append(EntityCandidate(EntityType.EMAIL, email, match_quality=0.9))
        rels.append(RelationshipCandidate(
            "mentioned_in", EntityType.EMAIL, email, EntityType.URL, url))

    for key, link in socials.items():
        platform = key.split(":", 1)[0]
        entities.append(EntityCandidate(EntityType.SOCIAL_PROFILE, key,
                                        {"platform": platform, "url": link}))
        rels.append(RelationshipCandidate(
            "mentioned_in", EntityType.SOCIAL_PROFILE, key, EntityType.URL, url))

    return ConnectorResult(
        source="http_probe", passive=False,
        raw={"url": url, "host": host, "status": resp.status_code,
             "title": title, "tech": tech,
             "emails": sorted(emails), "socials": sorted(socials.values())},
        entities=entities, relationships=rels,
    )
