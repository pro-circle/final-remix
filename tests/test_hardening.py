from forge.models.key_manager import KeyManager, NoKeysAvailable
from forge.sandbox.policies import ApprovalPolicy, Denied
from forge.sandbox.terminal import TerminalSession
import pytest


@pytest.mark.parametrize("cmd", ["cat .env", "source .env && go run .", "env", "printenv", "echo $GROQ_API_KEY"])
def test_shell_cannot_touch_secrets(cmd):
    with pytest.raises(Denied):
        ApprovalPolicy().check_command(cmd)


@pytest.mark.parametrize("cmd", ["cat .env.example", "pytest -q", "env NODE_ENV=test npm test", "cat src/env.ts"])
def test_normal_commands_allowed(cmd):
    ApprovalPolicy().check_command(cmd)


def test_terminal_env_has_no_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_x")
    assert "GROQ_API_KEY" not in TerminalSession.__dataclass_fields__["env"].default_factory()


def test_rejected_key_is_retired():
    km = KeyManager(["gsk_a", "gsk_b"])
    km.report_invalid(km.states[0], "auth 401")
    assert all(km.acquire().key == "gsk_b" for _ in range(3))
    km.report_invalid(km.states[1], "auth 401")
    with pytest.raises(NoKeysAvailable):
        km.acquire()
