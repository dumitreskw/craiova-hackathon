"""DeHashed breach exposure: which breaches an email / username / domain / phone
is in, and the PII exposed in each record.

Passive — searches DeHashed's index of published breach data; nothing is sent
to the target. **Paid** (spends DeHashed credits per search): never run by
the quick scan, offered to the agent only when the run allows paid tools.

For every breach record the connector extracts the exposed PII — phone numbers,
addresses, names, and credentials (passwords / hashes) — and creates entities
and relationships so the graph links the subject to each leaked field with its
breach provenance.
"""

from __future__ import annotations

import os
import re
from typing import Any

from ._http import get_client
from .base import ConnectorResult, EntityCandidate, EntityType, RelationshipCandidate

API = "https://api.dehashed.com/v2/search"
SIZE = 100
_SECRET_FIELDS = {"password", "hashed_password"}
_FIELDS = ["email", "username", "name", "phone", "address", "dob", "company", "ip_address",
           "url", "social", "cryptocurrency_address", "license_plate", "password",
           "hashed_password"]


def _norm_phone(phone: str) -> str:
    return "+" + re.sub(r"\D", "", phone)


def _norm_addr(addr: str) -> str:
    return " ".join(addr.lower().split())


def _norm_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _query(value: str) -> tuple[str, EntityType, str]:
    v = value.strip()
    if re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", v):
        return f'email:"{v.lower()}"', EntityType.EMAIL, v.lower()
    if re.fullmatch(r"([a-z0-9-]+\.)+[a-z]{2,}", v.lower()):
        return f"domain:{v.lower()}", EntityType.DOMAIN, v.lower()
    if re.fullmatch(r"\+\d[\d\s().-]{6,}", v):
        pkey = _norm_phone(v)
        return f'phone:"{pkey}"', EntityType.PHONE, pkey
    if re.fullmatch(r"[\w.-]{2,64}", v):
        return f'username:"{v}"', EntityType.USERNAME, v.lower()
    raise ValueError("expected an email, domain, phone or username")


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
    phones: dict[str, list[str]] = {}
    addresses: dict[str, list[str]] = {}
    names: dict[str, list[str]] = {}
    for entry in data.get("entries") or []:
        bname = (entry.get("database_name") or "unknown source").strip()
        b = breaches.setdefault(bname, {"records": 0, "data_classes": set(),
                                        "passwords": [], "hashed_passwords": []})
        b["records"] += 1
        for field in _FIELDS:
            val = entry.get(field)
            if not val:
                continue
            # DeHashed returns fields as strings or arrays; normalize to strings.
            vals = [str(v).strip() for v in (val if isinstance(val, list) else [val])
                    if str(v).strip()]
            if not vals:
                continue
            if field in _SECRET_FIELDS:
                b["data_classes"].add("credential")
                if field == "password":
                    b["passwords"].extend(vals)
                else:
                    b["hashed_passwords"].extend(vals)
            else:
                b["data_classes"].add(field)
            if field == "phone":
                for v in vals:
                    phones.setdefault(_norm_phone(v), []).append(bname)
            elif field == "address":
                for v in vals:
                    addresses.setdefault(_norm_addr(v), []).append(bname)
            elif field == "name":
                for v in vals:
                    names.setdefault(_norm_name(v), []).append(bname)

    entities = [EntityCandidate(etype, subject)]
    rels: list[RelationshipCandidate] = []
    for bname, b in sorted(breaches.items()):
        classes = sorted(b["data_classes"])
        breach_key = bname.lower()
        entities.append(EntityCandidate(EntityType.BREACH, breach_key, {
            "title": bname, "data_classes": classes, "records_matched": b["records"]}))
        attrs: dict[str, Any] = {"records": b["records"],
                                 "credential_exposed": "credential" in classes}
        if b["passwords"]:
            attrs["passwords"] = b["passwords"]
        if b["hashed_passwords"]:
            attrs["hashed_passwords"] = b["hashed_passwords"]
        rels.append(RelationshipCandidate("appears_in_breach", etype, subject,
                                          EntityType.BREACH, breach_key, attrs))
    for pkey, sources in sorted(phones.items()):
        entities.append(EntityCandidate(EntityType.PHONE, pkey, {"breaches": sources}))
        rels.append(RelationshipCandidate("has_phone", etype, subject,
                                          EntityType.PHONE, pkey,
                                          {"breaches": sources}))
    for akey, sources in sorted(addresses.items()):
        entities.append(EntityCandidate(EntityType.LOCATION, akey,
                                        {"address": akey, "breaches": sources}))
        rels.append(RelationshipCandidate("located_at", etype, subject,
                                          EntityType.LOCATION, akey,
                                          {"breaches": sources}))
    for nkey, sources in sorted(names.items()):
        entities.append(EntityCandidate(EntityType.PERSON, nkey,
                                        {"title": nkey, "breaches": sources}))
        rels.append(RelationshipCandidate("identified_as", etype, subject,
                                          EntityType.PERSON, nkey,
                                          {"breaches": sources}))
    return ConnectorResult(
        source="dehashed", passive=True,
        raw={"query": query, "total": data.get("total"), "balance": data.get("balance"),
             "breaches": {k: {"records": v["records"],
                              "data_classes": sorted(v["data_classes"]),
                              "passwords": v["passwords"],
                              "hashed_passwords": v["hashed_passwords"]}
                          for k, v in breaches.items()},
             "phones": sorted(phones), "addresses": sorted(addresses),
             "names": sorted(names)},
        entities=entities, relationships=rels,
        note=None if breaches else "not found in DeHashed")
