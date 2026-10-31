"""Wires the long-lived services together; one instance per app process."""

from __future__ import annotations

from typing import Callable

from .agent import AgentRunner
from .agent.catalog import build_model, is_anthropic, TASK_KEYS
from .agent.model import AnthropicModel, ModelClient, configured, venice_configured
from .config import Settings, load_settings
from .events import EventBus
from .ledger import Ledger
from .models import Database
from .scan import QuickScan
from .social import SocialHub


class Platform:
    def __init__(self, settings: Settings | None = None, db_path: str | None = None,
                 model_factory: Callable[[], ModelClient] | None = None):
        self.settings = settings or load_settings()
        self.db = Database(db_path or self.settings.db_path)
        self.bus = EventBus()
        self.ledger = Ledger(self.db, self.bus, self.settings)
        self.resolver = self.ledger.resolver
        self.scan = QuickScan(self.ledger)
        # Persona captures through the Kali worker (app/social.py).
        self.social = SocialHub(self.ledger)
        self.agents = AgentRunner(
            self.ledger,
            model_factory or (lambda: AnthropicModel(
                self.settings.agent_model, self.settings.agent_progress_updates)),
            None if model_factory else (lambda: AnthropicModel(
                self.settings.worker_model, self.settings.agent_progress_updates)),
            model_resolver=None if model_factory else self._resolve_model)
        self.agents.model_factory_is_default = model_factory is None
        self.agents.social = self.social

    # --- model assignments (Models settings page) -------------------------------

    def default_model(self, kind: str) -> str:
        """The built-in model for a task when the analyst hasn't assigned one."""
        return self.settings.worker_model if kind == "worker" else self.settings.agent_model

    def assigned_model(self, kind: str) -> str:
        """The model id chosen for a task, read fresh so UI changes take effect
        on the next run without a restart."""
        saved = self.db.get_setting("model_assignments", {}) or {}
        chosen = saved.get(kind)
        return chosen if isinstance(chosen, str) and chosen else self.default_model(kind)

    def model_error(self, model_id: str) -> str | None:
        """Why a model id can't run here, or ``None`` if it can."""
        if is_anthropic(model_id):
            return None if configured() else (
                "Claude models need ANTHROPIC_API_KEY on the server.")
        return None if venice_configured() else (
            f"{model_id} is a Venice model; set VENICE_INFERENCE_KEY on the server.")

    def _resolve_model(self, kind: str, override: str | None = None) -> ModelClient:
        model_id = override or self.assigned_model(kind)
        return build_model(model_id, progress_updates=self.settings.agent_progress_updates,
                           venice_api_key=self.settings.venice_api_key,
                           venice_base_url=self.settings.venice_base_url)

    def model_assignments(self) -> dict[str, str]:
        return {k: self.assigned_model(k) for k in TASK_KEYS}

    def close(self) -> None:
        self.social.stop()
        for task in [*self.scan.tasks.values(), *self.agents.tasks.values()]:
            task.cancel()
        self.db.close()
