"""Model routing.

Each phase has a preference chain of models (see config.DEFAULT_ROUTING). The router
walks that chain and picks the first model whose provider has a key with tokens- and
requests-per-minute headroom right now. If every candidate is saturated it picks the
one that frees up soonest, so a run spills from Groq to Gemini (and back) instead of
stalling on a single rate-limited model.
"""

from __future__ import annotations

from typing import Any, Protocol

from forge.config import Config

# Phase -> config role. Used as the fallback when no routing chain applies.
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


class CapacityClient(Protocol):
    def serves(self, model: str) -> bool: ...
    def has_capacity(self, model: str, need: int) -> bool: ...
    def wait_estimate(self, model: str, need: int) -> float: ...


def _capacity_aware(client: Any) -> bool:
    return all(hasattr(client, name) for name in ("serves", "has_capacity", "wait_estimate"))


class ModelRouter:
    def __init__(self, cfg: Config, client: Any | None = None) -> None:
        self.cfg = cfg
        self.client = client

    # --------------------------------------------------------------- choosing
    def model_for_phase(self, phase: str) -> str:
        """Static choice: first candidate for the phase (provider must have keys)."""
        candidates = self.cfg.candidates(phase, PHASE_ROLE.get(phase, "deep"))
        return candidates[0] if candidates else self.cfg.model_for(PHASE_ROLE.get(phase, "deep"))

    def choose(
        self,
        phase: str,
        need: int = 0,
        client: Any | None = None,
        exclude: set[str] | None = None,
    ) -> str:
        """Pick the best model for this phase given live per-key capacity.

        `exclude` drops models that just failed upstream (e.g. a provider 503), so the
        run slides down the chain to the next provider instead of retrying a dead one.
        """
        client = client if client is not None else self.client
        blocked = exclude or set()
        candidates = [m for m in self.cfg.candidates(phase, PHASE_ROLE.get(phase, "deep")) if m not in blocked]
        if not candidates:
            fallback = self.cfg.model_for(PHASE_ROLE.get(phase, "deep"))
            return fallback
        if client is None or not _capacity_aware(client):
            return candidates[0]

        servable = [m for m in candidates if client.serves(m)]
        if not servable:
            return candidates[0]
        for model in servable:
            # The prompt is fitted to each model's own budget, so ask for what would fit there.
            if client.has_capacity(model, min(need, self.context_budget_for(model)) if need else 0):
                return model
        # Everything is saturated: take whichever frees up first (ties keep chain order).
        return min(
            servable,
            key=lambda m: (client.wait_estimate(m, min(need, self.context_budget_for(m)) if need else 0), servable.index(m)),
        )


    # --------------------------------------------------------------- budgets
    def context_budget_for(self, model: str) -> int:
        return self.cfg.request_budget(model)

    def context_budget(self, phase: str) -> int:
        """Usable prompt tokens per request: clamped below the model's TPM and context window."""
        return self.cfg.request_budget(self.model_for_phase(phase))
