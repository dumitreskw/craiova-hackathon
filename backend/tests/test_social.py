"""Social personas + capture through the (simulated) Kali worker.

The real worker runs Playwright on Kali; here we drive :class:`SocialHub`
directly and feed it the kind of result body the worker posts back, so the
queue, the guard, provenance, the snapshot diff and watches are all exercised
with no browser and no network.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from app.social import PLATFORMS, SocialError, profile_key

# A 1x1 PNG (valid signature) is all the hub needs to store an image finding.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture
def hub(platform):
    return platform.social


@pytest.fixture
def investigation(make_investigation):
    return make_investigation()


def _social_entity(platform, inv_id, url):
    """Add a SocialProfile to the graph so the hub will accept a capture of it."""
    from app.models import new_id, now
    from app.resolver import entity_view  # noqa: F401 - ensure import path is valid
    key = profile_key(url)
    eid = new_id("e")
    platform.db.insert("entities", {
        "id": eid, "investigation_id": inv_id, "type": "SocialProfile", "key": key,
        "attributes": {}, "confidence": 0.9, "status": "active",
        "first_seen": now(), "last_seen": now()})
    return eid


def _result_body(persona_used=None, *, text="Head of Ops at Acme\nBased in Berlin, Germany",
                 followers="1,234", login_wall=False, html=b"<html>profile</html>"):
    return {
        "ok": True, "http_status": 200, "title": "Jane Doe", "final_url": None,
        "login_wall": login_wall, "blocked": False, "persona_used": persona_used,
        "captured_at": None, "profile": {"display_name": "Jane Doe", "followers": followers,
                                         "bio": "Head of Ops"},
        "text": text, "links": ["https://twitter.com/janedoe"],
        "screenshot_b64": base64.b64encode(PNG).decode(),
        "html_b64": base64.b64encode(html).decode(), "duration_ms": 1200,
        "egress": {"ip": "10.0.0.1", "city": "Frankfurt", "country": "DE", "mullvad": True},
    }


# --- keying -----------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://www.linkedin.com/in/jane-doe/", "linkedin:jane-doe"),
    ("https://www.linkedin.com/company/acme/", "linkedin:company/acme"),
    ("https://x.com/JaneDoe", "x:janedoe"),
    ("https://twitter.com/JaneDoe", "x:janedoe"),
    ("https://www.instagram.com/jane.doe/", "instagram:jane.doe"),
    ("https://facebook.com/profile.php?id=100099", "facebook:100099"),
    ("https://www.tiktok.com/@janedoe", "tiktok:janedoe"),
    ("https://www.reddit.com/user/janedoe", "reddit:janedoe"),
    ("https://example.com/jane", None),
    ("https://x.com/home", None),
])
def test_profile_key(url, expected):
    assert profile_key(url) == expected


# --- personas ---------------------------------------------------------------

def test_create_persona_validates(hub):
    p = hub.create_persona("li-ana", "linkedin", "de-fra", "research acct", "tester")
    assert p["status"] == "needs_login" and p["platform"] == "linkedin"
    with pytest.raises(SocialError):
        hub.create_persona("li-ana", "linkedin", "de", None, "tester")  # duplicate
    with pytest.raises(SocialError):
        hub.create_persona("bad name", "linkedin", "de", None, "tester")
    with pytest.raises(SocialError):
        hub.create_persona("ok2", "myspace", "de", None, "tester")      # bad platform
    with pytest.raises(SocialError):
        hub.create_persona("ok3", "linkedin", "germany", None, "tester")  # bad region
    assert set(PLATFORMS) >= {"linkedin", "facebook", "instagram", "x", "tiktok"}


def test_login_needs_online_worker(hub):
    hub.create_persona("li-ana", "linkedin", "de", None, "tester")
    with pytest.raises(SocialError, match="offline"):
        hub.login("li-ana", "tester")


# --- capture lifecycle ------------------------------------------------------

async def test_capture_refused_when_not_in_graph(hub, investigation):
    hub.worker_seen = __import__("time").monotonic()  # pretend worker is online
    out = await hub.capture(investigation, "https://www.linkedin.com/in/jane-doe/",
                            user="tester", run_by="analyst", wait=False)
    assert out.refused and "not in the investigation" in out.error


async def test_capture_refused_when_worker_offline(hub, platform, investigation):
    _social_entity(platform, investigation["id"], "https://x.com/janedoe")
    out = await hub.capture(investigation, "https://x.com/janedoe", user="tester",
                            run_by="analyst", wait=False)
    assert out.refused and "offline" in out.error


async def test_capture_roundtrip_creates_finding_and_entities(hub, platform, investigation):
    import time
    hub.worker_seen = time.monotonic()
    url = "https://www.instagram.com/jane.doe/"
    _social_entity(platform, investigation["id"], url)

    out = await hub.capture(investigation, url, user="tester", run_by="analyst",
                            persona="none", wait=False)
    assert out.finding_id is None  # queued; worker fills it

    job = await hub.next_job(wait=0)
    assert job["kind"] == "capture" and job["url"] == url and job["persona"] is None

    res = hub.complete(job["id"], _result_body(persona_used=None))
    assert res["ok"] and res["finding_id"]

    f = platform.db.get("findings", res["finding_id"])
    assert f["connector"] == "social_capture" and f["error"] is None
    assert f["egress"].startswith("kali mullvad")
    # SocialProfile entity carries the captured attributes; a Document holds the PNG.
    prof = platform.db.one("SELECT * FROM entities WHERE investigation_id = ? AND type = ? "
                           "AND key = ?", (investigation["id"], "SocialProfile",
                                           "instagram:jane.doe"))
    assert prof is not None
    doc = platform.db.one("SELECT * FROM entities WHERE investigation_id = ? AND type = ?",
                          (investigation["id"], "Document"))
    sha = hashlib.sha256(PNG).hexdigest()
    assert doc["attributes"]["sha256"][0]["value"] == sha
    # The linked twitter handle becomes a candidate SocialProfile.
    other = platform.db.one("SELECT * FROM entities WHERE type = ? AND key = ?",
                            ("SocialProfile", "x:janedoe"))
    assert other is not None

    job2 = await hub.next_job(wait=0)
    assert job2 is None  # queue drained


async def test_second_capture_diffs_against_first(hub, platform, investigation):
    import time
    hub.worker_seen = time.monotonic()
    url = "https://x.com/janedoe"
    _social_entity(platform, investigation["id"], url)

    await hub.capture(investigation, url, user="t", run_by="analyst", persona="none", wait=False)
    job = await hub.next_job(wait=0)
    hub.complete(job["id"], _result_body(persona_used=None, followers="1,000",
                                         text="Head of Ops at Acme\nBerlin"))

    await hub.capture(investigation, url, user="t", run_by="analyst", persona="none", wait=False)
    job2 = await hub.next_job(wait=0)
    r = hub.complete(job2["id"], _result_body(
        persona_used=None, followers="2,500",
        text="Head of Ops at Acme\nWe are now hiring backend engineers in Berlin!"))
    f = platform.db.get("findings", r["finding_id"])
    import json
    raw = json.loads((platform.settings.data_dir / f["raw_path"]).read_text(encoding="utf-8"))
    changes = raw["raw"]["changes"]
    assert changes["count"] >= 1
    assert "followers" in changes["fields"]
    assert any("hiring" in line for line in changes["new_lines"])


async def test_watch_scheduler_queues_due_captures(hub, platform, investigation):
    import time
    hub.worker_seen = time.monotonic()
    url = "https://www.tiktok.com/@janedoe"
    _social_entity(platform, investigation["id"], url)
    watch = hub.add_watch(investigation, url, interval_hours=1, persona="none",
                          entity_id=None, user="tester")
    assert watch["active"]
    queued = await hub.tick()
    assert queued == 1
    job = await hub.next_job(wait=0)
    assert job["kind"] == "capture" and job["url"] == url
    # Immediately ticking again must not double-queue (one in flight already).
    hub.db.update("social_watches", watch["id"], {"last_run_at": None})
    assert await hub.tick() == 0


async def test_persona_capture_marks_persona_used(hub, platform, investigation):
    import time
    hub.worker_seen = time.monotonic()
    hub.create_persona("li-ana", "linkedin", "de", None, "tester")
    hub.db.update("personas", hub.persona("li-ana")["id"], {"status": "ok"})
    url = "https://www.linkedin.com/in/jane-doe/"
    _social_entity(platform, investigation["id"], url)

    out = await hub.capture(investigation, url, user="t", run_by="analyst",
                            persona="li-ana", wait=False)
    assert not out.refused
    job = await hub.next_job(wait=0)
    assert job["persona"]["name"] == "li-ana"
    hub.complete(job["id"], _result_body(persona_used="li-ana"))
    assert hub.persona("li-ana")["last_used"] is not None


async def test_heartbeat_updates_persona_health(hub):
    hub.create_persona("x-bot", "x", "nl", None, "tester")
    hub.heartbeat({"version": "1.0.0", "vpn": {"connected": True},
                   "sessions": {"x-bot": "ok"}})
    assert hub.persona("x-bot")["status"] == "ok"
    assert hub.online()
    hub.heartbeat({"sessions": {"x-bot": "logged_out"}})
    assert hub.persona("x-bot")["status"] == "needs_login"
