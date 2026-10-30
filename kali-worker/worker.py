#!/usr/bin/env python3
"""osint social worker — runs on the Kali VM, serves the platform's captures.

The platform runs on a VPS that cannot reach this machine, so the worker
connects *out* to it: it long-polls ``/api/v1/worker/jobs/next``, does the job
with Playwright and the persona's saved browser profile, and posts the result
back. Nothing listens for inbound connections here, and the personas' session
cookies never leave this machine — the platform only ever sees a name, a
platform, a region and a health status.

What it does
------------
* ``capture`` — open a public profile URL and return a full-page screenshot
  (with a source banner), the page HTML, visible text, and a few profile
  fields. Logged out by default; it uses the persona's profile only when the
  page shows a login wall, or when the platform asked for a specific persona.
* ``login``   — open a *visible* browser on Kali's desktop with the persona's
  profile so you can sign in by hand (2FA, checkpoints). The session is then
  saved to that profile for later captures.
* ``check``   — report whether a persona's saved session is still signed in.

Read-only by design. It loads, scrolls and screenshots. It never follows,
likes, comments, messages, or joins anything, and it never creates accounts.

Before any network request it checks the Mullvad VPN is up and, for a persona,
that the exit is in the persona's own region — otherwise the job is refused, so
a persona is never exposed on the wrong IP.

Config via environment (see kali-worker/osint-worker.env.example):
    OSINT_PLATFORM_URL   https://osint.dumisoft.ro
    OSINT_WORKER_TOKEN   shared secret, matches the platform's OSINT_WORKER_TOKEN
    OSINT_BASIC_AUTH     user:pass for the platform's Caddy basic-auth (if any)
    OSINT_PERSONA_DIR    where persona browser profiles live (default ~/personas)
    OSINT_REQUIRE_VPN    "1" (default) refuse jobs unless Mullvad is connected
    OSINT_WORKER_HEADFUL_DISPLAY  X display for login jobs (default ":0")
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

VERSION = "1.0.0"
HEARTBEAT_EVERY = 30          # seconds between heartbeats while idle
POLL_WAIT = 25                # long-poll seconds asked of the platform
CAPTURE_NAV_MS = 30_000
SETTLE_MS = 2500
SCROLL_STEPS = 6
VIEWPORT = {"width": 1366, "height": 900}
MAX_HEIGHT = 8000             # clip very long pages
LOGIN_WAIT_S = 15 * 60

PERSONA_DIR = Path(os.environ.get("OSINT_PERSONA_DIR", Path.home() / "personas"))

# Phrases that mean "you must log in to see this" rather than real content.
LOGIN_WALL = ("log in to continue", "sign in to continue", "log in to see",
              "join linkedin", "sign up to see", "see photos and videos",
              "please log in", "you must log in", "log in or sign up",
              "create an account or log in", "sign up to continue")
BLOCK = ("just a moment", "attention required", "access denied", "are you a robot",
         "verify you are human", "unusual traffic", "captcha", "enable javascript")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(*a: object) -> None:
    print(now(), *a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Mullvad VPN
# --------------------------------------------------------------------------- #

def mullvad_status() -> dict:
    """``{connected, country, city}`` from the Mullvad CLI (best effort)."""
    try:
        out = subprocess.run(["mullvad", "status", "-v"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {"connected": False, "error": "mullvad CLI not available"}
    connected = out.lower().startswith("connected") or "\nconnected" in out.lower()
    country = city = None
    for line in out.splitlines():
        line = line.strip()
        if line.lower().startswith("visible location") or "country:" in line.lower():
            country = line.split(":", 1)[-1].strip() or country
        if line.lower().startswith("relay:") or "city:" in line.lower():
            city = line.split(":", 1)[-1].strip() or city
    return {"connected": connected, "country": country, "city": city, "raw": out.strip()[:400]}


def mullvad_set_location(region: str) -> None:
    """Point Mullvad at ``country`` or ``country-city`` and (re)connect."""
    parts = region.split("-")
    try:
        subprocess.run(["mullvad", "relay", "set", "location", *parts],
                       capture_output=True, timeout=15)
        subprocess.run(["mullvad", "connect"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        log("mullvad set-location failed:", e)


def egress() -> dict:
    """Public IP and geo, so each finding records where it actually left from."""
    try:
        with request.urlopen("https://ipinfo.io/json", timeout=10) as r:
            data = json.load(r)
    except (error.URLError, OSError, ValueError):
        return {}
    mull = {}
    try:
        with request.urlopen("https://am.i.mullvad.net/json", timeout=10) as r:
            mull = json.load(r)
    except (error.URLError, OSError, ValueError):
        pass
    return {"ip": data.get("ip"), "city": data.get("city"), "country": data.get("country"),
            "org": data.get("org"), "mullvad": bool(mull.get("mullvad_exit_ip"))}


def require_vpn() -> bool:
    return os.environ.get("OSINT_REQUIRE_VPN", "1") != "0"


def region_ok(region: str, status: dict) -> bool:
    """Loose check that the VPN exit matches the persona's country."""
    want = region.split("-")[0].lower()
    hay = " ".join(str(status.get(k) or "") for k in ("country", "city", "raw")).lower()
    return want in hay or _COUNTRY.get(want, want) in hay


_COUNTRY = {"de": "germany", "ro": "romania", "nl": "netherlands", "fr": "france",
            "gb": "uk", "uk": "united kingdom", "us": "usa", "se": "sweden",
            "es": "spain", "it": "italy", "pl": "poland", "ch": "switzerland",
            "at": "austria", "be": "belgium", "cz": "czech", "dk": "denmark"}


# --------------------------------------------------------------------------- #
# Platform HTTP client
# --------------------------------------------------------------------------- #

class Platform:
    def __init__(self, base: str, token: str, basic: str | None):
        self.base = base.rstrip("/")
        self.token = token
        self.auth = None
        if basic and ":" in basic:
            self.auth = "Basic " + base64.b64encode(basic.encode()).decode()

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 60):
        url = f"{self.base}/api/v1{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(url, data=data, method=method)
        req.add_header("X-Worker-Token", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.auth:
            req.add_header("Authorization", self.auth)
        with request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}

    def heartbeat(self, body: dict) -> dict:
        return self._req("POST", "/worker/heartbeat", body, timeout=20)

    def next_job(self) -> dict | None:
        try:
            return self._req("GET", f"/worker/jobs/next?wait={POLL_WAIT}",
                             timeout=POLL_WAIT + 15).get("job")
        except error.HTTPError as e:
            if e.code in (502, 503, 504):
                return None
            raise

    def complete(self, job_id: str, body: dict) -> dict:
        return self._req("POST", f"/worker/jobs/{job_id}/complete", body, timeout=60)


# --------------------------------------------------------------------------- #
# Browser
# --------------------------------------------------------------------------- #

def persona_profile(name: str) -> Path:
    path = PERSONA_DIR / name / "profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _banner(url: str, when: str, persona: str | None) -> str:
    who = f"persona {persona}" if persona else "logged out"
    text = (f"SOURCE: {url}  |  captured {when.replace('T', ' ').replace('+00:00', '')} UTC"
            f"  |  {who} via Kali/Mullvad").replace('"', "")
    return ("<div id=osint_banner style='all:unset;display:block;background:#1F3864;"
            "color:#fff;font:600 14px/1.5 Arial,sans-serif;padding:8px 12px;"
            f"word-break:break-all'>{text}</div>")


def _profile_fields(page) -> dict:
    """A few profile fields, read defensively from meta tags and the DOM."""
    try:
        return page.evaluate(
            """() => {
              const out = {};
              const meta = n => (document.querySelector(`meta[property="${n}"]`)
                || document.querySelector(`meta[name="${n}"]`) || {}).content || null;
              out.display_name = meta('og:title') || (document.title || '').trim() || null;
              out.bio = meta('og:description') || meta('description') || null;
              out.avatar = meta('og:image') || null;
              const grab = re => {
                const m = (document.body.innerText || '').match(re);
                return m ? m[1].replace(/[,\\.]/g,'') : null;
              };
              out.followers = grab(/([\\d.,KkMm]+)\\s+followers?/);
              out.following = grab(/([\\d.,KkMm]+)\\s+following/);
              out.posts = grab(/([\\d.,KkMm]+)\\s+posts?/);
              return out;
            }""") or {}
    except Exception:  # noqa: BLE001 - page scripting is best effort
        return {}


def capture(job: dict) -> dict:
    """Open a profile URL and return screenshot + HTML + text + fields."""
    from playwright.sync_api import sync_playwright

    url = job["url"]
    persona = (job.get("persona") or {}).get("name") if job.get("persona") else None
    forced = job.get("mode") == "persona"
    anonymous = job.get("mode") == "anonymous"
    started = time.monotonic()
    when = now()

    with sync_playwright() as pw:
        def open_ctx(use_persona: bool):
            if use_persona and persona:
                ctx = pw.chromium.launch_persistent_context(
                    str(persona_profile(persona)), channel="chromium", headless=True,
                    viewport=VIEWPORT, locale="en-US",
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
                return ctx, ctx.pages[0] if ctx.pages else ctx.new_page()
            browser = pw.chromium.launch(channel="chromium", headless=True,
                                         args=["--no-sandbox"])
            ctx = browser.new_context(viewport=VIEWPORT, locale="en-US")
            return ctx, ctx.new_page()

        def load(use_persona: bool):
            ctx, page = open_ctx(use_persona)
            resp = page.goto(url, wait_until="domcontentloaded", timeout=CAPTURE_NAV_MS)
            page.wait_for_timeout(SETTLE_MS)
            for _ in range(SCROLL_STEPS):
                page.mouse.wheel(0, VIEWPORT["height"])
                page.wait_for_timeout(600)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
            status = resp.status if resp else None
            text = page.inner_text("body")[:200000] if page.query_selector("body") else ""
            low = text[:4000].lower()
            title = (page.title() or "").strip()
            wall = any(p in low for p in LOGIN_WALL)
            blocked = any(b in low for b in BLOCK) or status in (401, 403, 429, 503)
            return ctx, page, status, title, text, wall, blocked

        # Logged out first, unless a specific persona was demanded.
        ctx, page, status, title, text, wall, blocked = load(forced and not anonymous)
        used_persona = persona if (forced and not anonymous) else None
        if wall and not anonymous and not used_persona and persona:
            ctx.close()
            ctx, page, status, title, text, wall, blocked = load(True)
            used_persona = persona

        png = _shot(page, url, when, used_persona)
        html = page.content().encode("utf-8", "replace")
        fields = _profile_fields(page)
        final_url = page.url
        links = _links(page)
        ctx.close()

    return {
        "ok": True, "http_status": status, "title": title, "final_url": final_url,
        "login_wall": wall, "blocked": blocked, "captured_at": when,
        "persona_used": used_persona, "profile": fields, "text": text, "links": links,
        "screenshot_b64": base64.b64encode(png).decode(),
        "html_b64": base64.b64encode(html).decode(),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "session": "ok" if used_persona and not wall else (
            "logged_out" if used_persona and wall else None),
    }


def _shot(page, url: str, when: str, persona: str | None) -> bytes:
    """Full-page PNG with a source banner prepended in the page itself."""
    try:
        page.evaluate(
            """([html]) => {
                const d = document.createElement('div');
                d.innerHTML = html;
                document.body.insertBefore(d.firstChild, document.body.firstChild);
            }""", [_banner(url, when, persona)])
    except Exception:  # noqa: BLE001
        pass
    height = min(page.evaluate("document.documentElement.scrollHeight") or VIEWPORT["height"],
                 MAX_HEIGHT)
    return page.screenshot(full_page=True, clip={"x": 0, "y": 0,
                                                 "width": VIEWPORT["width"], "height": height})


def _links(page) -> list[str]:
    try:
        return page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]'))"
            ".map(a => a.href).filter(h => /^https?:/.test(h)).slice(0, 400)") or []
    except Exception:  # noqa: BLE001
        return []


def login(job: dict) -> dict:
    """Open a visible browser on Kali's desktop so the analyst can sign in."""
    from playwright.sync_api import sync_playwright

    persona = (job.get("persona") or {}).get("name")
    if not persona:
        return {"ok": False, "error": "login job without a persona"}
    os.environ.setdefault("DISPLAY", os.environ.get("OSINT_WORKER_HEADFUL_DISPLAY", ":0"))
    url = _HOME.get((job.get("persona") or {}).get("platform"), "https://example.com")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(persona_profile(persona)), channel="chromium", headless=False,
            viewport=VIEWPORT, locale="en-US",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=CAPTURE_NAV_MS)
        except Exception as e:  # noqa: BLE001
            log("login nav:", e)
        log(f"[login] sign in as persona '{persona}' in the window on Kali's desktop; "
            "close it when done.")
        deadline = time.monotonic() + LOGIN_WAIT_S
        session = "logged_out"
        while time.monotonic() < deadline:
            if not ctx.pages:
                break
            try:
                if any(c["name"].lower() in _SESSION_COOKIES for c in ctx.cookies()):
                    session = "ok"
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(2000)
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": session == "ok", "session": session}


def check(job: dict) -> dict:
    persona = (job.get("persona") or {}).get("name")
    profile = PERSONA_DIR / (persona or "") / "profile"
    if not persona or not profile.exists():
        return {"ok": False, "session": "logged_out"}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(profile), channel="chromium",
                                                    headless=True, args=["--no-sandbox"])
        try:
            signed = any(c["name"].lower() in _SESSION_COOKIES for c in ctx.cookies())
        finally:
            ctx.close()
    return {"ok": True, "session": "ok" if signed else "logged_out"}


_HOME = {"linkedin": "https://www.linkedin.com/login", "facebook": "https://www.facebook.com/",
         "instagram": "https://www.instagram.com/accounts/login/",
         "x": "https://x.com/login", "tiktok": "https://www.tiktok.com/login",
         "youtube": "https://accounts.google.com/", "threads": "https://www.threads.net/login",
         "reddit": "https://www.reddit.com/login/"}
_SESSION_COOKIES = {"li_at", "c_user", "xs", "sessionid", "auth_token", "sid",
                    "sessionid_ss", "__secure-3psid", "reddit_session", "token_v2"}


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def sessions_summary() -> dict:
    out = {}
    if PERSONA_DIR.exists():
        for d in PERSONA_DIR.iterdir():
            if (d / "profile").exists():
                cookies = d / "profile" / "Default" / "Cookies"
                out[d.name] = "ok" if cookies.exists() else "logged_out"
    return out


def do_job(job: dict) -> dict:
    kind = job.get("kind")
    region = (job.get("persona") or {}).get("region")
    status = mullvad_status()
    if kind in ("capture", "check") and require_vpn() and not status.get("connected"):
        return {"ok": False, "error": "Mullvad VPN is not connected; refusing to capture",
                "egress": egress()}
    if job.get("persona") and region:
        if not region_ok(region, status):
            mullvad_set_location(region)
            time.sleep(3)
            status = mullvad_status()
        if require_vpn() and not region_ok(region, status):
            return {"ok": False, "error": f"VPN exit is not in persona region '{region}'",
                    "egress": egress()}
    try:
        if kind == "capture":
            result = capture(job)
        elif kind == "login":
            result = login(job)
        elif kind == "check":
            result = check(job)
        else:
            return {"ok": False, "error": f"unknown job kind: {kind}"}
    except Exception as e:  # noqa: BLE001 - report any failure as a failed job
        log("job error:", repr(e))
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:400], "egress": egress()}
    result.setdefault("egress", egress())
    return result


def run(platform: Platform) -> None:
    log(f"osint social worker {VERSION} → {platform.base}; personas in {PERSONA_DIR}")
    last_beat = 0.0
    while True:
        try:
            if time.monotonic() - last_beat > HEARTBEAT_EVERY:
                st = mullvad_status()
                platform.heartbeat({"version": VERSION, "vpn": st,
                                    "egress": egress() if st.get("connected") else {},
                                    "sessions": sessions_summary(), "busy": False})
                last_beat = time.monotonic()
            job = platform.next_job()
            if not job:
                continue
            log(f"job {job['id']} {job['kind']} {job.get('url') or ''} "
                f"persona={(job.get('persona') or {}).get('name')}")
            platform.heartbeat({"version": VERSION, "busy": True,
                               "sessions": sessions_summary()})
            result = do_job(job)
            platform.complete(job["id"], result)
            last_beat = 0.0
            log(f"job {job['id']} done ok={result.get('ok')} "
                f"wall={result.get('login_wall')} err={result.get('error')}")
        except error.HTTPError as e:
            log("platform HTTP", e.code, e.reason)
            time.sleep(10)
        except (error.URLError, OSError) as e:
            log("platform unreachable:", e, "- retrying in 15s")
            time.sleep(15)
        except KeyboardInterrupt:
            log("bye")
            return


def main() -> int:
    ap = argparse.ArgumentParser(description="OSINT social capture worker (runs on Kali)")
    ap.add_argument("--url", default=os.environ.get("OSINT_PLATFORM_URL"),
                    help="platform base URL, e.g. https://osint.dumisoft.ro")
    ap.add_argument("--token", default=os.environ.get("OSINT_WORKER_TOKEN"))
    ap.add_argument("--basic", default=os.environ.get("OSINT_BASIC_AUTH"),
                    help="user:pass for the platform's basic auth, if any")
    ap.add_argument("--once", action="store_true", help="run one poll cycle and exit (test)")
    args = ap.parse_args()
    if not args.url or not args.token:
        ap.error("set --url/--token or OSINT_PLATFORM_URL/OSINT_WORKER_TOKEN")
    platform = Platform(args.url, args.token, args.basic)
    if args.once:
        job = platform.next_job()
        print(json.dumps(do_job(job) if job else {"job": None}, default=str)[:2000])
        return 0
    run(platform)
    return 0


if __name__ == "__main__":
    sys.exit(main())
