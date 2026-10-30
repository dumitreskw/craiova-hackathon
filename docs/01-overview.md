# 1. Overview

## Problem

Open-source intelligence (OSINT) about a target is scattered across dozens of
sources: DNS records, certificate logs, company registries, social networks,
breach databases, code hosting, archived web pages, and more. Analysts spend
most of their time switching between tools, copy-pasting identifiers, and
manually stitching results together.

## Goal

A single platform where an analyst enters a **target** and gets back a
**correlated, source-attributed intelligence graph** built from every relevant
open source, automatically and recursively.

## Target types

| Target      | Typical seed input                         | Example                          |
|-------------|--------------------------------------------|----------------------------------|
| **Person**  | Full name, email, username, phone          | `jane.doe@example.com`           |
| **Company** | Legal name, registration number (CUI), VAT | `Example SRL`, `RO12345678`      |
| **Website** | Domain, URL, IP address                    | `example.com`, `203.0.113.10`    |

A target is just a starting point. Every finding can become a new **pivot**:
a website reveals an email, the email reveals a username, the username reveals
social profiles, and so on.

## Core concepts

- **Investigation**: a workspace containing one or more targets, their
  findings, notes, and history.
- **Entity**: a typed node in the graph (Person, Organization, Domain, IP,
  Email, Username, Phone, SocialProfile, Certificate, Document, Location, ...).
- **Relationship**: a typed edge between entities (`owns`, `resolves_to`,
  `works_at`, `mentioned_in`, `registered_by`, ...).
- **Connector**: a plugin that queries one source. It declares which entity
  types it accepts and which it produces.
- **Finding / Evidence**: the raw result a connector returned, stored
  unchanged, with timestamp and source, so every fact in the graph can be
  traced back to where it came from.
- **Pivot**: running connectors on a newly discovered entity. Depth limits
  stop the expansion from running away.

## Key capabilities

1. **Multi-source collection**: parallel connectors for DNS, WHOIS,
   certificate transparency, company registries, social media, breaches,
   search engines, code repos, web archives.
2. **Normalization and de-duplication**: different sources describing the same
   thing are merged into one entity.
3. **Recursive enrichment**: configurable pivot depth and connector selection.
4. **Graph visualization**: an interactive link chart of entities and
   relationships.
5. **Confidence scoring**: each fact has a confidence based on source
   reliability and corroboration.
6. **Timeline**: when things were first and last seen (domain registration,
   breach dates, posts).
7. **AI agent mode**: a collection agent (Claude) drives the investigation
   through an OSINT MCP server, choosing what to query and pivot on.
8. **AI reporting**: a separate report agent reasons over the graph, removes
   duplicates and noise, and curates what's worth reporting — capturing a
   screenshot of each supporting page as evidence.
9. **Deep analysis (orchestrated)**: an orchestrator fans a request out to
   specialized worker agents (recon, dorking, breach-exposure, legal/
   enforcement), then an analysis agent builds a sourced target profile from
   the results.
10. **Legal & enforcement checks**: sanctions, PEP, INTERPOL/Europol/national
    wanted notices, and court records — reported as unverified possible matches
    until corroborated, to avoid branding innocent namesakes.
11. **Reporting**: export to PDF / JSON.
12. **Auditability**: every query logged with who, when, why, and which source.

## Users

- **SOC / Threat-intel analysts**: attack surface mapping, phishing
  infrastructure, threat actor profiling.
- **Pentesters / red teams** (authorized engagements): external recon of a
  client's footprint.
- **Due-diligence / compliance**: vetting companies and business partners.
- **Journalists / researchers**: verification and investigation.

## Non-goals

- No active exploitation, brute forcing, or intrusive scanning by default. The
  platform is passive-first (see [Legal & Ethics](06-legal-ethics.md)).
- No access to private or authenticated data the analyst is not entitled to.
- No bypassing source terms of service or rate limits.
