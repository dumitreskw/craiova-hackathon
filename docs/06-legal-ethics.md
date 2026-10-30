# 6. Legal, Ethics & Safety

"Open source" means publicly available, not free to use however you like.
Collecting and correlating data about people is processing personal data under
the **GDPR** (and Romanian Law 190/2018), even when every piece is public.
This section defines the guardrails built into the platform.

## Principles

1. **Purpose first**: every investigation records a purpose and a legal basis
   (e.g. legitimate interest for security assessment, contractual for a
   pentest engagement, legal obligation for KYC/AML).
2. **Passive by default**: active connectors (anything that sends traffic to
   the target's infrastructure) are off unless the investigation is marked
   `active` and the user confirms written authorization for that scope.
3. **Data minimization**: collect what serves the purpose. Breach connectors
   return metadata (which breach, which data classes) and **never plaintext
   passwords**.
4. **Retention**: evidence auto-expires (default 90 days) unless the
   investigation is put on legal hold.
5. **Accountability**: immutable audit log of who queried what, when, and why.
6. **Respect sources**: honor ToS, robots.txt where applicable, and rate limits;
   use official APIs over scraping where available.

## Built-in controls

| Control                          | Implementation                                           |
|----------------------------------|----------------------------------------------------------|
| Purpose & legal basis            | Required fields on investigation creation               |
| Authorization attestation        | Checkbox + uploaded scope document for `active` mode    |
| RBAC                             | Viewers can't run connectors; only admins manage keys   |
| Sensitive-category filter        | Flag/hide health, religion, political, sexual orientation data (GDPR Art. 9) |
| Minors                           | Person entities with indicators of being under 18 are blocked from further pivoting |
| Breach data redaction            | Passwords/hashes never stored or displayed               |
| Audit log                        | Append-only, exportable                                  |
| Retention & purge                | Scheduled purge job; per-investigation delete            |
| Rate limiting                    | Per-connector token bucket                               |

## Acceptable use

Allowed: security assessments of your own or authorized organizations, threat
intelligence on malicious infrastructure, due diligence on companies,
journalism and research within legal limits.

Not allowed: stalking, harassment, doxxing, targeting private individuals
without a legitimate purpose, bypassing access controls, credential stuffing
with breach data.

## Data subject rights

Provide a process to handle access and erasure requests for personal data
held in investigations, and document it in the platform's privacy notice.
