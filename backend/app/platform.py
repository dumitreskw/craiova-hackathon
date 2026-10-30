"""Wires the long-lived services together; one instance per app process."""

from __future__ import annotations

from typing import Callable

from .agent import AgentRunner
from .agent.model import AnthropicModel, ModelClient
from .config import Settings, load_settings
from .events import EventBus
from .ledger import Ledger
from .models import Database
from .scan import QuickScan


class Platform:
    def __init__(self, settings: Settings | None = None, db_path: str | None = None,
                 model_factory: Callable[[], ModelClient] | None = None):
        self.settings = settings or load_settings()
        self.db = Database(db_path or self.settings.db_path)
        self.bus = EventBus()
        self.ledger = Ledger(self.db, self.bus, self.settings)
        self.resolver = self.ledger.resolver
        self.scan = QuickScan(self.ledger)
        self.agents = AgentRunner(
            self.ledger,
            model_factory or (lambda: AnthropicModel(
                self.settings.agent_model, self.settings.agent_progress_updates)),
            None if model_factory else (lambda: AnthropicModel(
                self.settings.worker_model, self.settings.agent_progress_updates)))
        self.agents.model_factory_is_default = model_factory is None

    def close(self) -> None:
        for task in [*self.scan.tasks.values(), *self.agents.tasks.values()]:
            task.cancel()
        self.db.close()
