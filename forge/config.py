"""Forge configuration: models, keys, budgets, approval policy.

Config lives at ~/.forge/config.toml and is never read from the project repo,
so keys can't be committed by accident. Environment variables win over the file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HOME_DIR = Path(os.environ.get("FORGE_HOME") or (Path.home() / ".forge"))
CONFIG_PATH = HOME_DIR / "config.toml"
DB_PATH = HOME_DIR / "forge.db"
CHECKPOINT_DIR = HOME_DIR / "checkpoints"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Google AI Studio's OpenAI-compatible endpoint (same request/response shape as Groq).
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

# Gemini 2.5 models are retired for new API keys; 3.8-flash is Google's recommended
# successor, 3.5-flash-lite the cheaper fallback (verified live with new-format keys).
GEMINI_FLASH = "gemini-3.8-flash"
GEMINI_FLASH_LITE = "gemini-3.5-flash-lite"
PROVIDER_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}
MAX_KEYS_PER_PROVIDER = 5


def provider_for(model: str) -> str:
    """Which provider serves a model. Gemini models are named gemini-*; everything else is Groq."""
    return "gemini" if model.startswith("gemini") else "groq"

# Context windows (tokens) as published by Groq for these models.
MODEL_CONTEXT = {
    "openai/gpt-oss-120b": 131072,
    "openai/gpt-oss-20b": 131072,
    "qwen/qwen3.8-27b": 131072,
    GEMINI_FLASH: 1_048_576,
    GEMINI_FLASH_LITE: 1_048_576,
}

# Groq free-tier tokens-per-minute ceilings. A single request larger than this is
# rejected outright (HTTP 413), so each prompt is clamped below it. Override in
# config.toml under [limits] (e.g. `tpm = { "openai/gpt-oss-20b" = 250000 }`) after upgrading.
MODEL_TPM = {
    "openai/gpt-oss-120b": 8000,
    "openai/gpt-oss-20b": 8000,
    "qwen/qwen3.8-27b": 6000,
    "llama-3.1-8b-instant": 6000,
    "llama-3.3-70b-versatile": 12000,
    GEMINI_FLASH: 250_000,
    GEMINI_FLASH_LITE: 250_000,
}
DEFAULT_TPM = 6000

# Free-tier requests-per-minute, per key (each key is a separate org with its own bucket).
# Override in config.toml: [limits] rpm = { "gemini-2.5-flash" = 10 }
MODEL_RPM = {
    "openai/gpt-oss-120b": 30,
    "openai/gpt-oss-20b": 30,
    "qwen/qwen3.8-27b": 30,
    "llama-3.1-8b-instant": 30,
    "llama-3.3-70b-versatile": 30,
    GEMINI_FLASH: 10,
    GEMINI_FLASH_LITE: 10,
}
DEFAULT_RPM = 30

# Which models each phase may use, in preference order. The scheduler walks this list and
# sends the request to the first model whose provider has a key with TPM/RPM headroom right
# now. Models whose provider has no keys are skipped. Override per phase under [routing].
DEFAULT_ROUTING: dict[str, list[str]] = {
    # Token-heavy reading/reasoning -> Gemini's 250k TPM first.
    "explore": [GEMINI_FLASH, "openai/gpt-oss-20b", "openai/gpt-oss-120b"],
    "plan": [GEMINI_FLASH, "openai/gpt-oss-120b"],
    "inspect": [GEMINI_FLASH, "openai/gpt-oss-120b"],
    # Tool use and part edits -> Groq's fast models, spill to Gemini when saturated.
    "code": ["openai/gpt-oss-120b", GEMINI_FLASH, "openai/gpt-oss-20b"],
    "debug": ["openai/gpt-oss-120b", GEMINI_FLASH],
    "test": ["openai/gpt-oss-20b", GEMINI_FLASH, "openai/gpt-oss-120b"],
    "review": ["openai/gpt-oss-20b", GEMINI_FLASH, "openai/gpt-oss-120b"],
    "summarise": ["openai/gpt-oss-20b", GEMINI_FLASH],
    "visual": ["qwen/qwen3.8-27b", GEMINI_FLASH],
}
# Headroom kept per request for the model's reply and token-estimate error.
TPM_REPLY_HEADROOM = 1800

DEFAULT_MODELS = {
    "fast": "openai/gpt-oss-20b",
    "deep": "openai/gpt-oss-120b",
    "vision": "qwen/qwen3.8-27b",
}


@dataclass
class Budget:
    max_tokens_per_run: int = 400_000
    max_tool_calls: int = 80
    max_repair_attempts: int = 3
    command_timeout_seconds: int = 300


@dataclass
class Config:
    api_keys: list[str] = field(default_factory=list)  # Groq keys
    gemini_keys: list[str] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    budget: Budget = field(default_factory=Budget)
    auto_approve: bool = False
    base_url: str = GROQ_BASE_URL
    gemini_base_url: str = GEMINI_BASE_URL
    # Reserve of the context window kept free for the model's own output.
    output_reserve_tokens: int = 16_000
    tpm_limits: dict[str, int] = field(default_factory=dict)
    rpm_limits: dict[str, int] = field(default_factory=dict)
    routing: dict[str, list[str]] = field(default_factory=dict)
    # Hard cap on prompt tokens per request (0 = derive from TPM and context window).
    max_request_tokens: int = 0

    def model_for(self, role: str) -> str:
        return self.models.get(role, DEFAULT_MODELS.get(role, DEFAULT_MODELS["deep"]))

    def context_window(self, model: str) -> int:
        return MODEL_CONTEXT.get(model, 131072)

    def keys_for(self, provider: str) -> list[str]:
        return list(self.gemini_keys if provider == "gemini" else self.api_keys)

    def base_url_for(self, provider: str) -> str:
        return self.gemini_base_url if provider == "gemini" else self.base_url

    @property
    def all_keys(self) -> list[str]:
        return [*self.api_keys, *self.gemini_keys]

    def candidates(self, phase: str, fallback_role: str = "deep") -> list[str]:
        """Models allowed for a phase, in preference order, limited to providers with keys."""
        chain = self.routing.get(phase) or DEFAULT_ROUTING.get(phase) or []
        usable = [m for m in chain if self.keys_for(provider_for(m))]
        if not usable:
            usable = [self.model_for(fallback_role)]
        return list(dict.fromkeys(usable))

    def rpm_limit(self, model: str) -> int:
        return int(self.rpm_limits.get(model) or MODEL_RPM.get(model, DEFAULT_RPM))

    def tpm_limit(self, model: str) -> int:
        return int(self.tpm_limits.get(model) or MODEL_TPM.get(model, DEFAULT_TPM))

    def request_budget(self, model: str) -> int:
        """Largest prompt (tokens) that fits both the context window and one minute of TPM."""
        window = self.context_window(model) - self.output_reserve_tokens
        per_minute = self.tpm_limit(model) - TPM_REPLY_HEADROOM
        budget = min(window, per_minute)
        if self.max_request_tokens:
            budget = min(budget, self.max_request_tokens)
        return max(2_500, budget)


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ; never overrides existing values."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _keys_from_env(prefix: str = "GROQ_API_KEY") -> list[str]:
    keys: list[str] = []
    for name in (prefix, *(f"{prefix}_{i}" for i in range(1, 6))):
        value = os.environ.get(name, "").strip()
        if value and "replace_me" not in value and value not in keys:
            keys.append(value)
    return keys


def load_config() -> Config:
    cfg = Config()
    # Keys live in .env (copy of .env.example) in the folder forge runs from — one file, up
    # to five keys. Real environment variables set before this call win.
    _load_dotenv(Path.cwd() / ".env")
    if CONFIG_PATH.exists():
        raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        groq = raw.get("groq", {})
        file_keys = groq.get("api_keys") or ([groq["api_key"]] if groq.get("api_key") else [])
        cfg.api_keys = [str(k).strip() for k in file_keys if str(k).strip() and "replace_me" not in str(k)]
        if groq.get("base_url"):
            cfg.base_url = str(groq["base_url"])
        cfg.models.update({k: str(v) for k, v in (raw.get("models") or {}).items()})
        budget = raw.get("budget") or {}
        for key, value in budget.items():
            if hasattr(cfg.budget, key):
                setattr(cfg.budget, key, int(value))
        limits = raw.get("limits") or {}
        cfg.tpm_limits.update({str(k): int(v) for k, v in (limits.get("tpm") or {}).items()})
        cfg.rpm_limits.update({str(k): int(v) for k, v in (limits.get("rpm") or {}).items()})
        gemini = raw.get("gemini", {})
        cfg.gemini_keys = [str(k).strip() for k in (gemini.get("api_keys") or []) if str(k).strip() and "replace_me" not in str(k)]
        if gemini.get("base_url"):
            cfg.gemini_base_url = str(gemini["base_url"])
        for phase, chain in (raw.get("routing") or {}).items():
            if isinstance(chain, str):
                chain = [chain]
            cfg.routing[str(phase)] = [str(m) for m in chain]
        if limits.get("max_request_tokens"):
            cfg.max_request_tokens = int(limits["max_request_tokens"])
        behaviour = raw.get("behaviour") or {}
        cfg.auto_approve = bool(behaviour.get("auto_approve", False))

    env_keys = _keys_from_env()
    if env_keys:
        cfg.api_keys = env_keys + [k for k in cfg.api_keys if k not in env_keys]
    cfg.api_keys = cfg.api_keys[:MAX_KEYS_PER_PROVIDER]
    gemini_env = _keys_from_env("GEMINI_API_KEY")
    if gemini_env:
        cfg.gemini_keys = gemini_env + [k for k in cfg.gemini_keys if k not in gemini_env]
    cfg.gemini_keys = cfg.gemini_keys[:MAX_KEYS_PER_PROVIDER]
    return cfg


def ensure_home() -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


def write_starter_config() -> Path:
    ensure_home()
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            "\n".join(
                [
                    "[groq]",
                    '# Up to five keys; Forge rotates across them.',
                    '# Easier: put GROQ_API_KEY=gsk_… (up to five) in .env',
                    '# in the project folder — see FORGE.md.',
                    'api_keys = ["gsk_replace_me"]',
                    "",
                    "[gemini]",
                    "# Optional. Easier: GEMINI_API_KEY / GEMINI_API_KEY_2 in .env",
                    "api_keys = []",
                    "",
                    "[routing]",
                    "# Optional per-phase model preference; Forge sends each request to the first",
                    "# model with free TPM/RPM right now. Example:",
                    '# code = ["openai/gpt-oss-120b", "gemini-2.5-flash"]',
                    "",
                    "[models]",
                    'fast = "openai/gpt-oss-20b"',
                    'deep = "openai/gpt-oss-120b"',
                    'vision = "qwen/qwen3.8-27b"',
                    "",
                    "[budget]",
                    "max_tokens_per_run = 400000",
                    "max_tool_calls = 80",
                    "max_repair_attempts = 3",
                    "",
                    "[limits]",
                    "# Groq free tier caps tokens per minute; prompts are clamped below this.",
                    "# Raise after upgrading, e.g. tpm = { \"openai/gpt-oss-120b\" = 250000 }",
                    "# max_request_tokens = 6000",
                    "",
                    "[behaviour]",
                    "auto_approve = false",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        CONFIG_PATH.chmod(0o600)
    return CONFIG_PATH


ENV_EXAMPLE_NAME = ".env.example"


def write_env_example(root: Path) -> Path:
    """Create a .env.example key file in the project folder (never overwrites)."""
    target = root / ENV_EXAMPLE_NAME
    if not target.exists():
        target.write_text(
            "\n".join(
                [
                    "# Forge API keys - paste your Groq keys below (gsk_...), one per line.",
                    "# Up to five keys; Forge rotates across them automatically.",
                    "# Environment variables (GROQ_API_KEY ...) still win over this file.",
                    "GROQ_API_KEY=",
                    "GROQ_API_KEY_2=",
                    "GROQ_API_KEY_3=",
                    "GROQ_API_KEY_4=",
                    "GROQ_API_KEY_5=",
                    "",
                    "# Optional Gemini keys (Google AI Studio) - up to five; Forge schedules",
                    "# requests across Groq and Gemini by free TPM/RPM.",
                    "GEMINI_API_KEY=",
                    "GEMINI_API_KEY_2=",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return target


def redact(text: str, cfg: Config | None = None) -> str:
    """Strip any API key material out of text before it is logged or displayed."""
    keys = (cfg.all_keys if cfg else []) or (_keys_from_env() + _keys_from_env("GEMINI_API_KEY"))
    out = text
    for key in keys:
        if key:
            out = out.replace(key, "***redacted***")
    return out
