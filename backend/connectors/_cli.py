"""Run an OSINT command-line tool (subfinder, dnstwist, holehe, maigret, lightpanda).

Resolution order for a tool:

1. ``OSINT_<TOOL>_CMD`` — an explicit command prefix, e.g.
   ``OSINT_LIGHTPANDA_CMD="docker run --rm lightpanda/browser:latest /bin/lightpanda"``;
2. the binary on ``PATH`` (the Docker image ships them);
3. ``OSINT_KALI_SSH=<ssh host alias>`` — run it on the Kali VM over ssh, so a
   laptop without the tools can still use the ones already set up there.

Arguments are always passed as a list (no shell on our side); for ssh each one
is quoted for the remote shell. Inputs reach here already validated.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil


class ToolUnavailable(RuntimeError):
    pass


def resolve(tool: str) -> list[str]:
    override = os.environ.get(f"OSINT_{tool.upper().replace('-', '_')}_CMD")
    if override:
        return shlex.split(override, posix=True)
    path = shutil.which(tool)
    if path:
        return [path]
    host = os.environ.get("OSINT_KALI_SSH")
    if host:
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, "--", tool]
    raise ToolUnavailable(f"{tool} is not installed here and OSINT_KALI_SSH is not set")


def available(tool: str) -> bool:
    try:
        resolve(tool)
        return True
    except ToolUnavailable:
        return False


async def run(tool: str, args: list[str], timeout: float = 120,
              env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run ``tool args...``; return ``(returncode, stdout, stderr)``. Kills on timeout."""
    prefix = resolve(tool)
    if prefix[0] == "ssh":
        # ssh joins argv into one remote command line: quote every argument.
        # Non-interactive shells skip the profile, so add the pipx/user bin dirs.
        argv = prefix[:-1] + ['PATH="$HOME/.local/bin:/usr/local/bin:$PATH" '
                              + " ".join(shlex.quote(a) for a in [prefix[-1], *args])]
    else:
        argv = prefix + args
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL, env={**os.environ, **(env or {})})
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
