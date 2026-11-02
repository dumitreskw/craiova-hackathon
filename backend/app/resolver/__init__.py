"""Resolver: fold connector results into the investigation graph.

Implements docs/03-data-model.md:

* **Deterministic merge** on ``(type, canonical key)``.
* **Provenance**: every entity / edge records which findings support it
  (``entity_sources`` / ``edge_sources``) and every attribute value keeps the
  finding ids it came from. Conflicting values are all kept.
* **Confidence** = ``1 - Π(1 - reliability_i * match_quality_i)`` over the
  supporting findings; analyst confirmation pins it to 1.0.
* **Legal / enforcement hits stay candidates.** A name-only wanted or
  sanctions hit is never merged into the investigated person, even when the
  names are identical. It becomes a separate ``candidate`` entity, keyed by
  name + official listing, linked to same-name entities only through a
  low-confidence ``possible_match`` edge. An analyst confirms or rejects it.
* **No name-only merges of people.** A Person a connector returns (a
  Companies House officer, a Hunter contact, a breach record's name) merges
  into an existing same-name Person only when it is linked through something
  that person already has: an email, username or phone, its company, or the
  entity the lookup ran on. Names compare without diacritics, case or word
  order (app/identity.py). Otherwise it becomes a separate ``namesake`` node,
  keyed by what it came with, tied to the same-name person by a
  ``possible_match`` edge, so a namesake's job, nationality and emails never
  become the subject's and the scan never follows them.
* **Name variants are leads, not merges.** A Person whose name is probably
  the same written differently (a middle or maiden name, a nickname, a
  Cyrillic or phonetic spelling; connectors/_names.py) stays its own node,
  tied to the existing person by a ``possible_match`` edge with basis
  "name variant", for the verify agent or the analyst to settle.
* **Independent sources.** Confidence counts each publisher once: two
  reads of the same site, or a search hit and the page it points to, are
  one source, not two (:func:`publisher`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from connectors._names import name_relation
from connectors.base import SOURCE_RELIABILITY, ConnectorResult, EntityCandidate, EntityType

from ..identity import identifiers, name_key, profile_for_entity
from ..models import Database, new_id, now

CANDIDATE_EDGE = "possible_match"
NAMESAKE_QUALITY = 0.3


@dataclass
class FoldResult:
    new_entities: list[dict] = field(default_factory=list)
    new_edges: list[dict] = field(default_factory=list)
    touched: set[str] = field(default_factory=set)   # entity ids seen in this finding


def combine(scores: list[tuple[float, float]]) -> float:
    """Noisy-OR of (reliability, match_quality) pairs."""
    p = 1.0
    for reliability, quality in scores:
        p *= 1.0 - max(0.0, min(1.0, reliability * quality))
    return round(1.0 - p, 4)


# Connectors that read one page: the page's site is the publisher.
PAGE_CONNECTORS = {"web_read", "screenshot", "social_capture", "http_probe"}
# The analyst's own input, however it arrived, is one source.
ANALYST_CONNECTORS = {"analyst_material", "subject_profile"}
# Second-level suffixes under which the registrable domain has three labels.
_SECOND_LEVEL = {"co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "com.au",
                 "net.au", "org.au", "co.jp", "co.nz", "co.za", "com.br", "com.tr",
                 "co.in", "com.cn", "com.mx", "com.ar", "co.il", "com.ro", "org.ro",
                 "info.ro", "nom.ro", "firm.ro", "store.ro", "www.ro", "com.ua", "com.pl"}


def site_of(value: str | None) -> str | None:
    """Registrable domain of a URL or host: "https://www.bbc.co.uk/x" -> "bbc.co.uk"."""
    if not value:
        return None
    host = urlparse(value).hostname if "://" in value else value.split("/")[0]
    host = (host or "").lower().strip(".")
    if "." not in host or host.replace(".", "").isdigit():
        return host or None
    parts = host.split(".")
    n = 3 if ".".join(parts[-2:]) in _SECOND_LEVEL and len(parts) >= 3 else 2
    return ".".join(parts[-n:])


def publisher(connector: str | None, arg: str | None, etype: str, key: str) -> str:
    """Who stands behind a finding, for counting independent sources.

    A page read is its site; a search hit about a URL is that URL's site (the
    snippet is the page); the analyst is one source; anything else is the
    connector (a registry, DNS, a breach index), with its per-type variants
    (otx_domain / otx_ip) counted as one.
    """
    if connector is None:
        return f"?{arg}"
    if connector in ANALYST_CONNECTORS:
        return "analyst"
    if connector in PAGE_CONNECTORS:
        return f"site:{site_of(arg) or arg}"
    if connector == "dork_search" and etype == EntityType.URL.value:
        return f"site:{site_of(key) or key}"
    for suffix in ("_domain", "_ip", "_org"):
        if connector.endswith(suffix):
            return connector[: -len(suffix)]
    return connector


def best_attributes(attrs: dict[str, list[dict]]) -> dict[str, Any]:
    """Flatten ``{name: [{value, sources}]}`` to the best-supported value per name."""
    out: dict[str, Any] = {}
    for name, values in attrs.items():
        if values:
            out[name] = max(values, key=lambda v: len(v["sources"]))["value"]
    return out


def conflicts(attrs: dict[str, list[dict]]) -> list[str]:
    return [name for name, values in attrs.items() if len(values) > 1]


def label_for(etype: str, key: str, attrs: dict[str, Any]) -> str:
    if etype in (EntityType.PERSON.value, EntityType.ORGANIZATION.value):
        return attrs.get("title") or attrs.get("legal_name") or key.split(" @ ")[0].title()
    if etype == EntityType.SOCIAL_PROFILE.value:
        return attrs.get("display_name") or key
    if etype == EntityType.URL.value:
        return attrs.get("title") or key
    return key


def entity_view(row: dict) -> dict:
    """API shape of an entity row."""
    attrs = best_attributes(row["attributes"])
    return {
        "id": row["id"], "type": row["type"], "key": row["key"],
        "label": label_for(row["type"], row["key"], attrs),
        "attributes": attrs, "conflicts": conflicts(row["attributes"]),
        "confidence": row["confidence"], "status": row["status"],
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
    }


def edge_view(row: dict) -> dict:
    return {
        "id": row["id"], "type": row["type"], "source": row["from_id"], "target": row["to_id"],
        "attributes": row["attributes"], "confidence": row["confidence"],
    }


class Resolver:
    def __init__(self, db: Database):
        self.db = db

    # --- public -------------------------------------------------------------

    def fold(self, investigation_id: str, finding_id: str, result: ConnectorResult,
             trigger_entity_id: str | None = None) -> FoldResult:
        out = FoldResult()
        reliability = result.reliability()
        local: dict[tuple[str, str], str] = {}
        # (type, name, listing_url) -> candidate entity id
        candidates: dict[tuple[str, str, str], str] = {}

        for cand in result.entities:
            if cand.attributes.get("unverified"):
                url = cand.attributes.get("listing_url") or self._listing_for(result, cand)
                eid = self._upsert_entity(out, investigation_id, finding_id, reliability,
                                          cand, key=f"{cand.key} @ {url}", status="candidate")
                candidates[(cand.type.value, cand.key, url)] = eid
                self._link_possible_matches(out, investigation_id, finding_id, reliability,
                                            cand, eid)
            elif cand.type == EntityType.PERSON:
                eid = self._person(out, investigation_id, finding_id, reliability, result,
                                   cand, trigger_entity_id)
                local[(cand.type.value, cand.key)] = eid
            else:
                eid = self._upsert_entity(out, investigation_id, finding_id, reliability, cand)
                local[(cand.type.value, cand.key)] = eid

        linked: set[str] = set()
        for rel in result.relationships:
            if rel.attributes.get("unverified"):
                src = candidates.get((rel.from_type.value, rel.from_key, rel.to_key))
            else:
                src = self._resolve(out, investigation_id, finding_id, reliability, local,
                                    rel.from_type, rel.from_key, result, trigger_entity_id)
            dst = self._resolve(out, investigation_id, finding_id, reliability, local,
                                rel.to_type, rel.to_key, result, trigger_entity_id)
            if not src or not dst or src == dst:
                continue
            quality = 0.3 if rel.attributes.get("unverified") else 1.0
            self._upsert_edge(out, investigation_id, finding_id, reliability, quality,
                              rel.type, src, dst, rel.attributes)
            linked.update((src, dst))

        # Keep the graph connected: anything the connector returned without an
        # edge (e.g. search hits) hangs off the entity that triggered the call.
        if trigger_entity_id:
            for eid in out.touched - linked - {trigger_entity_id}:
                row = self.db.get("entities", eid)
                if row["status"] == "candidate":
                    continue  # candidates only ever attach via possible_match
                etype = "mentioned_in" if row["type"] == EntityType.URL.value else "related_to"
                self._upsert_edge(out, investigation_id, finding_id, reliability, 1.0,
                                  etype, trigger_entity_id, eid, {"via": result.source})
        return out

    def set_status(self, entity_id: str, status: str) -> None:
        self.db.update("entities", entity_id, {"status": status})
        self._recompute_entity(entity_id)

    def merge(self, keep_id: str, merge_ids: list[str]) -> None:
        """Fold other entities into ``keep_id``: sources, attributes and edges move over."""
        keep = self.db.get("entities", keep_id)
        for mid in merge_ids:
            if mid == keep_id:
                continue
            other = self.db.get("entities", mid)
            if other is None or other["investigation_id"] != keep["investigation_id"]:
                raise ValueError(f"entity {mid} is not in the same investigation")
            for name, values in other["attributes"].items():
                for v in values:
                    for src in v["sources"]:
                        _add_attr(keep["attributes"], name, v["value"], src)
            c = self.db.conn
            c.execute("""INSERT OR IGNORE INTO entity_sources
                         SELECT ?, finding_id, reliability, match_quality
                         FROM entity_sources WHERE entity_id = ?""", (keep_id, mid))
            c.execute("DELETE FROM entity_sources WHERE entity_id = ?", (mid,))
            for col in ("from_id", "to_id"):
                for edge in self.db.query(f"SELECT * FROM edges WHERE {col} = ?", (mid,)):
                    ends = {"from_id": edge["from_id"], "to_id": edge["to_id"], col: keep_id}
                    dup = self.db.one(
                        "SELECT id FROM edges WHERE investigation_id=? AND type=? "
                        "AND from_id=? AND to_id=?",
                        (edge["investigation_id"], edge["type"], ends["from_id"], ends["to_id"]))
                    if dup or ends["from_id"] == ends["to_id"]:
                        c.execute("DELETE FROM edge_sources WHERE edge_id = ?", (edge["id"],))
                        c.execute("DELETE FROM edges WHERE id = ?", (edge["id"],))
                    else:
                        c.execute(f"UPDATE edges SET {col} = ? WHERE id = ?", (keep_id, edge["id"]))
            c.execute("UPDATE notes SET entity_id = ? WHERE entity_id = ?", (keep_id, mid))
            c.execute("UPDATE entity_reviews SET entity_id = ? WHERE entity_id = ?", (keep_id, mid))
            c.execute("UPDATE targets SET entity_id = ? WHERE entity_id = ?", (keep_id, mid))
            c.execute("DELETE FROM entities WHERE id = ?", (mid,))
        self.db.update("entities", keep_id, {"attributes": keep["attributes"],
                                             "last_seen": now()})
        self._recompute_entity(keep_id)

    def drop_finding(self, finding_id: str) -> list[str]:
        """Take one finding's support out of the graph (withdrawn material).

        Attribute values, edges and entities that only this finding supported
        go; the rest lose it as a source and get their confidence recomputed.
        Targets always stay. Returns the ids of the entities removed.
        """
        c = self.db.conn
        entity_ids = [r["entity_id"] for r in self.db.query(
            "SELECT entity_id FROM entity_sources WHERE finding_id = ?", (finding_id,))]
        edge_ids = [r["edge_id"] for r in self.db.query(
            "SELECT edge_id FROM edge_sources WHERE finding_id = ?", (finding_id,))]
        c.execute("DELETE FROM entity_sources WHERE finding_id = ?", (finding_id,))
        c.execute("DELETE FROM edge_sources WHERE finding_id = ?", (finding_id,))
        for edge_id in edge_ids:
            scores = c.execute("SELECT reliability, match_quality FROM edge_sources "
                               "WHERE edge_id = ?", (edge_id,)).fetchall()
            if scores:
                self.db.update("edges", edge_id, {"confidence": combine([tuple(s) for s in scores])})
            else:
                c.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
        removed = []
        for eid in entity_ids:
            row = self.db.get("entities", eid)
            if row is None:
                continue
            attrs = {}
            for name, values in row["attributes"].items():
                kept = [{**v, "sources": [s for s in v["sources"] if s != finding_id]}
                        for v in values]
                kept = [v for v in kept if v["sources"]]
                if kept:
                    attrs[name] = kept
            sourced = c.execute("SELECT 1 FROM entity_sources WHERE entity_id = ?",
                                (eid,)).fetchone()
            is_target = c.execute("SELECT 1 FROM targets WHERE entity_id = ?", (eid,)).fetchone()
            if sourced or is_target:
                self.db.update("entities", eid, {"attributes": attrs})
                self._recompute_entity(eid)
                continue
            for edge in self.db.query("SELECT id FROM edges WHERE from_id = ? OR to_id = ?",
                                      (eid, eid)):
                c.execute("DELETE FROM edge_sources WHERE edge_id = ?", (edge["id"],))
                c.execute("DELETE FROM edges WHERE id = ?", (edge["id"],))
            c.execute("DELETE FROM notes WHERE entity_id = ?", (eid,))
            c.execute("DELETE FROM entities WHERE id = ?", (eid,))
            removed.append(eid)
        c.commit()
        return removed

    def add_attribute(self, entity_id: str, name: str, value: Any, finding_id: str) -> None:
        """Record a derived attribute (e.g. an avatar fingerprint) under the finding
        it was derived from."""
        row = self.db.get("entities", entity_id)
        attrs = row["attributes"]
        _add_attr(attrs, name, value, finding_id)
        self.db.update("entities", entity_id, {"attributes": attrs})

    def link(self, investigation_id: str, finding_id: str, etype: str, src: str, dst: str,
             attributes: dict, quality: float = 1.0) -> dict | None:
        """Add an edge derived from a finding; returns the edge view when it is new."""
        f = self.db.get("findings", finding_id)
        reliability = SOURCE_RELIABILITY.get(f["connector"], 0.6) if f else 0.6
        out = FoldResult()
        self._upsert_edge(out, investigation_id, finding_id, reliability, quality, etype,
                          src, dst, attributes)
        return out.new_edges[0] if out.new_edges else None

    def ensure_entity(self, investigation_id: str, etype: EntityType, key: str) -> str:
        """Create (or find) a seed entity that has no finding behind it yet."""
        row = self._find(investigation_id, etype.value, key)
        if row:
            return row["id"]
        eid = new_id("e")
        ts = now()
        self.db.insert("entities", {
            "id": eid, "investigation_id": investigation_id, "type": etype.value, "key": key,
            "attributes": {}, "confidence": 1.0, "status": "active",
            "first_seen": ts, "last_seen": ts,
        })
        return eid

    # --- internals ----------------------------------------------------------

    def _find(self, investigation_id: str, etype: str, key: str) -> dict | None:
        return self.db.one(
            "SELECT * FROM entities WHERE investigation_id=? AND type=? AND key=?",
            (investigation_id, etype, key))

    def _resolve(self, out: FoldResult, investigation_id: str, finding_id: str,
                 reliability: float, local: dict, etype: EntityType, key: str,
                 result: ConnectorResult | None = None,
                 trigger_entity_id: str | None = None) -> str | None:
        if (etype.value, key) in local:
            return local[(etype.value, key)]
        if etype == EntityType.PERSON and result is not None:
            eid = self._person(out, investigation_id, finding_id, reliability, result,
                               EntityCandidate(etype, key), trigger_entity_id)
            local[(etype.value, key)] = eid
            return eid
        row = self._find(investigation_id, etype.value, key)
        if row and row["status"] != "candidate":
            return row["id"]
        # Referenced only by a relationship: it still came from this finding.
        eid = self._upsert_entity(out, investigation_id, finding_id, reliability,
                                  EntityCandidate(etype, key))
        local[(etype.value, key)] = eid
        return eid

    def _upsert_entity(self, out: FoldResult, investigation_id: str, finding_id: str,
                       reliability: float, cand: EntityCandidate, *, key: str | None = None,
                       status: str = "active") -> str:
        key = key or cand.key
        ts = now()
        row = self._find(investigation_id, cand.type.value, key)
        if row is None:
            eid = new_id("e")
            attrs: dict[str, list[dict]] = {}
            for name, value in cand.attributes.items():
                _add_attr(attrs, name, value, finding_id)
            self.db.insert("entities", {
                "id": eid, "investigation_id": investigation_id, "type": cand.type.value,
                "key": key, "attributes": attrs, "confidence": 0.0, "status": status,
                "first_seen": ts, "last_seen": ts,
            })
            created = True
        else:
            eid = row["id"]
            attrs = row["attributes"]
            for name, value in cand.attributes.items():
                _add_attr(attrs, name, value, finding_id)
            self.db.update("entities", eid, {"attributes": attrs, "last_seen": ts})
            created = False
        self.db.conn.execute(
            """INSERT INTO entity_sources (entity_id, finding_id, reliability, match_quality)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (entity_id, finding_id) DO UPDATE
               SET match_quality = max(match_quality, excluded.match_quality)""",
            (eid, finding_id, reliability, cand.match_quality))
        self.db.conn.commit()
        self._recompute_entity(eid)
        out.touched.add(eid)
        if created:
            out.new_entities.append(entity_view(self.db.get("entities", eid)))
        return eid

    def _upsert_edge(self, out: FoldResult, investigation_id: str, finding_id: str,
                     reliability: float, quality: float, etype: str, src: str, dst: str,
                     attributes: dict) -> str:
        ts = now()
        row = self.db.one(
            "SELECT * FROM edges WHERE investigation_id=? AND type=? AND from_id=? AND to_id=?",
            (investigation_id, etype, src, dst))
        if row is None:
            edge_id = new_id("r")
            self.db.insert("edges", {
                "id": edge_id, "investigation_id": investigation_id, "type": etype,
                "from_id": src, "to_id": dst, "attributes": attributes or {},
                "confidence": 0.0, "first_seen": ts, "last_seen": ts,
            })
            created = True
        else:
            edge_id = row["id"]
            self.db.update("edges", edge_id, {
                "attributes": {**row["attributes"], **(attributes or {})}, "last_seen": ts})
            created = False
        self.db.conn.execute(
            """INSERT INTO edge_sources (edge_id, finding_id, reliability, match_quality)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (edge_id, finding_id) DO UPDATE
               SET match_quality = max(match_quality, excluded.match_quality)""",
            (edge_id, finding_id, reliability, quality))
        self.db.conn.commit()
        scores = self.db.conn.execute(
            "SELECT reliability, match_quality FROM edge_sources WHERE edge_id = ?",
            (edge_id,)).fetchall()
        self.db.update("edges", edge_id, {"confidence": combine([tuple(s) for s in scores])})
        if created:
            out.new_edges.append(edge_view(self.db.get("edges", edge_id)))
        return edge_id

    def publishers(self, entity_id: str) -> dict[str, float]:
        """Independent sources of an entity: publisher -> its best score."""
        row = self.db.get("entities", entity_id)
        out: dict[str, float] = {}
        for s in self.db.query(
                "SELECT es.finding_id, es.reliability, es.match_quality, f.connector, f.input "
                "FROM entity_sources es LEFT JOIN findings f ON f.id = es.finding_id "
                "WHERE es.entity_id = ?", (entity_id,)):
            arg = (s["input"] or {}).get("arg") if s["connector"] else s["finding_id"]
            who = publisher(s["connector"], arg, row["type"], row["key"])
            score = max(0.0, min(1.0, s["reliability"] * s["match_quality"]))
            out[who] = max(out.get(who, 0.0), score)
        return out

    def _recompute_entity(self, entity_id: str) -> None:
        row = self.db.get("entities", entity_id)
        if row["status"] == "confirmed":
            conf = 1.0
        else:
            by_publisher = self.publishers(entity_id)
            conf = (combine([(score, 1.0) for score in by_publisher.values()])
                    if by_publisher else row["confidence"])
        self.db.update("entities", entity_id, {"confidence": conf})

    # --- people ------------------------------------------------------------------------

    def _person(self, out: FoldResult, investigation_id: str, finding_id: str,
                reliability: float, result: ConnectorResult, cand: EntityCandidate,
                trigger_entity_id: str | None) -> str:
        """Fold a Person: into a same-name person it is linked to, or as a namesake."""
        nkey = name_key(cand.key)
        same = [r for r in self.db.query(
            "SELECT id, key, attributes FROM entities WHERE investigation_id = ? "
            "AND type = 'Person' AND status != 'candidate'", (investigation_id,))
            if name_key(r["key"]) == nkey] if nkey else []
        if not same:
            eid = self._upsert_entity(out, investigation_id, finding_id, reliability, cand)
            self._link_variants(out, investigation_id, finding_id, reliability, cand.key, eid)
            return eid
        context = self._context(result, cand.key)
        # The subject (no namesake flag) first.
        for row in sorted(same, key=lambda r: "namesake" in (r["attributes"] or {})):
            if self._linked(investigation_id, row["id"], context, trigger_entity_id):
                return self._upsert_entity(out, investigation_id, finding_id, reliability,
                                           cand, key=row["key"])
        # A namesake: its own node, keyed by what it came with, so the same
        # namesake seen again through the same company or email lands on it.
        ctx = next((k for _t, k in context), None)
        if ctx is None and trigger_entity_id:
            trig = self.db.get("entities", trigger_entity_id)
            ctx = trig["key"] if trig else None
        base = cand.key.split(" @ ")[0]
        key = cand.key if " @ " in cand.key else f"{base} @ {ctx or result.source}"
        attrs = {**cand.attributes, "namesake": True,
                 "title": cand.attributes.get("title") or base.title()}
        eid = self._upsert_entity(out, investigation_id, finding_id, reliability,
                                  EntityCandidate(cand.type, cand.key, attrs,
                                                  cand.match_quality), key=key)
        for row in same:
            # Only the plain-named person (the subject) gets the link, not other namesakes.
            if row["id"] == eid or " @ " in row["key"] or "namesake" in (row["attributes"] or {}):
                continue
            self._upsert_edge(out, investigation_id, finding_id, reliability, NAMESAKE_QUALITY,
                              CANDIDATE_EDGE, row["id"], eid,
                              {"unverified": True, "basis": "same name only",
                               "namesake": True})
        return eid

    def _link_variants(self, out: FoldResult, investigation_id: str, finding_id: str,
                       reliability: float, key: str, eid: str) -> None:
        """possible_match from same-name-variant people (never a merge)."""
        for row in self.db.query(
                "SELECT id, key, attributes FROM entities WHERE investigation_id = ? "
                "AND type = 'Person' AND status != 'candidate' AND id != ?",
                (investigation_id, eid)):
            if "namesake" in (row["attributes"] or {}):
                continue
            if name_relation(row["key"], key) == "variant":
                self._upsert_edge(out, investigation_id, finding_id, reliability,
                                  NAMESAKE_QUALITY, CANDIDATE_EDGE, row["id"], eid,
                                  {"unverified": True, "basis": "name variant"})

    @staticmethod
    def _context(result: ConnectorResult, person_key: str) -> list[tuple[str, str]]:
        """``(type, key)`` of what the result ties this person to."""
        out: list[tuple[str, str]] = []
        for rel in result.relationships:
            if rel.attributes.get("unverified"):
                continue
            if rel.from_type == EntityType.PERSON and rel.from_key == person_key:
                other = (rel.to_type.value, rel.to_key)
            elif rel.to_type == EntityType.PERSON and rel.to_key == person_key:
                other = (rel.from_type.value, rel.from_key)
            else:
                continue
            if other[0] != EntityType.PERSON.value and other not in out:
                out.append(other)
        return out

    def _linked(self, investigation_id: str, person_id: str, context: list[tuple[str, str]],
                trigger_entity_id: str | None) -> bool:
        """Is ``person_id`` tied to anything in ``context`` or to the lookup's entity?

        A person's anchors: itself, its direct neighbours (not through a
        possible_match), and for a target the profile's identifiers and
        employers and the investigation's other targets.
        """
        anchors = {person_id}
        for e in self.db.query(
                "SELECT from_id, to_id FROM edges WHERE (from_id = ? OR to_id = ?) "
                "AND type != ?", (person_id, person_id, CANDIDATE_EDGE)):
            anchors.add(e["to_id"] if e["from_id"] == person_id else e["from_id"])
        keys: set[tuple[str, str]] = set()
        employers: set[str] = set()
        if self.db.one("SELECT 1 FROM targets WHERE entity_id = ?", (person_id,)):
            anchors |= {r["entity_id"] for r in self.db.query(
                "SELECT entity_id FROM targets WHERE investigation_id = ? "
                "AND entity_id IS NOT NULL", (investigation_id,))}
            profile = profile_for_entity(self.db, person_id)
            for value, etype in identifiers(profile):
                keys.add((etype, "+" + "".join(c for c in value if c.isdigit())
                          if etype == EntityType.PHONE.value else value.lower()))
            employers = {name_key(x) for x in profile.get("employers") or []}
        if trigger_entity_id and trigger_entity_id in anchors:
            return True
        for etype, key in context:
            if (etype, key) in keys:
                return True
            row = self._find(investigation_id, etype, key)
            if row and row["status"] != "rejected" and row["id"] in anchors:
                return True
            if etype == EntityType.ORGANIZATION.value and employers:
                names = {name_key(key)}
                if row:
                    names |= {name_key(str(v["value"]))
                              for v in row["attributes"].get("legal_name", [])}
                if names & employers:
                    return True
        return False

    def _link_possible_matches(self, out: FoldResult, investigation_id: str, finding_id: str,
                               reliability: float, cand: EntityCandidate,
                               candidate_id: str) -> None:
        for row in self.db.query(
                "SELECT id, key, attributes FROM entities WHERE investigation_id=? AND type=? "
                "AND status != 'candidate'", (investigation_id, cand.type.value)):
            if "namesake" in (row["attributes"] or {}):
                continue
            relation = name_relation(row["key"], cand.key)
            if relation is None:
                continue
            self._upsert_edge(out, investigation_id, finding_id, reliability, cand.match_quality,
                              CANDIDATE_EDGE, row["id"], candidate_id,
                              {"unverified": True, "basis": "name match only"
                               if relation == "same" else "name variant"})

    @staticmethod
    def _listing_for(result: ConnectorResult, cand: EntityCandidate) -> str:
        for rel in result.relationships:
            if rel.from_type == cand.type and rel.from_key == cand.key:
                return rel.to_key
        return result.source


def _add_attr(attrs: dict[str, list[dict]], name: str, value: Any, finding_id: str) -> None:
    if value in (None, "", [], {}):
        return
    values = attrs.setdefault(name, [])
    for v in values:
        if v["value"] == value:
            if finding_id not in v["sources"]:
                v["sources"].append(finding_id)
            return
    values.append({"value": value, "sources": [finding_id]})
