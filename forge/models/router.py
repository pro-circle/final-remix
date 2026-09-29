"""Model routing: cheap model for triage, big model for reasoning, vision for images."""

from __future__ import annotations

from forge.config import Config

# Phase -> config role
PHASE_ROLE = {
    "explore": "fast",
    "plan": "deep",
    "code": "deep",
    "debug": "deep",
    "test": "fast",
    "review": "fast",
    "summarise": "fast",
    "inspect": "deep",
    "visual": "vision",
}


class ModelRouter:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def model_for_phase(self, phase: str) -> str:
        return self.cfg.model_for(PHASE_ROLE.get(phase, "deep"))

    def context_budget(self, phase: str) -> int:
        """Usable prompt tokens per request: clamped below the model's TPM and context window."""
        return self.cfg.request_budget(self.model_for_phase(phase))
