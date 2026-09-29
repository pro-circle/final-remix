"""Five-key manager: rotation, health tracking and rate-limit backoff."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

WINDOW_SECONDS = 60.0


@dataclass
class KeyState:
    key: str
    label: str
    successes: int = 0
    failures: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    dead: bool = False  # rejected by the provider (401/403): never used again this session
    # Models this key may call. None = any model (until one is detected as unsupported).
    models: frozenset[str] | None = None
    # Models the provider told us this key can't use (404 / "not available"), learned live.
    unsupported: set[str] = field(default_factory=set)
    # Per-model sliding window of (timestamp, tokens) for the last minute of requests.
    window: dict[str, deque] = field(default_factory=dict)

    def used(self, model: str, now: float | None = None) -> tuple[int, int]:
        """(tokens, requests) sent for this model in the last minute."""
        now = now or time.time()
        q = self.window.get(model)
        if not q:
            return 0, 0
        while q and now - q[0][0] >= WINDOW_SECONDS:
            q.popleft()
        return sum(t for _, t in q), len(q)

    def fits(self, model: str, need: int, tpm: int, rpm: int) -> bool:
        tokens, reqs = self.used(model)
        return reqs < rpm and (tokens + need <= tpm or tokens == 0)

    def wait_for(self, model: str, need: int, tpm: int, rpm: int) -> float:
        """Seconds until this key could take the request (0 if now)."""
        now = time.time()
        base = max(0.0, self.cooldown_until - now)
        if self.fits(model, need, tpm, rpm):
            return base
        q = list(self.window.get(model) or [])
        tokens, reqs = self.used(model, now)
        for ts, t in q:  # oldest first: wait until enough of the window expires
            tokens -= t
            reqs -= 1
            if reqs < rpm and (tokens + need <= tpm or tokens <= 0):
                return max(base, ts + WINDOW_SECONDS - now)
        return max(base, WINDOW_SECONDS)

    def supports(self, model: str | None) -> bool:
        if not model:
            return True
        if model in self.unsupported:
            return False
        return self.models is None or model in self.models

    @property
    def available(self) -> bool:
        return time.time() >= self.cooldown_until

    @property
    def healthy(self) -> bool:
        return not self.dead and self.available and self.failures < 5


class NoKeysAvailable(RuntimeError):
    pass


class ModelUnavailable(NoKeysAvailable):
    """No key of this provider can call the requested model (declared or detected)."""


class KeyManager:
    """Round-robin over up to five keys, skipping ones in cooldown or lacking the model."""

    def __init__(
        self, keys: list[str], provider: str = "", key_models: dict[str, list[str]] | None = None
    ) -> None:
        self.provider = provider
        unique: list[str] = []
        for key in keys:
            if key and key not in unique:
                unique.append(key)
        self.states = [
            KeyState(
                key=key,
                label=f"{provider + ':' if provider else ''}key{i + 1}:…{key[-4:]}",
                models=frozenset(key_models[key]) if key_models and key_models.get(key) else None,
            )
            for i, key in enumerate(unique[:5])
        ]
        self._cursor = 0

    def __len__(self) -> int:
        return len(self.states)

    def serves(self, model: str) -> bool:
        """At least one live key can call this model."""
        return any(not s.dead and s.supports(model) for s in self.states)

    def headroom(self, model: str, need: int, tpm: int, rpm: int) -> bool:
        return any(s.healthy and s.supports(model) and s.fits(model, need, tpm, rpm) for s in self.states)

    def wait_estimate(self, model: str, need: int, tpm: int, rpm: int) -> float:
        alive = [s for s in self.states if not s.dead and s.supports(model)]
        if not alive:
            return float("inf")
        return min(s.wait_for(model, need, tpm, rpm) for s in alive)

    def record(self, state: KeyState, model: str, tokens: int) -> None:
        state.window.setdefault(model, deque()).append((time.time(), max(1, int(tokens))))

    def acquire(self, model: str | None = None, need: int = 0, tpm: int = 0, rpm: int = 0) -> KeyState:
        if not self.states:
            raise NoKeysAvailable(f"No {self.provider or 'Groq'} API keys configured. Add them to .env.")
        count = len(self.states)
        if model and not self.serves(model):
            raise ModelUnavailable(f"No {self.provider or 'Groq'} key can use {model}.")
        if model and tpm and rpm:
            # Prefer a key whose own org bucket has room for this request right now.
            for offset in range(count):
                state = self.states[(self._cursor + offset) % count]
                if state.healthy and state.supports(model) and state.fits(model, need, tpm, rpm):
                    self._cursor = (self._cursor + offset + 1) % count
                    return state
        for offset in range(count):
            state = self.states[(self._cursor + offset) % count]
            if state.healthy and state.supports(model):
                self._cursor = (self._cursor + offset + 1) % count
                return state
        alive = [s for s in self.states if not s.dead and s.supports(model)]
        if not alive:
            raise NoKeysAvailable(f"Every {self.provider or 'Groq'} key was rejected. Check the keys in .env.")
        # Everything is cooling down: wait for the soonest one.
        soonest = min(alive, key=lambda s: s.cooldown_until)
        wait = max(0.0, soonest.cooldown_until - time.time())
        if wait > 0:
            time.sleep(min(wait, 30))
        soonest.cooldown_until = 0.0
        return soonest

    def report_success(self, state: KeyState) -> None:
        state.successes += 1
        state.failures = 0
        state.last_error = ""

    def report_rate_limit(self, state: KeyState, retry_after: float | None) -> None:
        state.cooldown_until = time.time() + (retry_after if retry_after else 20.0)
        state.last_error = "rate limited"

    def report_failure(self, state: KeyState, error: str) -> None:
        state.failures += 1
        state.last_error = error
        state.cooldown_until = time.time() + min(60.0, 2.0**state.failures)

    def report_unsupported(self, state: KeyState, model: str) -> None:
        """The provider says this key can't call this model: stop routing it there."""
        state.unsupported.add(model)
        state.last_error = f"{model} not available"

    def report_invalid(self, state: KeyState, error: str) -> None:
        state.dead = True
        state.failures += 1
        state.last_error = error

    def health(self) -> list[dict[str, object]]:
        now = time.time()
        return [
            {
                "label": s.label,
                "provider": self.provider or "groq",
                "successes": s.successes,
                "failures": s.failures,
                "cooldown_s": max(0, round(s.cooldown_until - now)),
                "last_error": s.last_error,
                "models": sorted(s.models) if s.models is not None else "any",
                "unsupported": sorted(s.unsupported),
            }
            for s in self.states
        ]
