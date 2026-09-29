"""Two-provider fleet: key loading, per-provider rotation, and capacity-based routing."""

from __future__ import annotations

from pathlib import Path

import pytest

import forge.config as config_mod
from forge.config import GEMINI_FLASH, Config, load_config
from forge.models.client import FleetClient
from forge.models.key_manager import KeyManager
from forge.models.router import PHASE_ROLE, ModelRouter

GROQ_NAMES = ("GROQ_API_KEY", *(f"GROQ_API_KEY_{i}" for i in range(1, 6)))
GEMINI_NAMES = ("GEMINI_API_KEY", *(f"GEMINI_API_KEY_{i}" for i in range(1, 6)))

FAST = "openai/gpt-oss-20b"
DEEP = "openai/gpt-oss-120b"


@pytest.fixture()
def clean_env(monkeypatch):
    for name in (*GROQ_NAMES, *GEMINI_NAMES):
        monkeypatch.delenv(name, raising=False)
    home_env = Path(config_mod.HOME_DIR) / ".env"
    if home_env.exists():
        home_env.unlink()


# ------------------------------------------------------------------ key loading
def test_two_gemini_keys_load_from_dotenv(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "GROQ_API_KEY=gsk_a\nGEMINI_API_KEY=AIza_one\nGEMINI_API_KEY_2=AIza_two\n",
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.api_keys == ["gsk_a"]
    assert cfg.gemini_keys == ["AIza_one", "AIza_two"]
    assert cfg.all_keys == ["gsk_a", "AIza_one", "AIza_two"]


def test_five_keys_per_provider_and_cap(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    lines = [f"GROQ_API_KEY_{i}=gsk_{i}" for i in range(1, 6)]
    lines += [f"GEMINI_API_KEY_{i}=AIza_{i}" for i in range(1, 6)]
    lines.append("GEMINI_API_KEY=AIza_zero")  # a sixth Gemini key: dropped by the cap
    (tmp_path / ".env").write_text("\n".join(lines), encoding="utf-8")
    cfg = load_config()
    assert len(cfg.api_keys) == 5
    assert len(cfg.gemini_keys) == 5
    assert cfg.gemini_keys[0] == "AIza_zero"  # bare name comes first, key 5 falls off


def test_gemini_placeholders_ignored(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=replace_me_here\n", encoding="utf-8")
    assert load_config().gemini_keys == []


# --------------------------------------------------------------- key rotation
def test_each_provider_rotates_its_own_keys():
    groq = KeyManager(["g1", "g2", "g3"], "groq")
    gem = KeyManager(["m1", "m2"], "gemini")
    assert [groq.acquire().key for _ in range(4)] == ["g1", "g2", "g3", "g1"]
    assert [gem.acquire().key for _ in range(3)] == ["m1", "m2", "m1"]
    assert all(s.label.startswith("gemini:") for s in gem.states)


def test_key_is_skipped_once_its_own_minute_is_full():
    km = KeyManager(["k1", "k2"], "gemini")
    first = km.states[0]
    km.record(first, GEMINI_FLASH, 240_000)
    assert not first.fits(GEMINI_FLASH, 50_000, tpm=250_000, rpm=10)
    picked = km.acquire(GEMINI_FLASH, need=50_000, tpm=250_000, rpm=10)
    assert picked.key == "k2"
    assert km.headroom(GEMINI_FLASH, 50_000, tpm=250_000, rpm=10) is True  # k2 still free



def test_rpm_is_tracked_per_key_and_model():
    km = KeyManager(["k1"], "gemini")
    state = km.states[0]
    for _ in range(10):
        km.record(state, GEMINI_FLASH, 10)
    assert km.headroom(GEMINI_FLASH, 10, tpm=250_000, rpm=10) is False
    assert km.headroom(FAST, 10, tpm=8000, rpm=30) is True  # other model, own bucket
    assert 0 < km.wait_estimate(GEMINI_FLASH, 10, 250_000, 10) <= 60


# ------------------------------------------------------------- fleet dispatch
def test_fleet_sends_each_model_to_its_provider():
    cfg = Config(api_keys=["gsk_a"], gemini_keys=["AIza_a"])
    fleet = FleetClient(cfg)
    try:
        assert set(fleet.providers) == {"groq", "gemini"}
        assert fleet._for(DEEP).provider == "groq"
        assert fleet._for(GEMINI_FLASH).provider == "gemini"
        assert fleet._for(GEMINI_FLASH).base_url == cfg.gemini_base_url
        assert len(fleet.keys) == 2
        providers = {row["provider"] for row in fleet.keys.health()}
        assert providers == {"groq", "gemini"}
    finally:
        fleet.close()


def test_fleet_without_gemini_keys_serves_groq_only():
    cfg = Config(api_keys=["gsk_a"])
    fleet = FleetClient(cfg)
    try:
        assert set(fleet.providers) == {"groq"}
        assert fleet.serves(DEEP) and not fleet.serves(GEMINI_FLASH)
        assert fleet.wait_estimate(GEMINI_FLASH, 100) == float("inf")
    finally:
        fleet.close()


# ------------------------------------------------------- capacity-based choice
def test_router_prefers_gemini_for_reading_phases():
    cfg = Config(api_keys=["gsk_a"], gemini_keys=["AIza_a"])
    fleet = FleetClient(cfg)
    try:
        router = ModelRouter(cfg, fleet)
        assert router.choose("explore", need=5_000) == GEMINI_FLASH
        assert router.choose("plan", need=5_000) == GEMINI_FLASH
        assert router.choose("code", need=5_000) == DEEP  # tool use stays on Groq
        assert router.choose("review", need=1_000) == FAST
    finally:
        fleet.close()


def test_router_spills_to_groq_when_gemini_is_saturated():
    cfg = Config(api_keys=["gsk_a"], gemini_keys=["AIza_a"])
    fleet = FleetClient(cfg)
    try:
        gem_keys = fleet.providers["gemini"].keys
        for _ in range(cfg.rpm_limit(GEMINI_FLASH)):
            gem_keys.record(gem_keys.states[0], GEMINI_FLASH, 10)
        assert ModelRouter(cfg, fleet).choose("explore", need=5_000) == FAST
    finally:
        fleet.close()


def test_router_spills_to_gemini_when_groq_is_saturated():
    cfg = Config(api_keys=["gsk_a"], gemini_keys=["AIza_a"])
    fleet = FleetClient(cfg)
    try:
        groq_keys = fleet.providers["groq"].keys
        for model in (DEEP, FAST):
            for _ in range(cfg.rpm_limit(model)):
                groq_keys.record(groq_keys.states[0], model, 10)
        assert ModelRouter(cfg, fleet).choose("code", need=4_000) == GEMINI_FLASH
    finally:
        fleet.close()


def test_router_skips_models_whose_provider_has_no_keys():
    cfg = Config(api_keys=["gsk_a"])  # Groq only
    fleet = FleetClient(cfg)
    try:
        router = ModelRouter(cfg, fleet)
        for phase in PHASE_ROLE:
            assert not router.choose(phase, need=1_000).startswith("gemini")
    finally:
        fleet.close()


def test_router_picks_soonest_free_model_when_all_are_full():
    cfg = Config(api_keys=["gsk_a"], gemini_keys=["AIza_a"])
    fleet = FleetClient(cfg)
    try:
        for provider, models in (("groq", (DEEP, FAST)), ("gemini", (GEMINI_FLASH,))):
            keys = fleet.providers[provider].keys
            for model in models:
                for _ in range(cfg.rpm_limit(model)):
                    keys.record(keys.states[0], model, cfg.tpm_limit(model))
        chosen = ModelRouter(cfg, fleet).choose("code", need=4_000)
        assert chosen in (DEEP, FAST, GEMINI_FLASH)  # still returns a usable model, never None
    finally:
        fleet.close()


def test_router_without_capacity_client_falls_back_to_chain_order():
    cfg = Config(api_keys=["gsk_a"], gemini_keys=["AIza_a"])
    assert ModelRouter(cfg).choose("explore") == GEMINI_FLASH
    assert ModelRouter(cfg).model_for_phase("code") == DEEP


def test_groq_only_setup_behaves_exactly_as_before():
    cfg = Config(api_keys=["gsk_a"])
    router = ModelRouter(cfg, FleetClient(cfg))
    assert router.choose("explore") == FAST
    assert router.choose("code") == DEEP
    assert router.context_budget_for(router.choose("code")) < cfg.tpm_limit(DEEP)
