# 3. Data Model

## Entity types

| Type             | Canonical key                    | Notable attributes                                    |
|------------------|----------------------------------|-------------------------------------------------------|
| `Person`         | generated UUID (fuzzy-resolved)  | names[], aliases[], birth_year, nationality, photos[] |
| `Organization`   | registry ID (CUI) or VAT, else UUID | legal_name, trade_names[], country, status, founded, industry (CAEN/NACE) |
| `Domain`         | lowercase FQDN (punycode)        | registrar, created, expires, nameservers[], is_subdomain |
| `URL`            | normalized URL                   | status_code, title, tech_stack[], screenshot_ref       |
| `IPAddress`      | IP string                        | asn, org, geo, open_ports[], reverse_dns               |
| `ASN`            | AS number                        | name, country, prefixes[]                              |
| `Email`          | lowercase address                | deliverable, provider, breach_count, gravatar          |
| `Username`       | `platform:handle` or bare handle | platforms_found[]                                      |
| `Phone`          | E.164                            | carrier, line_type, country                            |
| `SocialProfile`  | `platform:id`                    | url, display_name, bio, followers, created, avatar     |
| `Certificate`    | SHA-256 fingerprint              | issuer, subject, SANs[], not_before, not_after         |
| `Location`       | normalized address / geohash     | lat, lon, country, city                                |
| `Document`       | content hash                     | url, mime, metadata (author, software, created)        |
| `Breach`         | breach name                      | date, data_classes[], source                           |
| `Repository`     | `host:owner/name`                | url, language, contributors[], leaked_secrets_flag     |
| `Vulnerability`  | CVE ID                           | cvss, affected_product                                 |

## Relationship types

| Edge                 | From → To                          |
|----------------------|------------------------------------|
| `subdomain_of`       | Domain → Domain                    |
| `resolves_to`        | Domain → IPAddress                 |
| `hosted_in`          | IPAddress → ASN                    |
| `registered_by`      | Domain → Person / Organization     |
| `uses_email`         | Person / Organization → Email      |
| `has_profile`        | Person / Organization → SocialProfile |
| `uses_username`      | Person → Username                  |
| `works_at` / `role`  | Person → Organization (role, since, until) |
| `owns` / `shareholder_of` | Person / Organization → Organization (pct) |
| `located_at`         | any → Location                     |
| `appears_in_breach`  | Email / Username / Phone → Breach  |
| `mentioned_in`       | any → URL / Document               |
| `secured_by`         | Domain → Certificate               |
| `exposes`            | IPAddress / URL → Vulnerability    |
| `same_as`            | any → any (resolver-proposed, analyst-confirmable) |

Every edge carries `first_seen`, `last_seen`, `sources[]`, `confidence`.

## Provenance

Nothing is stored without its origin.

```json
{
  "finding_id": "f_01HF...",
  "connector": "crtsh",
  "query": {"entity": "Domain:example.com"},
  "fetched_at": "2026-10-30T11:02:11Z",
  "raw_ref": "s3://evidence/inv_42/f_01HF.json",
  "sha256": "9b1c...",
  "extracted": ["Domain:dev.example.com", "Certificate:ab12..."]
}
```

An entity attribute looks like:

```json
{
  "key": "registrar",
  "value": "Example Registrar Inc.",
  "sources": ["f_01HF...", "f_01HG..."],
  "confidence": 0.92,
  "first_seen": "2019-03-01",
  "last_seen": "2026-10-30"
}
```

Conflicting values are **all kept**, each with its own sources and confidence.
The UI shows the strongest and flags the conflict.

## Confidence

```
confidence = 1 - Π (1 - reliability(source_i) * match_quality_i)
```

- `reliability(source)`: static per connector (official registry 0.95,
  DNS 0.95, CT logs 0.9, commercial aggregators 0.7, scraped social 0.6,
  username-match heuristics 0.4).
- `match_quality`: 1.0 for exact deterministic keys; lower for fuzzy merges
  (e.g. name-only person match ≈ 0.3).
- Multiple independent sources corroborating the same fact push confidence up.
- Analyst confirmation sets confidence to 1.0; rejection hides the fact and
  feeds back into the resolver.

## Entity resolution rules (MVP)

1. **Deterministic merge**: same type + same canonical key.
2. **Strong link**: Person ↔ Email when the email appears on a profile that
   also contains the person's name.
3. **Fuzzy candidate**: Persons with name similarity > 0.9 **and** at least one
   shared strong attribute (email, phone, employer, location). These are
   proposed as `same_as` edges and are **never auto-merged**. The analyst
   decides.

## Investigation-level objects

- `Investigation { id, name, owner, purpose, legal_basis, created_at, settings }`
- `Target { id, investigation_id, entity_ref, type, added_by }`
- `Job { id, connector, entity_ref, depth, status, started_at, finished_at, error }`
- `Note { id, entity_ref, author, body, created_at }`
- `Material { id, investigation_id, kind (file|text|link), name, format, size_bytes, sha256,
  chars, file, finding_id, note, digest, added_by, created_at }`: analyst-supplied
  material; its markdown lives in the finding's payload (`analyst_material`, reliability 0.7,
  or `web_read` for a link), and its identifiers hang off a `Document` entity.
- `AuditEvent { id, user, action, entity_ref, connector, timestamp, ip }`
