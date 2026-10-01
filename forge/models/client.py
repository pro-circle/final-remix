"""OpenAI-compatible chat clients (Groq, Gemini) with key rotation, retries, token
accounting, and a FleetClient that routes each request to the provider serving the model."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from forge.config import Config, provider_for, redact
from forge.models.key_manager import KeyManager, ModelUnavailable  # noqa: F401 (re-exported)


class BudgetExceeded(RuntimeError):
    pass


class ModelError(RuntimeError):
    pass


class UpstreamUnavailable(ModelError):
    """The provider itself is failing (HTTP 5xx / network). Another model may still work."""



class RequestTooLarge(ModelError):
    """One request exceeded the per-minute token ceiling (HTTP 413)."""

    def __init__(self, message: str, limit: int | None, requested: int | None) -> None:
        super().__init__(message)
        self.limit = limit
        self.requested = requested


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    requests: int = 0
    by_model: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass
class Completion:
    content: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str
    model: str
    raw: dict[str, Any]


class GroqClient:
    """One provider's client. Named GroqClient for history; also serves Gemini."""

    def __init__(
        self,
        cfg: Config,
        keys: KeyManager | None = None,
        provider: str = "groq",
        usage: Usage | None = None,
    ) -> None:
        self.cfg = cfg
        self.provider = provider
        self.name = "Gemini" if provider == "gemini" else "Groq"
        self.base_url = cfg.base_url_for(provider)
        self.keys = keys or KeyManager(cfg.keys_for(provider), provider, cfg.key_models)
        self.usage = usage or Usage()
        self._client = httpx.Client(timeout=httpx.Timeout(180.0, connect=15.0))

    def close(self) -> None:
        self._client.close()

    def check_budget(self) -> None:
        if self.usage.total >= self.cfg.budget.max_tokens_per_run:
            raise BudgetExceeded(
                f"Token budget reached ({self.usage.total} tokens). Raise budget.max_tokens_per_run to continue."
            )

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_attempts: int = 6,
    ) -> Completion:
        self.check_budget()
        payload: dict[str, Any] = {
            "model": model,
            "messages": _prepare_messages(messages, self.provider),
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            if self.provider == "groq":  # Gemini's compat layer rejects this field
                payload["parallel_tool_calls"] = False
        need = _estimate(messages)
        tpm, rpm = self.cfg.tpm_limit(model), self.cfg.rpm_limit(model)

        last_error = ""
        attempt = 0
        # A provider outage (5xx / network) must not eat the clock: try each key at most
        # once, briefly, then hand back so the router can move to the next model/provider.
        server_failures = 0
        server_budget = max(2, len(self.keys.states))
        rate_limit_deadline = time.time() + 600  # keep waiting out rate limits for up to 10 min
        while attempt < max_attempts:
            state = self.keys.acquire(model, need, tpm, rpm)
            try:
                response = self._client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {state.key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.HTTPError as exc:
                last_error = f"network error: {exc}"
                self.keys.report_failure(state, last_error)
                server_failures += 1
                if server_failures >= server_budget:
                    raise UpstreamUnavailable(f"{self.name} unreachable: {last_error}")
                time.sleep(min(1.5, 0.4 * server_failures) + random.random() * 0.2)
                attempt += 1
                continue


            if response.status_code in (400, 401, 403) and (
                "API_KEY_INVALID" in response.text or "API key not valid" in response.text
            ):
                self.keys.report_invalid(state, "key rejected")
                last_error = "key rejected"
                attempt += 1
                continue

            if _model_unavailable(response):
                # This key can't call this model (new keys lose retired models): learn it and
                # try another key; KeyManager raises ModelUnavailable once none remain.
                self.keys.report_unsupported(state, model)
                last_error = f"{model} not available for this key"
                continue

            if response.status_code == 429:
                self.keys.record(state, model, tpm)  # treat the bucket as full for a minute
                retry_after = response.headers.get("retry-after")
                self.keys.report_rate_limit(
                    state, float(retry_after) if retry_after else None
                )
                last_error = "rate limited"
                if time.time() < rate_limit_deadline:
                    continue  # wait and retry; rate limits don't use up attempts
                attempt += 1
                continue

            if response.status_code in (401, 403):
                self.keys.report_invalid(state, f"auth {response.status_code}")
                last_error = f"key rejected ({response.status_code})"
                attempt += 1
                continue

            if response.status_code >= 500:
                self.keys.report_failure(state, f"server {response.status_code}")
                last_error = f"upstream {response.status_code}"
                server_failures += 1
                if server_failures >= server_budget:
                    # The provider is down for this model right now: fail fast so the
                    # orchestrator can fall back to the next model (and provider).
                    raise UpstreamUnavailable(f"{self.name} {last_error} for {model}")
                time.sleep(min(1.5, 0.4 * server_failures) + random.random() * 0.2)
                attempt += 1
                continue


            if (
                response.status_code == 400
                and "tool_use_failed" in response.text
                and attempt < max_attempts - 1
            ):
                # The model produced a malformed tool call; sampling again usually fixes it.
                self.keys.report_success(state)
                last_error = "malformed tool call"
                attempt += 1
                continue

            if response.status_code == 413 or (
                response.status_code == 400 and "Request too large" in response.text
            ):
                import re

                self.keys.report_success(state)
                lim = re.search(r"Limit (\d+)", response.text)
                req = re.search(r"Requested (\d+)", response.text)
                raise RequestTooLarge(
                    "request larger than the per-minute token limit",
                    int(lim.group(1)) if lim else None,
                    int(req.group(1)) if req else None,
                )

            if response.status_code >= 400:
                detail = redact(response.text[:800], self.cfg)
                raise ModelError(f"{self.name} rejected the request ({response.status_code}): {detail}")

            self.keys.report_success(state)
            data = response.json()
            usage = data.get("usage") or {}
            self.usage.requests += 1
            self.usage.tokens_in += int(usage.get("prompt_tokens") or 0)
            self.usage.tokens_out += int(usage.get("completion_tokens") or 0)
            self.keys.record(state, model, int(usage.get("total_tokens") or need))
            self.usage.by_model[model] = self.usage.by_model.get(model, 0) + int(
                usage.get("total_tokens") or 0
            )
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            return Completion(
                content=message.get("content") or "",
                tool_calls=_normalise_tool_calls(message.get("tool_calls")),
                finish_reason=choice.get("finish_reason") or "stop",
                model=model,
                raw=data,
            )

        message = f"All {self.name} attempts failed: {last_error}"
        if last_error.startswith(("upstream", "network", "rate limited")):
            raise UpstreamUnavailable(message)  # recoverable: try another model/provider
        raise ModelError(message)


    # Capacity queries used by the scheduler.
    def serves(self, model: str) -> bool:
        return self.keys.serves(model)

    def has_capacity(self, model: str, need: int) -> bool:
        return self.keys.headroom(model, need, self.cfg.tpm_limit(model), self.cfg.rpm_limit(model))

    def wait_estimate(self, model: str, need: int) -> float:
        return self.keys.wait_estimate(model, need, self.cfg.tpm_limit(model), self.cfg.rpm_limit(model))


class FleetClient:
    """Routes each request to the provider that serves the model; shares one Usage."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.usage = Usage()
        self.providers: dict[str, GroqClient] = {
            name: GroqClient(cfg, provider=name, usage=self.usage)
            for name in ("groq", "gemini")
            if cfg.keys_for(name)
        }

    @property
    def keys(self) -> "_FleetKeys":
        return _FleetKeys(self)

    def close(self) -> None:
        for client in self.providers.values():
            client.close()

    def check_budget(self) -> None:
        if self.usage.total >= self.cfg.budget.max_tokens_per_run:
            raise BudgetExceeded(
                f"Token budget reached ({self.usage.total} tokens). Raise budget.max_tokens_per_run to continue."
            )

    def _for(self, model: str) -> GroqClient:
        client = self.providers.get(provider_for(model))
        if client is None:
            raise ModelError(f"No {provider_for(model)} keys configured for {model}.")
        return client

    def chat(self, *, model: str, **kwargs: Any) -> Completion:
        self.check_budget()
        return self._for(model).chat(model=model, **kwargs)

    def serves(self, model: str) -> bool:
        client = self.providers.get(provider_for(model))
        return client is not None and client.serves(model)

    def has_capacity(self, model: str, need: int) -> bool:
        return self.serves(model) and self._for(model).has_capacity(model, need)

    def wait_estimate(self, model: str, need: int) -> float:
        return self._for(model).wait_estimate(model, need) if self.serves(model) else float("inf")


class _FleetKeys:
    def __init__(self, fleet: FleetClient) -> None:
        self.fleet = fleet

    def health(self) -> list[dict[str, object]]:
        return [row for c in self.fleet.providers.values() for row in c.keys.health()]

    def __len__(self) -> int:
        return sum(len(c.keys) for c in self.fleet.providers.values())


def _model_unavailable(response: httpx.Response) -> bool:
    if response.status_code == 404:
        return True
    if response.status_code == 400:
        text = response.text.lower()
        return "model" in text and ("not found" in text or "no longer available" in text or "not supported" in text)
    return False


def _estimate(messages: list[dict[str, Any]]) -> int:
    from forge.context.builder import messages_tokens

    return messages_tokens(messages) + 1_000  # prompt plus a typical reply


def _normalise_tool_calls(raw: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in raw or []:
        fn = item.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                parsed = json.loads(args or "{}")
            except json.JSONDecodeError:
                parsed = {"_raw": args}
        else:
            parsed = args or {}
        call: dict[str, Any] = {
            "id": item.get("id") or f"call_{len(calls)}",
            "name": fn.get("name") or "",
            "arguments": parsed,
        }
        if item.get("extra_content"):
            call["extra_content"] = item["extra_content"]  # carries Gemini thought_signature
        calls.append(call)
    return calls


# Google's documented placeholder for function calls that no Gemini model produced
# (e.g. made by Groq after a failover); it tells Gemini to skip signature validation.
_SKIP_SIGNATURE = "skip_thought_signature_validator"


def _prepare_messages(messages: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
    """Adapt tool-call history for the target provider.

    Gemini: the first tool call of each assistant turn must carry a thought signature;
    calls without one (from another provider) get Google's skip placeholder.
    Others: drop the Gemini-only extra_content field.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        calls = message.get("tool_calls")
        if message.get("role") != "assistant" or not calls:
            out.append(message)
            continue
        if provider == "gemini":
            fixed = [dict(c) for c in calls]
            if not any(_signature(c) for c in fixed):
                fixed[0]["extra_content"] = {"google": {"thought_signature": _SKIP_SIGNATURE}}
        else:
            fixed = [{k: v for k, v in c.items() if k != "extra_content"} for c in calls]
        out.append({**message, "tool_calls": fixed})
    return out


def _signature(call: dict[str, Any]) -> str | None:
    extra = call.get("extra_content") or {}
    google = extra.get("google") if isinstance(extra, dict) else None
    return google.get("thought_signature") if isinstance(google, dict) else None
