"""Wires the long-lived services together; one instance per app process."""

from __future__ import annotations

from .config import Settings, load_settings
from .events import EventBus
from .ledger import Ledger
from .models import Database
from .scan import QuickScan


class Platform:
    def __init__(self, settings: Settings | None = None, db_path: str | None = None):
        self.settings = settings or load_settings()
        self.db = Database(db_path or self.settings.db_path)
        self.bus = EventBus()
        self.ledger = Ledger(self.db, self.bus, self.settings)
        self.resolver = self.ledger.resolver
        self.scan = QuickScan(self.ledger)

    def close(self) -> None:
        for task in list(self.scan.tasks.values()):
            task.cancel()
        self.db.close()
