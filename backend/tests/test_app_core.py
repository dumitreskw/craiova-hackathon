"""Offline tests for the app core: gate, detection, resolver, ledger, scan, retention."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import guard
from app.detect import DetectError, detect
from app.resolver import combine
from app.retention import purge_expired
from connectors import ConnectorSpec
from connectors.base import EntityType
from tests.conftest import fake_broken

# --- guard --------------------------------------------------------------------


def test_in_scope_matches_host_parent_domain_and_cidr():
    scope = ["example.com", "198.51.100.0/24"]
    assert guard.in_scope("example.com", scope)
    assert guard.in_scope("https://dev.example.com:8443/login", scope)
    assert guard.in_scope("198.51.100.7", scope)
    assert not guard.in_scope("example.com.evil.net", scope)
    assert not guard.in_scope("notexample.com", scope)
    assert not guard.in_scope("203.0.113.1", scope)


def _inv(**over):
    return {"mode": "passive", "settings": {}, **over}


def test_gate_allows_passive_and_active_in_active_mode():
    guard.check(_inv(), "dns", True, "example.com")
    with pytest.raises(guard.GateError, match="passive"):
        guard.check(_inv(), "http_probe", False, "example.com")
    guard.check(_inv(mode="active"), "http_probe", False, "example.com")
    guard.check(_inv(mode="active"), "http_probe", False, "www.example.com")


def test_gate_honours_connector_allow_and_deny_lists():
    with pytest.raises(guard.GateError, match="excluded"):
        guard.check(_inv(settings={"exclude_connectors": ["dns"]}), "dns", True, "x.com")
    with pytest.raises(guard.GateError, match="not enabled"):
        guard.check(_inv(settings={"connectors": ["crtsh"]}), "dns", True, "x.com")


# --- detect -------------------------------------------------------------------


@pytest.mark.parametrize("value,etype,key", [
    ("Example.COM", EntityType.DOMAIN, "example.com"),
    ("https://www.example.com/a?b=1", EntityType.DOMAIN, "www.example.com"),
    ("Jane.Doe@Example.com", EntityType.EMAIL, "jane.doe@example.com"),
    ("203.0.113.10", EntityType.IP, "203.0.113.10"),
    ("RO 12345678", EntityType.ORGANIZATION, "RO12345678"),
    ("+40 721 000 000", EntityType.PHONE, "+40721000000"),
    ("@octocat", EntityType.USERNAME, "octocat"),
    ("  Jane   DOE ", EntityType.PERSON, "jane doe"),
])
def test_detect(value, etype, key):
    assert detect(value) == (etype, key)


def test_detect_hint_and_errors():
    assert detect("jane", "Person") == (EntityType.PERSON, "jane")
    with pytest.raises(DetectError):
        detect("")
    with pytest.raises(DetectError):
        detect("x", "Spaceship")


# --- resolver -------------------------------------------------------------------


def test_combine_is_noisy_or():
    assert combine([(0.9, 1.0)]) == 0.9
    assert combine([(0.9, 1.0), (0.5, 1.0)]) == 0.95
    assert combine([(0.95, 0.3)]) == 0.285


async def test_merge_dedupes_and_raises_confidence(platform, make_investigation):
    inv = make_investigation()
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.DOMAIN, "example.com")
    await platform.ledger.run(inv, "dns", "example.com", user="t", run_by="test", entity_id=seed)
    ip = platform.db.one("SELECT * FROM entities WHERE type = 'IPAddress'")
    first = ip["confidence"]
    await platform.ledger.run(inv, "dns", "example.com", user="t", run_by="test", entity_id=seed)
    ips = platform.db.query("SELECT * FROM entities WHERE type = 'IPAddress'")
    assert len(ips) == 1
    assert ips[0]["confidence"] > first
    sources = platform.db.query("SELECT * FROM entity_sources WHERE entity_id = ?", (ips[0]["id"],))
    assert len(sources) == 2


async def test_legal_hits_stay_unverified_candidates(platform, make_investigation):
    inv = make_investigation()
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.PERSON, "jane doe")
    await platform.ledger.run(inv, "interpol", "jane doe", user="t", run_by="test",
                              entity_id=seed)
    persons = platform.db.query("SELECT * FROM entities WHERE type = 'Person' ORDER BY key")
    seed_row = next(p for p in persons if p["id"] == seed)
    candidates = [p for p in persons if p["status"] == "candidate"]
    # Two same-name listings stay two separate candidates; the seed is untouched.
    assert len(candidates) == 2
    assert seed_row["status"] == "active" and seed_row["attributes"] == {}
    assert all(c["confidence"] < 0.5 for c in candidates)
    links = platform.db.query("SELECT * FROM edges WHERE type = 'possible_match'")
    assert {e["to_id"] for e in links} == {c["id"] for c in candidates}
    assert all(e["from_id"] == seed for e in links)
    # No edge other than possible_match touches the seed.
    assert not platform.db.query(
        "SELECT * FROM edges WHERE (from_id = ? OR to_id = ?) AND type != 'possible_match'",
        (seed, seed))


async def test_confirm_pins_confidence(platform, make_investigation):
    inv = make_investigation()
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.PERSON, "jane doe")
    await platform.ledger.run(inv, "interpol", "jane doe", user="t", run_by="t", entity_id=seed)
    cand = platform.db.one("SELECT * FROM entities WHERE status = 'candidate'")
    platform.resolver.set_status(cand["id"], "confirmed")
    assert platform.db.get("entities", cand["id"])["confidence"] == 1.0


# --- ledger -------------------------------------------------------------------


async def test_ledger_records_finding_raw_file_and_audit(platform, make_investigation, settings):
    inv = make_investigation()
    out = await platform.ledger.run(inv, "crtsh", "example.com", user="ana", run_by="test")
    f = platform.db.get("findings", out.finding_id)
    assert f["sha256"] and f["raw_path"] and not f["error"]
    assert (settings.data_dir / f["raw_path"]).exists()
    audit = platform.db.query("SELECT * FROM audit WHERE connector = 'crtsh'")
    assert audit[0]["user"] == "ana" and audit[0]["outcome"] == "ok"


async def test_ledger_refuses_active_and_audits_it(platform, make_investigation):
    inv = make_investigation()
    out = await platform.ledger.run(inv, "http_probe", "example.com", user="t", run_by="t")
    assert out.refused and out.finding_id is None
    assert platform.db.one("SELECT * FROM audit WHERE outcome = 'refused'")["connector"] == \
        "http_probe"
    assert not platform.db.query("SELECT * FROM findings")


async def test_ledger_records_connector_failure(platform, make_investigation, fake_registry):
    fake_registry["broken"] = ConnectorSpec("broken", fake_broken, EntityType.DOMAIN, True, "x")
    inv = make_investigation()
    out = await platform.ledger.run(inv, "broken", "example.com", user="t", run_by="t")
    f = platform.db.get("findings", out.finding_id)
    assert "upstream exploded" in f["error"] and f["raw_path"] is None


def test_audit_is_append_only(platform):
    platform.db.audit(user="t", action="x")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        platform.db.conn.execute("DELETE FROM audit")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        platform.db.conn.execute("UPDATE audit SET user = 'someone else'")


# --- scan -------------------------------------------------------------------


async def test_quick_scan_pivots_and_skips_active_in_passive_mode(platform, make_investigation):
    inv = make_investigation()
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.DOMAIN, "example.com")
    run_id = platform.scan.start(inv, [seed], "t")
    await platform.scan.tasks[run_id]
    run = platform.db.get("agent_runs", run_id)
    assert run["status"] == "finished"
    called = {(f["connector"], f["input"]["arg"]) for f in platform.db.query("SELECT * FROM findings")}
    assert ("dns", "example.com") in called and ("crtsh", "example.com") in called
    assert ("dns", "dev.example.com") in called              # depth-1 pivot on a subdomain
    assert ("gravatar", "hostmaster@example.com") in called  # depth-1 pivot on an email
    assert not any(c == "http_probe" for c, _ in called)     # active: never offered
    # Third-party infrastructure (the DNS provider) is recorded, never pivoted on.
    assert not any("dnsprovider.net" in arg for _, arg in called)
    assert platform.db.one("SELECT * FROM entities WHERE key = 'ns1.dnsprovider.net'")
    assert run["stats"]["off_target_skipped"] == 2
    assert not platform.db.query("SELECT * FROM audit WHERE outcome = 'refused'")
    # No connector ran twice on the same input (loop detection).
    rows = platform.db.query("SELECT connector, input FROM findings")
    assert len(rows) == len({(r["connector"], r["input"]["arg"]) for r in rows})


async def test_quick_scan_runs_active_in_active_mode(platform, make_investigation):
    inv = make_investigation(mode="active", settings={"max_depth": 0})
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.DOMAIN, "example.com")
    await platform.scan.run("ar_x", inv, [seed], "t")
    assert platform.db.query("SELECT * FROM findings WHERE connector = 'http_probe'")


async def test_scan_does_not_screen_people_it_discovers(platform, make_investigation):
    inv = make_investigation(settings={"max_depth": 2})
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.EMAIL, "jane@example.com")
    await platform.scan.run("ar_x", inv, [seed], "t")
    calls = {(f["connector"], f["input"]["arg"])
             for f in platform.db.query("SELECT * FROM findings")}
    connectors_run = {c for c, _ in calls}
    assert "gravatar" in connectors_run and "github_user" in connectors_run
    # The person gravatar revealed ("jane doe") is recorded but never screened.
    assert not connectors_run & {"interpol", "europol"}
    assert not any("jane doe" in arg.lower() for _, arg in calls)


async def test_scan_respects_call_cap(platform, make_investigation, settings):
    object.__setattr__(settings, "max_calls_per_scan", 2)
    inv = make_investigation()
    seed = platform.resolver.ensure_entity(inv["id"], EntityType.DOMAIN, "example.com")
    stats = await platform.scan.run("ar_x", inv, [seed], "t")
    assert stats["calls"] == 2 and stats["calls_capped"] >= 1


# --- retention -----------------------------------------------------------------


async def test_purge_expired_deletes_data_but_keeps_audit(platform, make_investigation, settings):
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat(timespec="seconds")
    inv = make_investigation(created_at=old)
    held = make_investigation(created_at=old, legal_hold=1)
    for i in (inv, held):
        await platform.ledger.run(i, "crtsh", "example.com", user="t", run_by="t")
    assert purge_expired(platform.db, settings) == [inv["id"]]
    assert not platform.db.query("SELECT * FROM findings WHERE investigation_id = ?", (inv["id"],))
    assert platform.db.query("SELECT * FROM findings WHERE investigation_id = ?", (held["id"],))
    assert not (settings.evidence_dir / inv["id"]).exists()
    assert platform.db.query("SELECT * FROM audit WHERE action = 'investigation.purge'")
