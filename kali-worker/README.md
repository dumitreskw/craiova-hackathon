# OSINT social worker (Kali)

Serves the platform's social-media captures using research accounts
("personas") whose logged-in browser profiles live on the Kali VM. The
platform runs on the VPS and **cannot reach Kali**, so this worker connects
*out* to the platform, long-polls for jobs, runs them with Playwright, and
posts results back. Session cookies never leave this machine — the platform
only stores each persona's name, platform, region and health.

**Read-only.** It loads, scrolls and screenshots public profiles. It never
follows, likes, comments, messages or joins anything, and never creates
accounts. Only profiles already in an investigation's graph can be captured,
and every capture needs the Mullvad VPN up (in the persona's region).

## Install (on Kali)

```bash
sudo apt-get update && sudo apt-get install -y nodejs   # Playwright's driver needs Node
mkdir -p ~/osint-worker && cd ~/osint-worker
cp /path/to/repo/kali-worker/worker.py .
python3 -m venv .venv && . .venv/bin/activate
pip install playwright requests
playwright install chromium            # self-contained browser for this venv
#   To use a system Chromium instead, skip the download and set
#   OSINT_CHROMIUM_CHANNEL=chromium in the env file.

cp /path/to/repo/kali-worker/osint-worker.env.example osint-worker.env
# edit osint-worker.env: OSINT_PLATFORM_URL, OSINT_WORKER_TOKEN
```

On the platform set the matching secret: `OSINT_WORKER_TOKEN` (same value) in
the Coolify env. Without it the `/worker/*` API stays off.

Run it:

```bash
. .venv/bin/activate && python worker.py          # foreground
# or install the service:
sudo cp osint-worker.service /etc/systemd/system/
sudo systemctl enable --now osint-worker
```

Confirm the platform sees it: the **Personas** panel shows "worker online"
with the current VPN exit, or `GET /api/v1/social/status` returns
`"online": true`.

## One-time persona login

1. Create the persona in the UI (Personas → New): name, platform, Mullvad
   region (e.g. `de-fra`). It starts as **needs login**.
2. Click **Log in** on the persona. The worker sets Mullvad to that region and
   opens a **visible** Chromium on Kali's desktop (display `:0` — use the VM
   console or a VNC session).
3. Sign in by hand, clear any 2FA/checkpoint, then close the window. The
   session saves to `~/personas/<name>/profile`. The persona goes **ok**.

If a later capture hits a checkpoint the persona flips back to **needs login**
and the UI asks you to repeat this.

## VPN

Captures are refused unless `mullvad status` is *Connected* (disable only with
`OSINT_REQUIRE_VPN=0`, not recommended). For a persona the worker also checks
the exit is in the persona's country and re-points Mullvad if not, so a
persona is never seen from the wrong IP. Turn on Mullvad lockdown mode
(`mullvad lockdown-mode set on`) so nothing leaks if the tunnel drops.

## Notes & limits

- Platforms change their markup constantly; profile-field extraction
  (followers, bio …) is best-effort and the screenshot + saved HTML are the
  real evidence.
- Using research accounts is against most platforms' terms of service; accounts
  get rate-limited or banned. Keep volume low (the platform caps captures per
  persona per hour) and treat any persona as disposable.
- `python worker.py --once` runs a single poll cycle and prints the result —
  handy for checking connectivity and a persona end to end.
