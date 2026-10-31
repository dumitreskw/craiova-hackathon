"""The build this server runs: the first 8 characters of its git commit.

Looked up once, in order: ``OSINT_BUILD`` (set by hand or by CI), the
``BUILD`` file the Docker image writes next to the code, then ``git`` in a
source checkout. ``dev`` when none of them answers.
"""

from __future__ import annotations

import os
import subprocess
from functools import cache

from .config import BACKEND_DIR

LENGTH = 8


@cache
def build_id() -> str:
    value = os.environ.get("OSINT_BUILD", "").strip()
    if not value:
        path = BACKEND_DIR / "BUILD"
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
    if not value:
        try:
            value = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BACKEND_DIR, text=True,
                                   capture_output=True, timeout=5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            value = ""
    return value[:LENGTH] or "dev"
