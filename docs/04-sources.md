# 4. Sources & Connectors

Legend: **P** = passive (never touches target infrastructure), **A** = active
(sends traffic to the target), 🔑 = API key required, 💲 = paid tier needed for
useful volume.

## Websites / Infrastructure

| Connector          | Source                                  | Input → Output                                  | Mode |
|--------------------|-----------------------------------------|-------------------------------------------------|------|
| `dns`              | Public resolvers (A, AAAA, MX, NS, TXT, SOA, CAA) | Domain → IP, Domain, Email (SOA/DMARC rua) | P |
| `whois` / `rdap`   | RDAP / WHOIS servers (incl. ROTLD for `.ro`) | Domain → Person/Org, Email, dates          | P |
| `crtsh`            | crt.sh certificate transparency         | Domain → subdomains, Certificate                | P |
| `passive_dns`      | SecurityTrails, VirusTotal, Validin     | Domain ↔ IP history                             | P 🔑 |
| `shodan` / `censys`| Internet-wide scan data                 | IP/Domain → ports, banners, tech, CVEs          | P 🔑💲 |
| `ipinfo` / `asn`   | ipinfo.io, RIPE, BGP data               | IP → ASN, geo, org                              | P 🔑 |
| `wayback`          | Internet Archive CDX API                | Domain → historical URLs, old snapshots         | P |
| `urlscan`          | urlscan.io                              | Domain/URL → screenshots, outgoing links        | P 🔑 |
| `virustotal`       | VirusTotal                              | Domain/IP/URL → reputation, related samples     | P 🔑 |
| `http_probe`       | Direct fetch                            | Domain → title, tech stack, emails, socials, robots.txt, security.txt | **A** |
| `tech_fingerprint` | Wappalyzer rules on fetched page        | URL → technologies + versions                   | **A** |
| `subfinder`        | Aggregator of passive subdomain sources | Domain → subdomains                             | P |
| `doc_metadata`     | Fetch public PDFs/DOCX, read EXIF/XMP   | Domain → Document → author names, software      | **A** |
| `screenshot`       | Headless Chromium (Playwright)          | URL → Document (rendered PNG evidence)          | **A** |

## Companies

| Connector          | Source                                  | Input → Output                                  | Mode |
|--------------------|-----------------------------------------|-------------------------------------------------|------|
| `onrc` / `listafirme` | Romanian Trade Registry, aggregators (termene.ro, risco.ro, listafirme.ro) | CUI/name → admins, shareholders, CAEN, address, financials | P |
| `anaf`             | ANAF public API (VAT status, fiscal info) | CUI → VAT, address, status                    | P |
| `vies`             | EU VIES                                 | VAT → validity, name, address                   | P |
| `opencorporates`   | OpenCorporates                          | Name → registrations worldwide, officers        | P 🔑 |
| `gleif`            | GLEIF LEI registry                      | Name/LEI → legal entity, parent/child           | P |
| `sec_edgar` / `companies_house` | US SEC / UK Companies House | Name → filings, officers                       | P 🔑 |
| `sanctions`        | OpenSanctions, EU/UN/OFAC lists         | Person/Org → sanctions & PEP hits               | P |
| `seap`             | Romanian public procurement (e-licitatie) | Org → public contracts                        | P |
| `news`             | GDELT, Google News RSS                  | Org/Person → articles, mentions                 | P |
| `jobs`             | LinkedIn/eJobs job posts (public)       | Org → tech stack, team structure                | P |

## People

| Connector          | Source                                  | Input → Output                                  | Mode |
|--------------------|-----------------------------------------|-------------------------------------------------|------|
| `username_enum`    | Sherlock / Maigret / WhatsMyName lists  | Username → SocialProfile on 400+ sites          | P* |
| `email_rep`        | Holehe-style checks, EmailRep           | Email → registered services                     | P* |
| `gravatar`         | Gravatar                                | Email → avatar, profile, linked accounts        | P |
| `hibp`             | Have I Been Pwned                       | Email/Phone → Breach                            | P 🔑 |
| `dehashed` / `intelx` | Breach & leak indexes                | Email/Username/Domain → breach records (redacted) | P 🔑💲 |
| `github`           | GitHub API (users, commits, gists)      | Username/Email → profile, emails in commits, repos | P 🔑 |
| `social_public`    | Public profile pages (X, Instagram, Facebook, TikTok, Reddit, Mastodon) | Profile → bio, links, location hints | P |
| `linkedin_public`  | Public search results only              | Name + Org → role, profile URL                  | P |
| `phone_lookup`     | libphonenumber, NumVerify               | Phone → carrier, country, line type             | P 🔑 |
| `reverse_image`    | Yandex / Bing / TinEye                  | Photo → other appearances                       | P |
| `dork_search`      | Google, Bing, DuckDuckGo, Yandex with dork templates (merged, de-duped) | any → URL, Document mentions (articles, profiles, public sites) | P |
| `pastebins`        | Paste-site search                       | Email/Domain → mentions                         | P |

\* Account-existence checks hit the third-party service (not the target), but
can trigger notifications on some platforms. Flag them in the UI.

## Legal & law enforcement

Public wanted lists, sanctions, and court/enforcement records. All passive —
official APIs or public pages.

| Connector          | Source                                  | Input → Output                                  | Mode |
|--------------------|-----------------------------------------|-------------------------------------------------|------|
| `interpol`         | INTERPOL Notices public API (Red/Yellow/UN notices) | Name (+DOB/nationality) → notice, charges, country | P |
| `europol`          | EU Most Wanted (eumostwanted.eu)        | Name → listing, offence, member state           | P |
| `fbi_wanted`       | FBI Wanted public API                   | Name → listing, subject details                 | P |
| `sanctions`        | OpenSanctions (consolidates EU/UN/OFAC/UK + PEP + some wanted) | Person/Org → sanctions, PEP, watchlist hits | P |
| `natl_wanted`      | National police / fugitive lists (e.g. RO Poliția Română "urmăriți") | Name → listing | P |
| `court_records`    | Public court portals where available (e.g. RO portal.just.ro) | Name/Org → case references | P |

**Name-collision warning.** These sources match mostly on name, so hits have a
high false-positive rate — many people share a name with a wanted or
sanctioned person. The resolver stores every hit as a **low-confidence
candidate** that is *not* attached to the person entity until a disambiguator
(date of birth, nationality, photo, passport/ID) corroborates it. The UI labels
it "possible match — unverified," and nothing in a report asserts that someone
is wanted or sanctioned without a corroborated match and a screenshot of the
official listing. This is to avoid falsely branding an innocent namesake.

## Code & leaks

| Connector          | Source                                  | Input → Output                                  | Mode |
|--------------------|-----------------------------------------|-------------------------------------------------|------|
| `github_code`      | GitHub code search                      | Domain/Org → repos, leaked config/keys mentions | P 🔑 |
| `gitlab` / `bitbucket` | Public projects                     | Org → repos                                     | P |
| `postman_public`   | Public Postman workspaces               | Domain → exposed API collections                | P |
| `bucket_finder`    | Naming permutations for S3/GCS/Azure    | Org/Domain → open buckets                       | **A** |
| `docker_hub` / `npm` / `pypi` | Package registries           | Org/Username → published packages               | P |

## Implemented now (beyond the original list)

Brought over from the Kali field setup (same tools, same API keys), plus the
search and page-reading stack. Cost: **free**, **metered** (monthly quota, the
ledger refuses calls past it), **paid** (credits per call). *Seed-only*
connectors run on the investigation's own targets only, never on entities a
scan stumbles on.

| Connector | Source | Input → Output | Cost | Notes |
|---|---|---|---|---|
| `subfinder` | ~40 passive sources (CT, passive DNS, search APIs) with our keys | Domain → subdomains | free | 30-60 s; widest subdomain coverage |
| `urlscan` | urlscan.io scan index | Domain → historical pages, IPs, ASNs, servers | free (key) | <1 s |
| `otx_domain` / `otx_ip` | AlienVault OTX passive DNS | Domain ↔ IP history | free (key) | finds subdomains CT logs miss |
| `dnstwist` | permutation engine + public DNS | Domain → registered look-alikes (+MX = phishing risk) | free | seed-only, ~25 s |
| `hunter` / `tomba` | Hunter.io / Tomba.io indexes | Domain → emails, names, roles, address pattern | metered (50 / 25 a month) | seed-only; their "organisation" field is unreliable, kept as a label only |
| `dehashed` | DeHashed breach index | Email/username/domain → breach names + data classes | paid | seed-only; values (passwords, hashes, other PII) dropped before storage |
| `holehe` | services' signup/recovery endpoints | Email → services it is registered on | free | seed-only |
| `maigret` | top ~300 sites | Username → profiles | free | seed-only, ~30 s; search pages filtered out |
| `anaf` | Romanian tax registry (ANAF API v9) | CUI → legal name, J number, CAEN, incorporation, addresses, VAT, inactive / struck off | free, no key | 0.3 s |
| `vies` | EU VIES | VAT number → valid, registered name and address | free, no key | 0.8 s |
| `gleif` | Global LEI index | name / LEI → LEI, local registration number (linked to the ANAF / Companies House node), addresses, ultimate parent | free, no key | 0.4 s |
| `companies_house` | UK Companies House | company number / exact name → profile, officers, persons with significant control | free key | officers are recorded, never auto-screened |
| `wayback` | Internet Archive CDX | Domain → age, old subdomains, archived files and admin paths | free, no key | ~15 s |
| `hibp` | Have I Been Pwned | Email → breaches, dates, data classes | ~US$4.39/month (Core 1) | seed-only; no passwords |
| `dork_search` | **SearXNG** (self-hosted metasearch), DuckDuckGo fallback | query → URLs | free | 40 merged results in ~2 s |
| `web_read` | **Firecrawl** → plain HTTP → **Lightpanda** | URL → main text, author/date, emails, social profiles, JSON-LD/OpenGraph, company identifiers (RO CUI, UK company number) that lead into the registries | free | ~0.5-2 s with Lightpanda; target-owned hosts follow active rules |
| `screenshot` | headless Chromium | URL → banner-stamped PNG evidence | free | target-owned hosts follow active rules |

### Why SearXNG, Lightpanda and Firecrawl

- **SearXNG — search that doesn't fall over.** The original `dork_search`
  scraped DuckDuckGo's lite page, which throttles a server IP within a few
  queries. SearXNG runs inside the stack and fans one query out to Brave,
  Bing, Google, Startpage, Mojeek and others, ranks by agreement, and keeps
  working when individual engines block us. It needs no API keys and keeps
  queries off third-party SaaS. Measured: 40 relevant results in ~2 s where the
  DuckDuckGo scraper returned errors.
- **Lightpanda — reading pages fast.** Search snippets are not evidence; the
  agents now read the pages behind them (`read_page`). Lightpanda is a
  headless browser built for machines: it runs the page's JavaScript, starts
  in milliseconds and emits markdown directly. Measured in the stack: a
  Wikipedia article in 495 ms, 27k characters, against 9k from plain HTTP
  (no JS). It has no graphical renderer, so screenshots stay on Chromium.
- **Firecrawl — the premium reader, optional.** Best markdown and anti-bot
  handling on its hosted API; also self-hostable. It is heavy to self-host
  (API, workers, Playwright, Redis, RabbitMQ, Postgres), so it is not in the
  default stack: set `FIRECRAWL_API_KEY` (hosted) or `FIRECRAWL_API_URL`
  (your own instance) and `web_read` uses it first, falling back to
  Lightpanda and then HTTP. Next step: its `map` endpoint for site discovery
  under active mode.

### Page reading pipeline (after benchmarking, Oct 2026)

`web_read` now reads **HTTP first** and escalates to the Lightpanda browser
only when a page needs JavaScript (thin text, a "please enable JavaScript"
shell) or HTTP is refused. On server-rendered pages (blogs, Wikipedia,
registries) plain HTTP gave the same text and metadata as the browser 2-10x
faster: 0.2-0.6 s vs 0.9-2.2 s.

Every page then goes through the same extraction:

- **selectolax** (lexbor) for links, contacts, JSON-LD and meta tags:
  15-35x faster than BeautifulSoup on six real pages (1-2 ms vs 14-75 ms),
  identical results; parsing runs in a worker thread, off the event loop.
- **trafilatura** for the main text without menus and footers, and the
  page's own author / publish date / site name (`page_metadata`), so report
  citations can say who published what and when. On the PSF blog post the
  agent now reads 3.8k characters of article instead of 30k of page.
  Contacts are still extracted from the whole page (they live in footers).
  Page dates are the page's claim (creation, update or publication), not a
  fact about the subject.

The shared HTTP client uses **HTTP/2** (20 parallel requests to one host:
88-96 ms vs 169-310 ms on HTTP/1.1) and a User-Agent with a contact URL;
Wikimedia refuses anonymous bots and returned 403 before.

| Library (from "7 Python scraping libraries", 2026) | Decision | Why |
|---|---|---|
| selectolax | **Adopted** | measured 15-35x faster parsing, same output |
| trafilatura | **Adopted** | main text + author/date for citations, fewer agent tokens |
| httpx (HTTP/2) | **Adopted** | already our client; HTTP/2 measured 2-3x faster in parallel |
| curl_cffi | Not adopted | impersonates Chrome's TLS fingerprint to get past bot checks; the platform identifies itself honestly (an honest contactable User-Agent fixed the one 403 we hit) |
| botasaurus | Not adopted | anti-detection browser; same reason, and heavy |
| Crawlee | Later | a crawl framework fits active, in-scope site discovery (with katana on Kali as the alternative), not passive collection |
| price-parser | Not relevant | prices are not an OSINT signal here |

## Pivot map (which entity triggers which connectors)

```text
Domain        → dns, whois, crtsh, passive_dns, wayback, urlscan, virustotal,
                shodan, subfinder, github_code, dork_search, [http_probe]
IPAddress     → ipinfo, shodan, passive_dns (reverse), virustotal
Organization  → onrc, anaf, vies, opencorporates, gleif, sanctions, seap,
                news, jobs, github_code, dork_search
Person        → sanctions, interpol, europol, fbi_wanted, natl_wanted,
                court_records, news, linkedin_public, dork_search, onrc (as admin)
Email         → hibp, gravatar, email_rep, github, pastebins, dork_search
Username      → username_enum, github, social_public
Phone         → phone_lookup, hibp, dork_search
SocialProfile → social_public, reverse_image (avatar)
Document      → doc_metadata
```

## Connector priorities for the hackathon

**Tier 1 (free, no key, high value)**: `dns`, `rdap`, `crtsh`, `wayback`,
`http_probe`, `gravatar`, `username_enum`, `anaf`, `vies`, `opensanctions`,
`github` (unauthenticated).

**Tier 2 (free key)**: `hibp`, `urlscan`, `virustotal`, `ipinfo`, `shodan`
(limited), `github` (authenticated).

**Tier 3 (stretch)**: `onrc` scraping, `reverse_image`, `doc_metadata`,
`dehashed`, LLM extraction from arbitrary pages.
