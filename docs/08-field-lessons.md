# 8. Lessons from field casework

Before the hackathon, part of the team ran real, authorized OSINT work with
Claude Code driving a Kali VM through a hand-built MCP server: due-diligence
background reports on people and companies, and domain footprinting. Every one
of those reports went through several revisions, and most of the extra work
came from a small set of repeated problems. This page lists them and where the
platform handles each one, so the design stays grounded in what goes wrong in
practice.

## What kept going wrong → what the platform does

| # | Problem in casework | Platform answer | Where |
|---|---|---|---|
| 1 | Collection details (which tool ran, when, from which egress, how many paid credits) were kept in hand-written notes and were incomplete. | **Ledger.** Every connector call goes through one path that writes a `findings` row (input, timing, who/what ran it, sha256), stores the raw payload as a file, and appends to the audit log, including refused calls. | `app/ledger.py` |
| 2 | The scope gate allowed any host listed in **any** engagement's scope file, so case A's authorization could cover case B. | **Per-investigation scope.** Active connectors need `mode=active`, a recorded authorization, and the host in *this* investigation's scope (host, parent domain, or CIDR). | `app/guard.py` |
| 3 | Recon drifted onto other people's infrastructure (DNS providers, mail hosts, CDN). | **Off-target pivots are recorded, not followed.** NS/MX/SOA results that sit outside the target's domains are kept in the graph and listed in the report as related third-party assets. Scope creep is a finding, not an action. | `app/scan` (`on_target`), report section "Related third-party assets" |
| 4 | Name-only hits on wanted/sanctions lists were close to being attached to the subject. | **Candidates, never merges.** Each name-only legal hit is a separate `candidate` entity (keyed by name + official listing), linked only through a low-confidence `possible_match` edge. An analyst confirms it with a disambiguator (DOB, nationality, ID); the confirmation is audited. | `app/resolver` |
| 5 | Fact-checking a finished report meant re-reading every source by hand; inline citations were added and later stripped with a regex. | **Claim-level report model.** Each claim lists the finding ids behind it. Citations and evidence numbering are generated at render time, so they can be turned on or off without editing the text. | `app/report` |
| 6 | Reports went out with unsourced statements, a "wanted" claim based on a name match, or text that looked like a credential. | **Report lint.** Blocks export on uncited claims, unknown/failed evidence, legal hits stated as fact, and credential-looking text; warns on GDPR Art. 9 data that isn't flagged. A forced export is audited. | `app/report.lint` |
| 7 | The same report had to be hand-cleaned for a manager: remove egress relays, internal paths, tool names. | **Audience-aware render.** `internal` shows finding ids, hashes, raw paths and the collection method; `client` shows only source + retrieval date. Lint warns about workspace details in client text. | `app/report.render` |
| 8 | "Delete after review" files and old case data stayed on disk indefinitely. | **Retention.** Each investigation expires after `retention_days` unless on legal hold; purge removes rows and evidence files and keeps the audit trail. | `app/retention.py` |
| 9 | People who came up during collection (relatives, a registrant, a GitHub display name) were screened as thoroughly as the actual subject. | **Only seeds are screened.** The quick scan never runs web searches or legal checks on a person it discovered. An analyst can pivot on that person explicitly, and the pivot is audited. | `app/scan` |
| 10 | Bot walls and rate limits silently produced empty results. | Connector failures become findings with an `error`, not silent gaps; the report's method section lists failed calls per connector. | `app/ledger.py`, `app/report` |

## The collection path

```mermaid
flowchart LR
  seed(["Target<br/>auto-detected type"]) --> scan["Quick scan<br/>pivot map · loop detection<br/>call & pivot caps"]
  analyst(["Analyst pivot"]) --> ledger
  scan --> ledger

  subgraph ledger["Ledger.run — every connector call"]
    direction TB
    gate{"Gate<br/>allow/deny list<br/>active → mode + authorization<br/>+ this investigation's scope"}
    call["Connector"]
    rec[("finding row<br/>raw JSON file + sha256")]
    res["Resolver<br/>dedupe · provenance · confidence<br/>legal hits → candidates"]
    gate -->|allowed| call --> rec --> res
  end

  gate -->|refused| audit[("Audit log<br/>append-only")]
  rec --> audit
  res --> graph[("Graph<br/>entities · edges · sources")]
  res -. "SSE events" .-> ui(["UI"])
  graph --> draft["Report draft<br/>(or report agent)"]
  draft --> lint{"Lint"}
  lint -->|clean| render["Render<br/>internal · client"]
  lint -->|errors| fix(["Analyst fixes claims"])
```

## Still to carry over

- **Evidence capture** with a source banner, plus a fallback chain for bot walls
  (headless capture, then a saved-page render, then a manual capture task). The
  banner content should depend on the audience, like the report.
- **Egress per investigation**: record which country/relay the collection
  went out from, and pick it to match the target's country, so local search
  results are what a local would see.
- **Paid-credit budget** per investigation (`budget_credits` in the API draft),
  charged by the ledger.
- **Standard workstreams** (corporate registry, adverse media / sanctions,
  public footprint, breach exposure) as worker agents with fixed output shapes;
  these are the deep-analysis workers in [Architecture](02-architecture.md).
