"""Seed auto-detection: turn what the analyst typed into a typed entity key.

The key must match the canonical key the connectors emit
(docs/03-data-model.md#entity-types), or the seed and the connector output
would land in the graph as two different nodes.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from connectors.base import EntityType

_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_CUI = re.compile(r"^(RO)?\d{2,10}$", re.IGNORECASE)
_UK_COMPANY = re.compile(r"^(GB|UK)[0-9A-Z]{2}\d{6}$", re.IGNORECASE)
_PHONE = re.compile(r"^\+\d[\d\s().-]{6,}$")
_USERNAME = re.compile(r"^@?[A-Za-z0-9_.-]{2,39}$")


class DetectError(ValueError):
    pass


def normalize_name(name: str) -> str:
    # Same rule as connectors._legal.normalize_name, so seeds and hits compare.
    return " ".join(name.strip().lower().split())


def detect(value: str, hint: str = "auto") -> tuple[EntityType, str]:
    """Return ``(type, canonical_key)`` for a seed. ``hint`` forces a type."""
    raw = value.strip()
    if not raw:
        raise DetectError("empty target")

    if hint != "auto":
        try:
            etype = EntityType(hint)
        except ValueError as e:
            raise DetectError(f"unknown entity type: {hint}") from e
        return etype, canonical_key(etype, raw)

    if raw.lower().startswith(("http://", "https://")):
        host = urlparse(raw).hostname or ""
        return EntityType.DOMAIN, canonical_key(EntityType.DOMAIN, host)
    if _EMAIL.match(raw):
        return EntityType.EMAIL, raw.lower()
    try:
        return EntityType.IP, str(ipaddress.ip_address(raw))
    except ValueError:
        pass
    if _DOMAIN.match(raw.lower().rstrip(".")):
        return EntityType.DOMAIN, canonical_key(EntityType.DOMAIN, raw)
    if _CUI.match(raw.replace(" ", "")) or _UK_COMPANY.match(raw.replace(" ", "")):
        return EntityType.ORGANIZATION, canonical_key(EntityType.ORGANIZATION, raw)
    if _PHONE.match(raw):
        return EntityType.PHONE, canonical_key(EntityType.PHONE, raw)
    if " " not in raw and _USERNAME.match(raw):
        return EntityType.USERNAME, raw.lstrip("@").lower()
    if " " in raw:
        return EntityType.PERSON, normalize_name(raw)
    raise DetectError(f"could not detect the type of {value!r}; pass an explicit type")


def canonical_key(etype: EntityType, value: str) -> str:
    v = value.strip()
    if etype == EntityType.DOMAIN:
        return v.lower().rstrip(".").encode("idna").decode("ascii")
    if etype in (EntityType.EMAIL, EntityType.USERNAME):
        return v.lstrip("@").lower()
    if etype == EntityType.PHONE:
        return "+" + re.sub(r"\D", "", v)
    if etype == EntityType.ORGANIZATION and _CUI.match(v.replace(" ", "")):
        return "RO" + re.sub(r"\D", "", v)
    if etype == EntityType.ORGANIZATION and _UK_COMPANY.match(v.replace(" ", "")):
        return "GB" + v.replace(" ", "").upper()[2:]
    if etype in (EntityType.PERSON, EntityType.ORGANIZATION):
        return normalize_name(v)
    if etype == EntityType.IP:
        return str(ipaddress.ip_address(v))
    return v
