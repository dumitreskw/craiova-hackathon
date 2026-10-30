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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from connectors.base import ConnectorResult, EntityCandidate, EntityType

from ..models import Database, new_id, now

CANDIDATE_EDGE = "possible_match"


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
            else:
                eid = self._upsert_entity(out, investigation_id, finding_id, reliability, cand)
                local[(cand.type.value, cand.key)] = eid

        linked: set[str] = set()
        for rel in result.relationships:
            if rel.attributes.get("unverified"):
                src = candidates.get((rel.from_type.value, rel.from_key, rel.to_key))
            else:
                src = self._resolve(out, investigation_id, finding_id, reliability, local,
                                    rel.from_type, rel.from_key)
            dst = self._resolve(out, investigation_id, finding_id, reliability, local,
                                rel.to_type, rel.to_key)
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
                 reliability: float, local: dict, etype: EntityType, key: str) -> str | None:
        if (etype.value, key) in local:
            return local[(etype.value, key)]
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

    def _recompute_entity(self, entity_id: str) -> None:
        row = self.db.get("entities", entity_id)
        if row["status"] == "confirmed":
            conf = 1.0
        else:
            scores = self.db.conn.execute(
                "SELECT reliability, match_quality FROM entity_sources WHERE entity_id = ?",
                (entity_id,)).fetchall()
            conf = combine([tuple(s) for s in scores]) if scores else row["confidence"]
        self.db.update("entities", entity_id, {"confidence": conf})

    def _link_possible_matches(self, out: FoldResult, investigation_id: str, finding_id: str,
                               reliability: float, cand: EntityCandidate,
                               candidate_id: str) -> None:
        for row in self.db.query(
                "SELECT id FROM entities WHERE investigation_id=? AND type=? AND key=? "
                "AND status != 'candidate'", (investigation_id, cand.type.value, cand.key)):
            self._upsert_edge(out, investigation_id, finding_id, reliability, cand.match_quality,
                              CANDIDATE_EDGE, row["id"], candidate_id,
                              {"unverified": True, "basis": "name match only"})

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
