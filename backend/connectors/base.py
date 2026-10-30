"""Shared connector contract.

Every connector is a plain async function that takes a single seed value and
returns a :class:`ConnectorResult`. The same functions are used two ways:

* imported directly by the quick-scan / agent code, and
* wrapped as tools by ``mcp_server.py``.

Keep connectors free of side effects beyond the network lookup, and never
raise for an "expected" empty result — return an empty
:class:`ConnectorResult` instead. Reserve exceptions for genuine failures
(network errors, bad input); the server turns those into tool errors.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EntityType(str, Enum):
    PERSON = "Person"
    ORGANIZATION = "Organization"
    DOMAIN = "Domain"
    URL = "URL"
    IP = "IPAddress"
    ASN = "ASN"
    EMAIL = "Email"
    USERNAME = "Username"
    PHONE = "Phone"
    SOCIAL_PROFILE = "SocialProfile"
    CERTIFICATE = "Certificate"
    LOCATION = "Location"
    DOCUMENT = "Document"
    BREACH = "Breach"
    REPOSITORY = "Repository"
    VULNERABILITY = "Vulnerability"


# Static per-source reliability, used as the base for confidence scoring.
# Official registries and DNS are trusted; scraped / name-matched sources are not.
# See docs/03-data-model.md#confidence.
SOURCE_RELIABILITY: dict[str, float] = {
    "dns": 0.95,
    "rdap": 0.95,
    "crtsh": 0.9,
    "http_probe": 0.85,
    "anaf": 0.95,
    "vies": 0.95,
    "gravatar": 0.8,
    "github_user": 0.85,
    "dork_search": 0.5,
    # Legal/enforcement: the SOURCE is authoritative (high reliability), but
    # the per-hit match_quality is low (name-based), so confidence stays low
    # until an analyst disambiguates. See connectors/_legal.py.
    "interpol": 0.95,
    "europol": 0.9,
    "sanctions": 0.9,
    "national_wanted": 0.9,
}


@dataclass(slots=True)
class EntityCandidate:
    """A possible entity extracted from a finding.

    ``key`` is the canonical identifier used for deterministic de-duplication
    (lowercase domain, E.164 phone, lowercase email, ...). The resolver merges
    candidates that share ``(type, key)``.
    """

    type: EntityType
    key: str
    attributes: dict[str, Any] = field(default_factory=dict)
    # 1.0 for a deterministic/exact extraction; lower for fuzzy or name-only.
    match_quality: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "key": self.key,
            "attributes": self.attributes,
            "match_quality": self.match_quality,
        }


@dataclass(slots=True)
class RelationshipCandidate:
    """A possible edge between two entities, referenced by canonical key."""

    type: str  # e.g. "resolves_to", "registered_by", "mentioned_in"
    from_type: EntityType
    from_key: str
    to_type: EntityType
    to_key: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "from": {"type": self.from_type.value, "key": self.from_key},
            "to": {"type": self.to_type.value, "key": self.to_key},
            "attributes": self.attributes,
        }


@dataclass(slots=True)
class ConnectorResult:
    """What every connector returns.

    ``raw`` is stored unchanged as evidence (provenance). ``entities`` and
    ``relationships`` are candidates the resolver folds into the graph.
    """

    source: str
    passive: bool
    raw: Any
    entities: list[EntityCandidate] = field(default_factory=list)
    relationships: list[RelationshipCandidate] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Set when the connector was asked for something it does not support, or a
    # key is missing — distinct from an exception, which means it broke.
    note: str | None = None

    def reliability(self) -> float:
        return SOURCE_RELIABILITY.get(self.source, 0.6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "passive": self.passive,
            "fetched_at": self.fetched_at.isoformat(),
            "reliability": self.reliability(),
            "note": self.note,
            "raw": self.raw,
            "entities": [e.to_dict() for e in self.entities],
            "relationships": [r.to_dict() for r in self.relationships],
            "sha256": self.raw_hash(),
        }

    def raw_hash(self) -> str:
        """Content hash of the raw payload, for evidence integrity."""
        blob = json.dumps(self.raw, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()
