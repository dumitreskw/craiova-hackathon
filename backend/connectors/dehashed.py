"""DeHashed breach exposure: which breaches an email / username / domain is in.

Passive — searches DeHashed's index of published breach data; nothing is sent
to the target. **Paid** (spends DeHashed credits per search): never run by
the quick scan, offered to the agent only when the run allows paid tools.

Data minimization (docs/06-legal-ethics.md): the platform keeps *which breach*
and *which data classes* were exposed — never passwords or hashes, and not the
other personal fields of the records. Values are dropped here, before anything
reaches the ledger.
"""

from __future__ import annotations

import os
import re

from ._http import get_client
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

API = "https://api.dehashed.com/v2/search"
SIZE = 100
_SECRET_FIELDS = {"password", "hashed_password"}
_FIELDS = ["email", "username", "name", "phone", "address", "dob", "company", "ip_address",
           "url", "social", "cryptocurrency_address", "license_plate", "password",
           "hashed_password"]


def _query(value: str) -> tuple[str, EntityType, str]:
    v = value.strip()
    if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", v):
        return f'email:"{v.lower()}"', EntityType.EMAIL, v.lower()
    if re.fullmatch(r"([a-z0-9-]+\.)+[a-z]{2,}", v.lower()):
        return f"domain:{v.lower()}", EntityType.DOMAIN, v.lower()
    if re.fullmatch(r"[\w.-]{2,64}", v):
        return f'username:"{v}"', EntityType.USERNAME, v.lower()
    raise ValueError("expected an email, domain or username")


async def dehashed(value: str) -> ConnectorResult:
    key = os.environ.get("DEHASHED_API_KEY")
    if not key:
        return ConnectorResult(source="dehashed", passive=True, raw={},
                               note="DEHASHED_API_KEY not set; skipped")
    query, etype, subject = _query(value)
    r = await get_client().post(API, timeout=45.0, headers={"Dehashed-Api-Key": key},
                                json={"query": query, "page": 1, "size": SIZE,
                                      "de_dupe": True})
    if r.status_code in (401, 403):
        raise PermissionError(f"DeHashed refused the search ({r.status_code}): no access or credits")
    r.raise_for_status()
    data = r.json()
    breaches: dict[str, dict] = {}
    for entry in data.get("entries") or []:
        name = (entry.get("database_name") or "unknown source").strip()
        b = breaches.setdefault(name, {"records": 0, "data_classes": set()})
        b["records"] += 1
        for field in _FIELDS:
            if entry.get(field):
                b["data_classes"].add("credential" if field in _SECRET_FIELDS else field)

    entities = [EntityCandidate(etype, subject)]
    rels: list[RelationshipCandidate] = []
    for name, b in sorted(breaches.items()):
        classes = sorted(b["data_classes"])
        entities.append(EntityCandidate(EntityType.BREACH, name.lower(), {
            "title": name, "data_classes": classes, "records_matched": b["records"]}))
        rels.append(RelationshipCandidate("appears_in_breach", etype, subject,
                                          EntityType.BREACH, name.lower(),
                                          {"records": b["records"],
                                           "credential_exposed": "credential" in classes}))
    return ConnectorResult(
        source="dehashed", passive=True,
        raw={"query": query, "total": data.get("total"), "balance": data.get("balance"),
             "breaches": {k: {"records": v["records"], "data_classes": sorted(v["data_classes"])}
                          for k, v in breaches.items()},
             "minimized": "record values dropped; passwords and hashes never kept"},
        entities=entities, relationships=rels,
        note=None if breaches else "not found in DeHashed")
