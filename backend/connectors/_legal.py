"""Shared helpers for legal / law-enforcement connectors.

These sources (wanted notices, sanctions) match mostly on name, so every hit is
a **possible** match, never a verdict. This module centralizes that rule so all
legal connectors behave the same:

* a hit produces a low-``match_quality`` Person/Organization candidate so the
  resolver keeps it as an *unverified candidate* and does not attach it to a
  confirmed person;
* the hit carries ``unverified: True`` and a list of ``disambiguators`` (DOB,
  nationality, ...) the analyst can use to confirm or rule it out;
* a ``URL`` entity for the official listing is emitted so the report agent can
  screenshot the source.

See docs/04-sources.md#legal--law-enforcement and docs/02-architecture.md
(analysis / profiling agent — "legal hits are candidates, not verdicts").
"""

from __future__ import annotations

from .base import EntityCandidate, EntityType, RelationshipCandidate

# Name-only matches are weak evidence. This keeps confidence low until an
# analyst corroborates with a disambiguator. Do not raise it per-source.
NAME_MATCH_QUALITY = 0.3

UNVERIFIED_NOTE = (
    "Name-based matches — possible matches only, NOT confirmed. Each is an "
    "unverified candidate pending a disambiguator (date of birth, nationality, "
    "photo, ID). Do not report anyone as wanted/sanctioned without corroboration."
)


def normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def legal_hit(
    *,
    subject_name: str,
    subject_type: EntityType,
    edge: str,
    source_url: str,
    source: str,
    attributes: dict,
    disambiguators: dict,
) -> tuple[list[EntityCandidate], list[RelationshipCandidate]]:
    """Build the (entities, relationships) for one unverified legal hit.

    ``edge`` e.g. "appears_in_wanted_list" / "appears_in_sanctions".
    ``source_url`` is the official listing, kept as a URL entity for evidence.
    """
    key = normalize_name(subject_name)
    subject_attrs = {
        **attributes,
        "unverified": True,
        # Lets the resolver keep two same-name hits apart (one per listing).
        "listing_url": source_url,
        "disambiguators": {k: v for k, v in disambiguators.items() if v},
    }
    subject = EntityCandidate(subject_type, key, subject_attrs,
                              match_quality=NAME_MATCH_QUALITY)

    listing = EntityCandidate(
        EntityType.URL, source_url,
        {"kind": "official_listing", "source": source, "title": attributes.get("title")},
    )
    rel = RelationshipCandidate(
        edge, subject_type, key, EntityType.URL, source_url,
        attributes={"source": source, "unverified": True, **disambiguators},
    )
    return [subject, listing], [rel]
