"""Runtime settings, read from the environment once at import."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    # Default evidence lifetime (docs/06-legal-ethics.md, principle 4).
    retention_days: int
    # Hard cap on connector calls in one quick scan, so a pivot can't run away.
    max_calls_per_scan: int
    # How many new entities of one type a single pivot round may expand.
    max_pivots_per_type: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "osint.sqlite"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("OSINT_DATA_DIR", BACKEND_DIR / "data")),
        retention_days=int(os.environ.get("OSINT_RETENTION_DAYS", "90")),
        max_calls_per_scan=int(os.environ.get("OSINT_MAX_CALLS_PER_SCAN", "60")),
        max_pivots_per_type=int(os.environ.get("OSINT_MAX_PIVOTS_PER_TYPE", "10")),
    )
