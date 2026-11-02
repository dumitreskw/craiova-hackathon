"""Same profile picture, same person: avatar fingerprints across accounts.

Accounts rarely share an email, but people reuse their photo. Every entity
that a connector gives an ``avatar`` URL (GitHub, Gravatar, persona captures
of social profiles) gets a 64-bit perceptual fingerprint (difference hash:
robust to resizing and recompression, cheap, no model). Two entities of the
investigation whose fingerprints differ in at most :data:`MAX_DISTANCE` bits
get a ``same_avatar`` edge, with the finding that supplied the picture as its
source. The resolver counts that edge as a link (app/resolver ``_linked``)
and the verify agent sees it.

Generic pictures must never link strangers, so a picture is skipped when it
is a placeholder (a default URL, a near-blank image), and a fingerprint shared
by more than :data:`MAX_SHARED` entities in an investigation is treated as a
default avatar: no edges.
"""

from __future__ import annotations

import io
import logging
from urllib.parse import urlencode, urlparse, urlunparse

from connectors._http import get_client

from .models import Database

log = logging.getLogger(__name__)

ATTR = "avatar"
HASH_ATTR = "avatar_hash"
EDGE = "same_avatar"
MAX_DISTANCE = 6          # of 64 bits
MAX_SHARED = 3            # more entities than this with one picture = a default picture
MAX_BYTES = 3_000_000
MIN_CONTRAST = 6.0        # grey-level spread below which a picture is blank
_GENERIC = ("ghost", "default", "placeholder", "blank", "no-avatar", "noavatar",
            "anonymous", "silhouette", "/static/")


def dhash(data: bytes) -> int | None:
    """64-bit difference hash of an image, or ``None`` for a blank / unreadable one."""
    try:
        from PIL import Image, ImageStat
    except ImportError:   # pragma: no cover - Pillow is a dependency
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001 - any undecodable payload is just "no picture"
        return None
    grey = img.convert("L")
    if ImageStat.Stat(grey.resize((32, 32))).stddev[0] < MIN_CONTRAST:
        return None
    small = grey.resize((9, 8), Image.Resampling.LANCZOS)
    px = list(small.tobytes())   # one byte per pixel in mode "L"
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _fetch_url(url: str) -> str | None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or any(g in url.lower() for g in _GENERIC):
        return None
    if (p.hostname or "").endswith("gravatar.com"):
        # No picture -> 404 instead of Gravatar's default silhouette.
        return urlunparse(p._replace(query=urlencode({"d": "404", "s": "128"})))
    return url


async def fingerprint(url: str) -> int | None:
    target = _fetch_url(url)
    if target is None:
        return None
    try:
        r = await get_client().get(target, timeout=15.0, follow_redirects=True)
    except Exception as e:  # noqa: BLE001 - a picture we can't load is no evidence
        log.info("avatar fetch failed for %s: %s", url, e)
        return None
    if r.status_code != 200 or len(r.content) > MAX_BYTES \
            or not r.headers.get("content-type", "image").startswith("image"):
        return None
    return dhash(r.content)


def _avatar(row: dict) -> tuple[str, str] | None:
    """``(url, finding_id)`` of an entity's avatar, if it has one."""
    for v in (row["attributes"] or {}).get(ATTR, []):
        if isinstance(v.get("value"), str) and v["value"].startswith("http") and v["sources"]:
            return v["value"], v["sources"][0]
    return None


def _hash_of(row: dict) -> int | None:
    vals = (row["attributes"] or {}).get(HASH_ATTR) or []
    try:
        return int(vals[0]["value"], 16) if vals else None
    except (ValueError, TypeError):
        return None


async def process(ledger, investigation_id: str, entity_ids: list[str]) -> list[dict]:
    """Fingerprint the new avatars among ``entity_ids`` and link the matches.
    Returns the new edges."""
    db: Database = ledger.db
    fresh: list[tuple[str, int, str]] = []
    for eid in entity_ids:
        row = db.get("entities", eid)
        if row is None or _hash_of(row) is not None:
            continue
        found = _avatar(row)
        if not found:
            continue
        url, finding_id = found
        h = await fingerprint(url)
        if h is None:
            continue
        ledger.resolver.add_attribute(eid, HASH_ATTR, f"{h:016x}", finding_id)
        fresh.append((eid, h, finding_id))
    if not fresh:
        return []
    known = [(r["id"], _hash_of(r)) for r in db.query(
        "SELECT id, attributes FROM entities WHERE investigation_id = ? AND status != 'rejected' "
        "AND attributes LIKE ?", (investigation_id, f'%"{HASH_ATTR}"%'))]
    known = [(i, h) for i, h in known if h is not None]
    edges = []
    for eid, h, finding_id in fresh:
        same = [other for other, oh in known if other != eid and distance(h, oh) <= MAX_DISTANCE]
        if not same or len(same) >= MAX_SHARED:
            continue   # unique, or a default picture many accounts show
        for other in same:
            edge = ledger.resolver.link(investigation_id, finding_id, EDGE, other, eid,
                                        {"bits_different": distance(h, dict(known)[other]),
                                         "basis": "same profile picture"}, quality=0.8)
            if edge:
                edges.append(edge)
                ledger.bus.publish(investigation_id, "edge.created", {"edge": edge})
    return edges
