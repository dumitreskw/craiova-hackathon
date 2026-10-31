"""Social-media capture through research accounts ("personas") on the Kali VM.

The app runs on the VPS, but the personas' logged-in browser profiles live on
the Kali VM, which the VPS cannot reach. So Kali runs ``kali-worker/worker.py``,
which connects *out* to the app: it long-polls ``/api/v1/worker/jobs/next``,
does the job with Playwright and the persona's saved profile, and posts the
result back. Session cookies never leave Kali; the app only knows each
persona's name, platform, region and health.

A capture is an ordinary finding: it goes through the guard and
:meth:`Ledger.record`, so it gets the same audit row, sha256 and provenance as
any connector call. Watches re-capture a profile on a schedule and diff each
snapshot against the previous one.

Read-only by design: the worker loads, scrolls and screenshots. It never
follows, likes, comments, messages or joins anything.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from connectors.base import (ConnectorResult, EntityCandidate, EntityType,
                             RelationshipCandidate)

from . import guard
from .evidence import screenshot_dir
from .ledger import Ledger, RunOutcome
from .models import new_id, now

CONNECTOR = "social_capture"
SEARCH_CONNECTOR = "social_search"            # in-platform people/content search via a persona
INTERACTIONS_CONNECTOR = "social_interactions"  # co-engagement: who comments on the target's posts

WORKER_STALE_S = 75            # no heartbeat for this long = worker offline
CAPTURE_TIMEOUT_S = 240        # queued + running, for a capture someone waits on
LOGIN_TIMEOUT_S = 20 * 60      # the analyst logs in by hand
QUEUE_TTL_S = 30 * 60          # an unclaimed job expires
PERSONA_HOURLY_CAP = 30        # captures per persona per hour
SCHEDULER_TICK_S = 60
MIN_WATCH_HOURS = 1

PLATFORM_HOSTS = {
    "linkedin.com": "linkedin", "facebook.com": "facebook", "fb.com": "facebook",
    "instagram.com": "instagram", "x.com": "x", "twitter.com": "x",
    "tiktok.com": "tiktok", "youtube.com": "youtube", "threads.net": "threads",
    "threads.com": "threads", "reddit.com": "reddit",
}
PLATFORMS = sorted(set(PLATFORM_HOSTS.values()))

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]{3})?$")        # Mullvad country[-city], e.g. de-fra
_URL = re.compile(r"^https?://[^\s\"'<>]+$")
# Path segments that are never a profile handle.
_NOT_HANDLE = {"in", "company", "pub", "profile.php", "people", "user", "u", "c", "channel",
               "school", "showcase", "pages", "groups", "hashtag", "explore", "p", "reel",
               "status", "watch", "search", "home", "login", "accounts", "i",
               # app/navigation paths that look like a handle but aren't a profile
               "feed", "messaging", "mynetwork", "notifications", "jobs", "settings", "help",
               "legal", "about", "share", "sharing", "learning", "games", "directory",
               "posts", "events", "marketplace", "stories", "reels", "tv", "web"}


def platform_of(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for domain, name in PLATFORM_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def profile_key(url: str) -> str | None:
    """``platform:handle`` for a profile URL (the SocialProfile entity key)."""
    platform = platform_of(url)
    if platform is None:
        return None
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    handle = None
    if platform == "facebook" and parts[:1] == ["profile.php"]:
        handle = (parse_qs(parsed.query).get("id") or [None])[0]
    elif platform == "linkedin" and len(parts) >= 2 and parts[0] in ("in", "company", "school"):
        handle = parts[1] if parts[0] == "in" else f"{parts[0]}/{parts[1]}"
    elif platform == "reddit" and len(parts) >= 2 and parts[0] in ("user", "u"):
        handle = parts[1]
    elif platform == "youtube" and len(parts) >= 2 and parts[0] in ("channel", "c", "user"):
        handle = parts[1]
    elif parts and parts[0] not in _NOT_HANDLE:
        handle = parts[0]
    if not handle:
        return None
    return f"{platform}:{handle.lstrip('@').lower()}"


class SocialError(Exception):
    """A request the hub refuses; the message is user-facing."""


class SocialHub:
    """Persona registry, the job queue the Kali worker drains, and watches."""

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.db = ledger.db
        self.bus = ledger.bus
        self.worker: dict[str, Any] = {}     # last heartbeat body
        self.worker_seen = 0.0               # monotonic time of the last heartbeat/poll
        self._waiters: dict[str, asyncio.Future] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    # --- worker presence --------------------------------------------------------

    def online(self) -> bool:
        return self.worker_seen > 0 and time.monotonic() - self.worker_seen < WORKER_STALE_S

    def heartbeat(self, body: dict[str, Any]) -> None:
        self.worker = body
        self.worker_seen = time.monotonic()
        self.worker["received_at"] = now()
        sessions = body.get("sessions") or {}
        for name, session in sessions.items():
            persona = self.persona(name)
            if persona and session in ("ok", "logged_out", "checkpoint") \
                    and persona["status"] != "disabled":
                status = "ok" if session == "ok" else "needs_login"
                if status != persona["status"]:
                    self.db.update("personas", persona["id"], {"status": status,
                                                                "status_at": now()})

    def status(self) -> dict[str, Any]:
        w = self.worker
        return {
            "online": self.online(),
            "last_seen": w.get("received_at"),
            "version": w.get("version"),
            "vpn": w.get("vpn"),
            "egress": w.get("egress"),
            "busy": w.get("busy"),
            "queued": self.db.one("SELECT count(*) AS n FROM social_jobs "
                                  "WHERE status = 'queued'")["n"],
            "personas": self.personas(),
        }

    # --- personas -----------------------------------------------------------------

    def personas(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM personas ORDER BY platform, name")
        hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
        for r in rows:
            r["captures_last_hour"] = self.db.one(
                "SELECT count(*) AS n FROM social_jobs WHERE persona = ? AND kind = 'capture' "
                "AND created_at >= ?", (r["name"], hour_ago))["n"]
        return rows

    def persona(self, name: str) -> dict[str, Any] | None:
        return self.db.one("SELECT * FROM personas WHERE name = ?", (name,))

    def create_persona(self, name: str, platform: str, region: str, note: str | None,
                       user: str) -> dict[str, Any]:
        name, region = name.strip().lower(), region.strip().lower()
        if not _NAME.match(name):
            raise SocialError("name: 2-40 chars, lowercase letters, digits, '-' or '_'")
        if platform not in PLATFORMS:
            raise SocialError(f"platform must be one of {', '.join(PLATFORMS)}")
        if not _REGION.match(region):
            raise SocialError("region is a Mullvad location: a country code like 'de', or "
                              "country-city like 'de-fra'")
        if self.persona(name):
            raise SocialError(f"persona '{name}' already exists")
        row = {"id": new_id("pp"), "name": name, "platform": platform, "region": region,
               "status": "needs_login", "note": (note or "").strip()[:500] or None,
               "created_by": user, "created_at": now(), "status_at": now(),
               "last_used": None}
        self.db.insert("personas", row)
        self.db.audit(user=user, action="persona.create", target=name,
                      detail={"platform": platform, "region": region})
        return row

    def update_persona(self, name: str, values: dict[str, Any], user: str) -> dict[str, Any]:
        persona = self.persona(name)
        if persona is None:
            raise SocialError(f"no persona '{name}'")
        changes: dict[str, Any] = {}
        if values.get("region") is not None:
            region = values["region"].strip().lower()
            if not _REGION.match(region):
                raise SocialError("region must look like 'de' or 'de-fra'")
            changes["region"] = region
        if values.get("note") is not None:
            changes["note"] = values["note"].strip()[:500] or None
        if values.get("disabled") is not None:
            changes["status"] = "disabled" if values["disabled"] else "needs_login"
            changes["status_at"] = now()
        if changes:
            self.db.update("personas", persona["id"], changes)
            self.db.audit(user=user, action="persona.update", target=name, detail=changes)
        return self.persona(name)

    def _pick_persona(self, platform: str) -> dict[str, Any] | None:
        """The least recently used healthy persona for a platform."""
        return self.db.one(
            "SELECT * FROM personas WHERE platform = ? AND status = 'ok' "
            "ORDER BY coalesce(last_used, '') LIMIT 1", (platform,))

    # --- jobs ---------------------------------------------------------------------

    def enqueue(self, kind: str, *, user: str, run_by: str, persona: str | None = None,
                url: str | None = None, investigation_id: str | None = None,
                entity_id: str | None = None, watch_id: str | None = None,
                mode: str = "auto", query: str | None = None,
                platform: str | None = None) -> dict[str, Any]:
        row = {"id": new_id("sj"), "kind": kind, "status": "queued", "persona": persona,
               "mode": mode, "url": url, "query": query, "platform": platform,
               "investigation_id": investigation_id,
               "entity_id": entity_id, "watch_id": watch_id, "requested_by": user,
               "run_by": run_by, "created_at": now(), "started_at": None,
               "finished_at": None, "error": None, "finding_id": None, "result": {}}
        self.db.insert("social_jobs", row)
        self._wake.set()
        return row

    def login(self, name: str, user: str) -> dict[str, Any]:
        """Queue a manual login: the worker opens a visible browser on Kali's desktop."""
        persona = self.persona(name)
        if persona is None:
            raise SocialError(f"no persona '{name}'")
        if persona["status"] == "disabled":
            raise SocialError(f"persona '{name}' is disabled")
        if not self.online():
            raise SocialError("the Kali worker is offline")
        self.db.audit(user=user, action="persona.login", target=name)
        return self.enqueue("login", user=user, run_by="analyst", persona=name)

    async def next_job(self, wait: float = 25.0) -> dict[str, Any] | None:
        """Claim the oldest queued job, waiting up to ``wait`` seconds for one."""
        self.worker_seen = time.monotonic()
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            self._wake.clear()
            self._expire()
            row = self.db.one("SELECT * FROM social_jobs WHERE status = 'queued' "
                              "ORDER BY created_at, rowid LIMIT 1")
            if row:
                self.db.update("social_jobs", row["id"], {"status": "running",
                                                          "started_at": now()})
                return self._job_for_worker(row)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self._wake.wait(), remaining)
            except TimeoutError:
                return None

    def _job_for_worker(self, row: dict[str, Any]) -> dict[str, Any]:
        persona = self.persona(row["persona"]) if row["persona"] else None
        platform = row.get("platform") or (platform_of(row["url"]) if row["url"] else None) \
            or (persona["platform"] if persona else None)
        return {"id": row["id"], "kind": row["kind"], "url": row["url"], "mode": row["mode"],
                "query": row.get("query"), "platform": platform,
                "persona": {k: persona[k] for k in ("name", "platform", "region")}
                if persona else None}

    def _expire(self) -> None:
        cutoff_q = (datetime.now(UTC) - timedelta(seconds=QUEUE_TTL_S)).isoformat(
            timespec="seconds")
        for row in self.db.query("SELECT id FROM social_jobs WHERE status = 'queued' "
                                 "AND created_at < ?", (cutoff_q,)):
            self._finish(row["id"], "expired", "no worker picked the job up in time")
        for row in self.db.query("SELECT id, kind, started_at FROM social_jobs "
                                 "WHERE status = 'running'"):
            limit = LOGIN_TIMEOUT_S if row["kind"] == "login" else CAPTURE_TIMEOUT_S
            started = datetime.fromisoformat(row["started_at"])
            if datetime.now(UTC) - started > timedelta(seconds=limit + 60):
                self._finish(row["id"], "failed", "the worker did not report back")

    def _finish(self, job_id: str, status: str, error: str | None = None,
                finding_id: str | None = None, result: dict | None = None) -> None:
        values: dict[str, Any] = {"status": status, "finished_at": now(), "error": error,
                                  "finding_id": finding_id}
        if result is not None:
            values["result"] = result
        self.db.update("social_jobs", job_id, values)
        waiter = self._waiters.pop(job_id, None)
        if waiter and not waiter.done() and status != "done":
            waiter.set_result(RunOutcome(finding_id, None, error=error or status))

    def jobs(self, limit: int = 50, investigation_id: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM social_jobs", []
        if investigation_id:
            sql += " WHERE investigation_id = ?"
            params.append(investigation_id)
        return self.db.query(sql + " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                             [*params, limit])

    # --- capture ------------------------------------------------------------------

    def subject_entity(self, inv_id: str, url: str,
                       entity_id: str | None = None) -> dict[str, Any] | None:
        """The SocialProfile / URL entity of this investigation the URL belongs to.

        Captures are tied to profiles already in the investigation (found by a
        connector, or added by the analyst as a target), never to arbitrary URLs.
        """
        if entity_id:
            row = self.db.get("entities", entity_id)
            if row and row["investigation_id"] == inv_id and row["type"] in (
                    EntityType.SOCIAL_PROFILE.value, EntityType.URL.value):
                return row
            return None
        key = profile_key(url)
        if key:
            row = self.db.one("SELECT * FROM entities WHERE investigation_id = ? AND type = ? "
                              "AND key = ?", (inv_id, EntityType.SOCIAL_PROFILE.value, key))
            if row:
                return row
        wanted = _norm_url(url)
        for row in self.db.query(
                "SELECT * FROM entities WHERE investigation_id = ? AND type IN (?, ?)",
                (inv_id, EntityType.SOCIAL_PROFILE.value, EntityType.URL.value)):
            urls = [row["key"]] + [v["value"] for v in row["attributes"].get("url", [])
                                   if isinstance(v.get("value"), str)]
            if any(_norm_url(u) == wanted for u in urls):
                return row
        return None

    def _prepare(self, investigation: dict, url: str, persona: str | None,
                 entity_id: str | None, user: str, run_by: str) -> tuple[str, str | None, str]:
        """Validate a capture request; returns ``(entity_id, persona, mode)``."""
        if not _URL.match(url or ""):
            raise SocialError("not an http(s) URL")
        platform = platform_of(url)
        if platform is None:
            raise SocialError(f"not a supported social platform ({', '.join(PLATFORMS)})")
        try:
            guard.check(investigation, CONNECTOR, True, url)
        except guard.GateError as e:
            raise SocialError(str(e)) from e
        subject = self.subject_entity(investigation["id"], url, entity_id)
        if subject is None:
            raise SocialError("this profile is not in the investigation's graph; add it as a "
                              "target (or find it with a lookup) first")
        mode = "auto"
        if persona == "none":
            persona, mode = None, "anonymous"
        elif persona:
            row = self.persona(persona)
            if row is None:
                raise SocialError(f"no persona '{persona}'")
            if row["platform"] != platform:
                raise SocialError(f"persona '{persona}' is for {row['platform']}, "
                                  f"not {platform}")
            if row["status"] != "ok":
                raise SocialError(f"persona '{persona}' is {row['status']}; log it in first")
            mode = "persona"
        else:
            row = self._pick_persona(platform)
            persona = row["name"] if row else None
        if persona:
            hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
            used = self.db.one("SELECT count(*) AS n FROM social_jobs WHERE persona = ? "
                               "AND kind = 'capture' AND created_at >= ?",
                               (persona, hour_ago))["n"]
            if used >= PERSONA_HOURLY_CAP:
                raise SocialError(f"persona '{persona}' hit its hourly cap "
                                  f"({PERSONA_HOURLY_CAP}); try again later")
        if not self.online():
            raise SocialError("the Kali worker is offline (start osint-worker on the Kali VM)")
        return subject["id"], persona, mode

    async def capture(self, investigation: dict, url: str, *, user: str, run_by: str,
                      persona: str | None = None, entity_id: str | None = None,
                      watch_id: str | None = None, wait: bool = True) -> RunOutcome:
        """Capture a profile through the Kali worker.

        ``persona``: a persona name, ``"none"`` for logged-out only, or None to
        try logged out first and fall back to a healthy persona at a login wall.
        """
        inv_id = investigation["id"]
        try:
            subject_id, persona, mode = self._prepare(investigation, url, persona, entity_id,
                                                      user, run_by)
        except SocialError as e:
            self.db.audit(user=user, action=f"{CONNECTOR}.request", outcome="refused",
                          investigation_id=inv_id, connector=CONNECTOR, target=url,
                          detail={"reason": str(e), "run_by": run_by, "persona": persona})
            self.bus.publish(inv_id, "tool.refused", {"connector": CONNECTOR,
                                                      "input": {"arg": url},
                                                      "reason": str(e)})
            return RunOutcome(None, None, error=str(e), refused=True)
        job = self.enqueue("capture", user=user, run_by=run_by, persona=persona, url=url,
                           investigation_id=inv_id, entity_id=subject_id, watch_id=watch_id,
                           mode=mode)
        self.bus.publish(inv_id, "tool.started", {"connector": CONNECTOR,
                                                  "input": {"arg": url}, "by": run_by,
                                                  "job_id": job["id"]})
        if not wait:
            return RunOutcome(None, None)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[job["id"]] = future
        try:
            return await asyncio.wait_for(asyncio.shield(future), CAPTURE_TIMEOUT_S)
        except TimeoutError:
            self._waiters.pop(job["id"], None)
            if self.db.get("social_jobs", job["id"])["status"] in ("queued", "running"):
                self.db.update("social_jobs", job["id"], {"status": "expired",
                                                          "finished_at": now(),
                                                          "error": "timed out"})
            return RunOutcome(None, None, error="the Kali worker did not finish in time")

    async def wait(self, job_id: str, timeout: float = CAPTURE_TIMEOUT_S) -> RunOutcome:
        """Wait for a queued job (search, interactions) to finish; for agents."""
        row = self.db.get("social_jobs", job_id)
        if row is None:
            return RunOutcome(None, None, error="no such job")
        if row["status"] not in ("queued", "running"):
            failed = row["status"] != "done"
            return RunOutcome(row["finding_id"], None,
                              error=(row["error"] or row["status"]) if failed else None)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[job_id] = future
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except TimeoutError:
            self._waiters.pop(job_id, None)
            return RunOutcome(None, None, error="the Kali worker did not finish in time")

    def platforms_ready(self) -> list[str]:
        """Platforms with a logged-in persona, when the worker is online."""
        if not self.online():
            return []
        return sorted({r["platform"] for r in self.personas() if r["status"] == "ok"})

    # --- search & interaction mapping --------------------------------------------------

    def search(self, investigation: dict, platform: str, query: str, *, user: str,
               run_by: str, persona: str | None = None,
               entity_id: str | None = None) -> dict[str, Any]:
        """Queue an in-platform people/content search through a persona (logged in).

        Results come back as *candidate* SocialProfile leads for the analyst to
        confirm — the platform's own search sees what public web search can't.
        """
        if platform not in PLATFORMS:
            raise SocialError(f"platform must be one of {', '.join(PLATFORMS)}")
        query = (query or "").strip()
        if len(query) < 2:
            raise SocialError("query is too short")
        if persona and persona != "none":
            row = self.persona(persona)
            if row is None:
                raise SocialError(f"no persona '{persona}'")
            if row["platform"] != platform:
                raise SocialError(f"persona '{persona}' is for {row['platform']}, "
                                  f"not {platform}")
            if row["status"] != "ok":
                raise SocialError(f"persona '{persona}' is {row['status']}; log it in first")
        elif persona is None:
            row = self._pick_persona(platform)
            persona = row["name"] if row else None
        else:  # "none"
            persona = None
        if not persona:
            raise SocialError(f"in-platform search needs a logged-in {platform} persona")
        if not self.online():
            raise SocialError("the Kali worker is offline")
        job = self.enqueue("search", user=user, run_by=run_by, persona=persona,
                           query=query, platform=platform,
                           investigation_id=investigation["id"], entity_id=entity_id,
                           mode="persona")
        self.db.audit(user=user, action=f"{SEARCH_CONNECTOR}.request",
                      investigation_id=investigation["id"], connector=SEARCH_CONNECTOR,
                      target=query, detail={"platform": platform, "persona": persona,
                                            "run_by": run_by, "job_id": job["id"]})
        self.bus.publish(investigation["id"], "tool.started",
                         {"connector": SEARCH_CONNECTOR, "input": {"arg": query},
                          "by": run_by, "job_id": job["id"]})
        return job

    async def map_interactions(self, investigation: dict, url: str, *, user: str,
                               run_by: str, persona: str | None = None,
                               entity_id: str | None = None) -> dict[str, Any]:
        """Queue a co-engagement map: who comments on the target profile's recent posts."""
        subject_id, persona, mode = self._prepare(investigation, url, persona, entity_id,
                                                  user, run_by)
        job = self.enqueue("interactions", user=user, run_by=run_by, persona=persona,
                           url=url, platform=platform_of(url),
                           investigation_id=investigation["id"], entity_id=subject_id,
                           mode=mode)
        self.db.audit(user=user, action=f"{INTERACTIONS_CONNECTOR}.request",
                      investigation_id=investigation["id"], connector=INTERACTIONS_CONNECTOR,
                      target=url, detail={"persona": persona, "run_by": run_by,
                                          "job_id": job["id"]})
        self.bus.publish(investigation["id"], "tool.started",
                         {"connector": INTERACTIONS_CONNECTOR, "input": {"arg": url},
                          "by": run_by, "job_id": job["id"]})
        return job

    # --- results from the worker -------------------------------------------------------

    def complete(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        job = self.db.get("social_jobs", job_id)
        if job is None:
            raise SocialError("no such job")
        if job["status"] != "running":
            raise SocialError(f"job is {job['status']}, not running")
        self.worker_seen = time.monotonic()
        if job["kind"] in ("login", "check"):
            return self._complete_session(job, body)
        if job["kind"] == "search":
            return self._complete_search(job, body)
        if job["kind"] == "interactions":
            return self._complete_interactions(job, body)
        return self._complete_capture(job, body)

    def _complete_session(self, job: dict, body: dict) -> dict:
        session = body.get("session")
        persona = self.persona(job["persona"]) if job["persona"] else None
        if persona and session in ("ok", "logged_out", "checkpoint"):
            self.db.update("personas", persona["id"], {
                "status": "ok" if session == "ok" else "needs_login", "status_at": now()})
        ok = bool(body.get("ok")) and session == "ok"
        self._finish(job["id"], "done" if ok else "failed",
                     None if ok else (body.get("error") or f"session: {session}"),
                     result={"session": session})
        self.db.audit(user=job["requested_by"], action=f"persona.{job['kind']}",
                      outcome="ok" if ok else "error", target=job["persona"],
                      detail={"session": session, "error": body.get("error")})
        return {"ok": ok}

    def _complete_capture(self, job: dict, body: dict) -> dict:
        investigation = self.db.get("investigations", job["investigation_id"])
        if investigation is None:
            self._finish(job["id"], "failed", "investigation is gone")
            return {"ok": False}
        call = {"arg": job["url"], "persona": body.get("persona_used"), "mode": job["mode"]}
        duration_ms = int(body.get("duration_ms") or 0)
        result = error = None
        if body.get("ok"):
            try:
                result = self._result(investigation, job, body)
            except (ValueError, KeyError, TypeError) as e:
                error = f"bad worker result: {e}"[:500]
        else:
            error = str(body.get("error") or "capture failed")[:500]
        outcome = self.ledger.record(investigation, CONNECTOR, call, result, error,
                                     user=job["requested_by"], run_by=job["run_by"],
                                     entity_id=job["entity_id"], passive=True,
                                     duration_ms=duration_ms)
        if outcome.finding_id:
            self.db.update("findings", outcome.finding_id,
                           {"egress": _egress_label(body.get("egress"))
                            or self.ledger.settings.egress_label})
        if body.get("persona_used"):
            persona = self.persona(body["persona_used"])
            if persona:
                values: dict[str, Any] = {"last_used": now()}
                if body.get("session") in ("logged_out", "checkpoint"):
                    values.update(status="needs_login", status_at=now())
                self.db.update("personas", persona["id"], values)
        if job["watch_id"]:
            self.db.update("social_watches", job["watch_id"], {
                "last_finding_id": outcome.finding_id, "last_error": error,
                "last_run_at": now()})
        changes = (result.raw.get("changes") if result else None) or {}
        if changes.get("count"):
            self.bus.publish(job["investigation_id"], "social.changed", {
                "url": job["url"], "finding_id": outcome.finding_id,
                "watch_id": job["watch_id"], "changes": changes})
        waiter = self._waiters.pop(job["id"], None)
        if waiter and not waiter.done():
            waiter.set_result(outcome)
        self._finish(job["id"], "done" if not error else "failed", error,
                     finding_id=outcome.finding_id,
                     result={"login_wall": body.get("login_wall"),
                             "persona_used": body.get("persona_used")})
        return {"ok": not error, "finding_id": outcome.finding_id}

    def _save_png(self, inv_id: str, b64: str) -> tuple[str, str]:
        """Write a base64 PNG into the evidence store; returns (filename, sha256)."""
        png = base64.b64decode(b64, validate=True)
        if not png.startswith(b"\x89PNG"):
            raise ValueError("screenshot is not a PNG")
        digest = hashlib.sha256(png).hexdigest()
        filename = f"{digest[:20]}.png"
        shots = screenshot_dir(self.ledger, inv_id)
        shots.mkdir(parents=True, exist_ok=True)
        (shots / filename).write_bytes(png)
        return filename, digest

    def _complete_search(self, job: dict, body: dict) -> dict:
        investigation = self.db.get("investigations", job["investigation_id"])
        if investigation is None:
            self._finish(job["id"], "failed", "investigation is gone")
            return {"ok": False}
        call = {"arg": job["query"], "persona": body.get("persona_used"),
                "platform": job["platform"]}
        result = error = None
        if body.get("ok"):
            try:
                result = self._search_result(investigation, job, body)
            except (ValueError, KeyError, TypeError) as e:
                error = f"bad worker result: {e}"[:500]
        else:
            error = str(body.get("error") or "search failed")[:500]
        outcome = self.ledger.record(investigation, SEARCH_CONNECTOR, call, result, error,
                                     user=job["requested_by"], run_by=job["run_by"],
                                     entity_id=job["entity_id"], passive=True,
                                     duration_ms=int(body.get("duration_ms") or 0))
        if outcome.finding_id:
            self.db.update("findings", outcome.finding_id,
                           {"egress": _egress_label(body.get("egress"))
                            or self.ledger.settings.egress_label})
        self._mark_persona(body)
        waiter = self._waiters.pop(job["id"], None)
        if waiter and not waiter.done():
            waiter.set_result(outcome)
        self._finish(job["id"], "done" if not error else "failed", error,
                     finding_id=outcome.finding_id,
                     result={"results": len(body.get("results") or []),
                             "login_wall": body.get("login_wall")})
        return {"ok": not error, "finding_id": outcome.finding_id}

    def _search_result(self, investigation: dict, job: dict, body: dict) -> ConnectorResult:
        platform = job["platform"]
        entities: list[EntityCandidate] = []
        rels: list[RelationshipCandidate] = []
        seen: set[str] = set()
        for r in body.get("results") or []:
            link = r.get("url") if isinstance(r, dict) else None
            key = profile_key(link) if isinstance(link, str) else None
            if not key or key in seen:
                continue
            seen.add(key)
            name = (r.get("text") or "").strip()[:200] or None
            entities.append(EntityCandidate(
                EntityType.SOCIAL_PROFILE, key,
                {"platform": platform_of(link) or platform, "url": link,
                 "display_name": name, "lead": "search"},
                match_quality=0.4))
        if body.get("screenshot_b64"):
            fn, digest = self._save_png(investigation["id"], body["screenshot_b64"])
            entities.append(EntityCandidate(EntityType.DOCUMENT, digest, {
                "kind": "screenshot", "url": body.get("search_url"), "file": fn,
                "sha256": digest, "captured_at": body.get("captured_at")}))
        note = (f"{len(seen)} candidate {platform} profile(s) for '{job['query']}' — "
                "leads to confirm, not verified matches")
        if body.get("login_wall"):
            note = "search hit a login wall; persona may be logged out — " + note
        elif not seen:
            note = f"no {platform} profiles parsed for '{job['query']}' (markup may have changed)"
        raw = {"platform": platform, "query": job["query"], "search_url": body.get("search_url"),
               "persona": body.get("persona_used"), "login_wall": bool(body.get("login_wall")),
               "results": body.get("results") or [], "captured_at": body.get("captured_at")}
        return ConnectorResult(source=SEARCH_CONNECTOR, passive=True, raw=raw,
                               entities=entities, relationships=rels, note=note)

    def _complete_interactions(self, job: dict, body: dict) -> dict:
        investigation = self.db.get("investigations", job["investigation_id"])
        if investigation is None:
            self._finish(job["id"], "failed", "investigation is gone")
            return {"ok": False}
        call = {"arg": job["url"], "persona": body.get("persona_used")}
        result = error = None
        if body.get("ok"):
            try:
                result = self._interactions_result(investigation, job, body)
            except (ValueError, KeyError, TypeError) as e:
                error = f"bad worker result: {e}"[:500]
        else:
            error = str(body.get("error") or "interaction mapping failed")[:500]
        outcome = self.ledger.record(investigation, INTERACTIONS_CONNECTOR, call, result, error,
                                     user=job["requested_by"], run_by=job["run_by"],
                                     entity_id=job["entity_id"], passive=True,
                                     duration_ms=int(body.get("duration_ms") or 0))
        if outcome.finding_id:
            self.db.update("findings", outcome.finding_id,
                           {"egress": _egress_label(body.get("egress"))
                            or self.ledger.settings.egress_label})
        self._mark_persona(body)
        waiter = self._waiters.pop(job["id"], None)
        if waiter and not waiter.done():
            waiter.set_result(outcome)
        self._finish(job["id"], "done" if not error else "failed", error,
                     finding_id=outcome.finding_id,
                     result={"associates": len(body.get("interactions") or []),
                             "posts_examined": body.get("posts_examined")})
        return {"ok": not error, "finding_id": outcome.finding_id}

    def _interactions_result(self, investigation: dict, job: dict,
                             body: dict) -> ConnectorResult:
        subject = self.db.get("entities", job["entity_id"]) if job["entity_id"] else None
        subject_key = subject["key"] if subject and \
            subject["type"] == EntityType.SOCIAL_PROFILE.value else profile_key(job["url"])
        entities: list[EntityCandidate] = []
        rels: list[RelationshipCandidate] = []
        if subject_key:
            entities.append(EntityCandidate(EntityType.SOCIAL_PROFILE, subject_key,
                                            {"platform": platform_of(job["url"]),
                                             "url": job["url"]}))
        ranked = [i for i in (body.get("interactions") or []) if isinstance(i, dict)]
        for i in ranked:
            link = i.get("url")
            key = profile_key(link) if isinstance(link, str) else None
            count = int(i.get("count") or 0)
            if not key or key == subject_key or count <= 0:
                continue
            # More interactions → stronger lead, capped; still a candidate to confirm.
            mq = min(0.35 + 0.08 * count, 0.8)
            entities.append(EntityCandidate(
                EntityType.SOCIAL_PROFILE, key,
                {"platform": platform_of(link), "url": link,
                 "display_name": (i.get("name") or "").strip()[:200] or None,
                 "interactions": count, "lead": "co-engagement"},
                match_quality=mq))
            if subject_key:
                rels.append(RelationshipCandidate(
                    "interacts_with", EntityType.SOCIAL_PROFILE, key,
                    EntityType.SOCIAL_PROFILE, subject_key,
                    {"count": count, "basis": "comments/replies on the target's posts"}))
        if body.get("screenshot_b64"):
            fn, digest = self._save_png(investigation["id"], body["screenshot_b64"])
            entities.append(EntityCandidate(EntityType.DOCUMENT, digest, {
                "kind": "screenshot", "url": job["url"], "file": fn, "sha256": digest,
                "captured_at": body.get("captured_at")}))
        posts = body.get("posts_examined") or 0
        top = ", ".join(f"{(i.get('name') or profile_key(i.get('url')) or '?')}×{i.get('count')}"
                        for i in ranked[:5])
        note = (f"co-engagement across {posts} post(s): {len([r for r in rels])} frequent "
                f"interactor(s) — likely-close-circle leads, not confirmed. Top: {top}"
                if rels else
                f"no commenters parsed across {posts} post(s) (login wall or changed markup)")
        raw = {"platform": job["platform"], "url": job["url"], "persona": body.get("persona_used"),
               "login_wall": bool(body.get("login_wall")), "posts_examined": posts,
               "interactions": ranked, "captured_at": body.get("captured_at")}
        return ConnectorResult(source=INTERACTIONS_CONNECTOR, passive=True, raw=raw,
                               entities=entities, relationships=rels, note=note)

    def _mark_persona(self, body: dict) -> None:
        if not body.get("persona_used"):
            return
        persona = self.persona(body["persona_used"])
        if not persona:
            return
        values: dict[str, Any] = {"last_used": now()}
        if body.get("session") in ("logged_out", "checkpoint"):
            values.update(status="needs_login", status_at=now())
        self.db.update("personas", persona["id"], values)

    def _result(self, investigation: dict, job: dict, body: dict) -> ConnectorResult:
        inv_id = investigation["id"]
        png = base64.b64decode(body["screenshot_b64"], validate=True)
        if not png.startswith(b"\x89PNG"):
            raise ValueError("screenshot is not a PNG")
        digest = hashlib.sha256(png).hexdigest()
        filename = f"{digest[:20]}.png"
        shots = screenshot_dir(self.ledger, inv_id)
        shots.mkdir(parents=True, exist_ok=True)
        (shots / filename).write_bytes(png)
        html_file = html_sha = None
        if body.get("html_b64"):
            page = base64.b64decode(body["html_b64"], validate=True)
            html_sha = hashlib.sha256(page).hexdigest()
            html_file = f"{html_sha[:20]}.html"
            pages = self.ledger.settings.evidence_dir / inv_id / "pages"
            pages.mkdir(parents=True, exist_ok=True)
            (pages / html_file).write_bytes(page)

        url = job["url"]
        final_url = str(body.get("final_url") or url)
        captured_at = str(body.get("captured_at") or now())
        profile = {k: v for k, v in (body.get("profile") or {}).items()
                   if isinstance(v, (str, int, float)) and v != ""}
        text = str(body.get("text") or "")[:60000]
        subject = self.db.get("entities", job["entity_id"]) if job["entity_id"] else None
        if subject and subject["type"] == EntityType.SOCIAL_PROFILE.value:
            key = subject["key"]
        else:
            key = profile_key(final_url) or profile_key(url)
        raw: dict[str, Any] = {
            "url": url, "final_url": final_url, "platform": platform_of(url),
            "http_status": body.get("http_status"), "title": body.get("title"),
            "login_wall": bool(body.get("login_wall")),
            "logged_in": bool(body.get("persona_used")), "persona": body.get("persona_used"),
            "captured_at": captured_at, "file": filename, "sha256": digest,
            "html_file": html_file, "html_sha256": html_sha, "clipped": body.get("clipped"),
            "blocked": bool(body.get("blocked")), "profile": profile, "text": text,
            "links": [u for u in (body.get("links") or []) if isinstance(u, str)][:200],
            "egress": body.get("egress"),
        }
        raw["changes"] = self._diff(inv_id, key or final_url, raw)

        doc_attrs = {"kind": "screenshot", "url": final_url, "file": filename,
                     "sha256": digest, "captured_at": captured_at,
                     "http_status": body.get("http_status"), "blocked": raw["blocked"]}
        entities = [EntityCandidate(EntityType.URL, url, {"title": body.get("title")}),
                    EntityCandidate(EntityType.DOCUMENT, digest, doc_attrs)]
        rels = [RelationshipCandidate("captured_as", EntityType.URL, url,
                                      EntityType.DOCUMENT, digest)]
        if key:
            attrs = {"platform": platform_of(url), "url": url,
                     **{k: profile[k] for k in ("display_name", "bio", "followers", "following",
                                                "posts", "location", "avatar") if k in profile}}
            entities.append(EntityCandidate(EntityType.SOCIAL_PROFILE, key, attrs))
            rels.append(RelationshipCandidate("captured_as", EntityType.SOCIAL_PROFILE, key,
                                              EntityType.DOCUMENT, digest))
            for link in raw["links"]:
                other = profile_key(link)
                if other and other != key:
                    entities.append(EntityCandidate(
                        EntityType.SOCIAL_PROFILE, other,
                        {"platform": platform_of(link), "url": link}, match_quality=0.7))
                    rels.append(RelationshipCandidate("links_to", EntityType.SOCIAL_PROFILE,
                                                      key, EntityType.SOCIAL_PROFILE, other))
            for email in sorted(set(_EMAIL.findall(text)))[:20]:
                entities.append(EntityCandidate(EntityType.EMAIL, email.lower(),
                                                match_quality=0.8))
                rels.append(RelationshipCandidate("mentioned_in", EntityType.EMAIL,
                                                  email.lower(), EntityType.SOCIAL_PROFILE, key))
        note = None
        if raw["login_wall"] and not raw["logged_in"]:
            note = ("login wall and no healthy persona for this platform: only the public "
                    "teaser was captured")
        elif raw["blocked"]:
            note = "capture blocked (bot wall): open the URL in a normal browser"
        if raw["changes"].get("count"):
            note = ((note + "; ") if note else "") + \
                f"{raw['changes']['count']} change(s) since {raw['changes']['since']}"
        return ConnectorResult(source=CONNECTOR, passive=True, raw=raw, entities=entities,
                               relationships=rels, note=note)

    def _diff(self, inv_id: str, key: str, raw: dict) -> dict[str, Any]:
        """What changed since the last capture of the same profile."""
        prev = None
        for f in self.db.query(
                "SELECT id, raw_path, fetched_at FROM findings WHERE investigation_id = ? AND "
                "connector = ? AND error IS NULL ORDER BY fetched_at DESC, rowid DESC LIMIT 20",
                (inv_id, CONNECTOR)):
            path = self.ledger.settings.data_dir / (f["raw_path"] or "")
            if not f["raw_path"] or not path.is_file():
                continue
            old = json.loads(path.read_text(encoding="utf-8")).get("raw") or {}
            old_key = profile_key(old.get("final_url") or "") or profile_key(old.get("url") or "")
            if key in (old_key, old.get("final_url")) or old.get("url") == raw["url"]:
                prev = (f, old)
                break
        if prev is None:
            return {}
        f, old = prev
        if bool(old.get("logged_in")) != bool(raw["logged_in"]):
            # A logged-out teaser vs. a logged-in page differ by design; not a change.
            return {"since": f["fetched_at"], "previous_finding": f["id"], "count": 0,
                    "skipped": "logged-in state differs from the previous capture"}
        fields = {}
        for name in sorted(set(old.get("profile") or {}) | set(raw["profile"])):
            a, b = (old.get("profile") or {}).get(name), raw["profile"].get(name)
            if a != b:
                fields[name] = [a, b]
        old_lines, new_lines = _lines(old.get("text") or ""), _lines(raw["text"])
        added = [line for line in new_lines if line not in old_lines][:30]
        removed = [line for line in old_lines if line not in new_lines][:30]
        return {"since": f["fetched_at"], "previous_finding": f["id"],
                "count": len(fields) + len(added) + len(removed),
                "fields": fields, "new_lines": added, "removed_lines": removed}

    # --- watches --------------------------------------------------------------------

    def add_watch(self, investigation: dict, url: str, *, interval_hours: int,
                  persona: str | None, entity_id: str | None, user: str) -> dict[str, Any]:
        if interval_hours < MIN_WATCH_HOURS:
            raise SocialError(f"interval must be at least {MIN_WATCH_HOURS} hour(s)")
        if not _URL.match(url or "") or platform_of(url) is None:
            raise SocialError(f"not a supported social profile URL ({', '.join(PLATFORMS)})")
        try:
            guard.check(investigation, CONNECTOR, True, url)
        except guard.GateError as e:
            raise SocialError(str(e)) from e
        subject = self.subject_entity(investigation["id"], url, entity_id)
        if subject is None:
            raise SocialError("this profile is not in the investigation's graph")
        if persona and persona != "none":
            row = self.persona(persona)
            if row is None or row["platform"] != platform_of(url):
                raise SocialError(f"persona '{persona}' does not exist for this platform")
        row = {"id": new_id("sw"), "investigation_id": investigation["id"],
               "entity_id": subject["id"], "url": url, "persona": persona,
               "interval_hours": interval_hours, "active": True, "created_by": user,
               "created_at": now(), "last_run_at": None, "last_finding_id": None,
               "last_error": None}
        self.db.insert("social_watches", row)
        self.db.audit(user=user, action="social_watch.create",
                      investigation_id=investigation["id"], connector=CONNECTOR, target=url,
                      detail={"interval_hours": interval_hours, "persona": persona})
        return self.db.get("social_watches", row["id"])

    def watches(self, inv_id: str) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM social_watches WHERE investigation_id = ? "
                             "ORDER BY created_at", (inv_id,))

    def update_watch(self, watch_id: str, values: dict[str, Any], user: str) -> dict:
        watch = self.db.get("social_watches", watch_id)
        if watch is None:
            raise SocialError("no such watch")
        changes = {}
        if values.get("active") is not None:
            changes["active"] = bool(values["active"])
        if values.get("interval_hours") is not None:
            if int(values["interval_hours"]) < MIN_WATCH_HOURS:
                raise SocialError(f"interval must be at least {MIN_WATCH_HOURS} hour(s)")
            changes["interval_hours"] = int(values["interval_hours"])
        if changes:
            self.db.update("social_watches", watch_id, changes)
            self.db.audit(user=user, action="social_watch.update",
                          investigation_id=watch["investigation_id"], target=watch["url"],
                          detail=changes)
        return self.db.get("social_watches", watch_id)

    def delete_watch(self, watch_id: str, user: str) -> None:
        watch = self.db.get("social_watches", watch_id)
        if watch is None:
            raise SocialError("no such watch")
        self.db.conn.execute("DELETE FROM social_watches WHERE id = ?", (watch_id,))
        self.db.conn.commit()
        self.db.audit(user=user, action="social_watch.delete",
                      investigation_id=watch["investigation_id"], target=watch["url"])

    async def tick(self) -> int:
        """Queue captures for due watches. Returns how many were queued."""
        if not self.online():
            return 0
        queued = 0
        for w in self.db.query("SELECT * FROM social_watches WHERE active = 1"):
            if w["last_run_at"]:
                due = datetime.fromisoformat(w["last_run_at"]) + timedelta(
                    hours=w["interval_hours"])
                if datetime.now(UTC) < due:
                    continue
            pending = self.db.one("SELECT 1 FROM social_jobs WHERE watch_id = ? AND status IN "
                                  "('queued', 'running')", (w["id"],))
            if pending:
                continue
            inv = self.db.get("investigations", w["investigation_id"])
            if inv is None or inv["deleted_at"]:
                self.db.update("social_watches", w["id"], {"active": False})
                continue
            outcome = await self.capture(inv, w["url"], user=w["created_by"],
                                         run_by=f"watch:{w['id']}", persona=w["persona"],
                                         entity_id=w["entity_id"], watch_id=w["id"],
                                         wait=False)
            if outcome.refused:
                self.db.update("social_watches", w["id"], {"last_error": outcome.error,
                                                           "last_run_at": now()})
            else:
                self.db.update("social_watches", w["id"], {"last_run_at": now()})
                queued += 1
        return queued

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._scheduler())

    async def _scheduler(self) -> None:
        while True:
            try:
                await self.tick()
                self._expire()
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the loop alive; the next tick retries
                pass
            await asyncio.sleep(SCHEDULER_TICK_S)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _norm_url(url: str) -> str:
    p = urlparse(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    return f"{host}{p.path.rstrip('/').lower()}"


def _lines(text: str) -> list[str]:
    """Distinct meaningful lines of page text, in order (for snapshot diffs)."""
    seen: dict[str, None] = {}
    for line in text.splitlines():
        line = " ".join(line.split())
        if len(line) >= 25 and not re.fullmatch(r"[\d\s.,:/KkMm·-]+", line):
            seen.setdefault(line, None)
    return list(seen)


def _egress_label(egress: Any) -> str | None:
    if not isinstance(egress, dict) or not egress.get("ip"):
        return None
    where = ", ".join(str(egress[k]) for k in ("city", "country") if egress.get(k))
    vpn = "mullvad" if egress.get("mullvad") else "no-vpn"
    return f"kali {vpn} {egress['ip']}" + (f" ({where})" if where else "")
