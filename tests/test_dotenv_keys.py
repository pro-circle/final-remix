"""Keys come from .env in the project folder — no .env, no home fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

import forge.config as config_mod
from forge.config import load_config, write_env_example

KEY_NAMES = ("GROQ_API_KEY", *(f"GROQ_API_KEY_{i}" for i in range(1, 6)))


@pytest.fixture()
def clean_env(monkeypatch):
    for name in KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    home_env = Path(config_mod.HOME_DIR) / ".env"
    if home_env.exists():
        home_env.unlink()


def _chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def test_keys_load_from_env_example(tmp_path, monkeypatch, clean_env):
    _chdir(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text(
        "# comment\nGROQ_API_KEY=gsk_first\nGROQ_API_KEY_2=gsk_second\n",
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.api_keys == ["gsk_first", "gsk_second"]


def test_real_env_beats_env_example(tmp_path, monkeypatch, clean_env):
    _chdir(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text("GROQ_API_KEY=gsk_from_file\n", encoding="utf-8")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env_var")
    cfg = load_config()
    assert cfg.api_keys == ["gsk_from_env_var"]


def test_home_dotenv_is_ignored(tmp_path, monkeypatch, clean_env):
    (Path(config_mod.HOME_DIR) / ".env").write_text("GROQ_API_KEY=gsk_home\n", encoding="utf-8")
    _chdir(tmp_path, monkeypatch)
    cfg = load_config()
    assert cfg.api_keys == []


def test_placeholder_keys_are_ignored(tmp_path, monkeypatch, clean_env):
    _chdir(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text("GROQ_API_KEY=gsk_replace_me\n", encoding="utf-8")
    cfg = load_config()
    assert cfg.api_keys == []


def test_env_example_supports_quotes_and_ignores_garbage(tmp_path, monkeypatch, clean_env):
    _chdir(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text(
        'GROQ_API_KEY="gsk_quoted"\nNO_EQUALS_HERE\n\nGROQ_API_KEY_2=\'gsk_single\'\n',
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.api_keys == ["gsk_quoted", "gsk_single"]


def test_init_writes_env_example(tmp_path, monkeypatch, clean_env):
    target = write_env_example(tmp_path)
    assert target == tmp_path / ".env.example"
    _chdir(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text("GROQ_API_KEY=gsk_added\n", encoding="utf-8")
    cfg = load_config()
    assert cfg.api_keys == ["gsk_added"]
