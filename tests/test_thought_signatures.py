"""Gemini 3 thought signatures survive the tool loop and are adapted per provider."""

from forge.models.client import _normalise_tool_calls, _prepare_messages

SIG = {"google": {"thought_signature": "abc123"}}


def _assistant(calls):
    return {"role": "assistant", "content": "", "tool_calls": calls}


def _call(extra=None):
    c = {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}
    if extra:
        c["extra_content"] = extra
    return c


def test_normalise_keeps_extra_content():
    out = _normalise_tool_calls([_call(SIG)])
    assert out[0]["extra_content"] == SIG


def test_gemini_keeps_real_signature():
    msgs = _prepare_messages([_assistant([_call(SIG)])], "gemini")
    assert msgs[0]["tool_calls"][0]["extra_content"] == SIG


def test_gemini_fills_placeholder_for_foreign_calls():
    msgs = _prepare_messages([_assistant([_call(), _call()])], "gemini")
    calls = msgs[0]["tool_calls"]
    assert calls[0]["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"
    assert "extra_content" not in calls[1]


def test_groq_strips_extra_content_without_mutating_history():
    history = [_assistant([_call(SIG)])]
    msgs = _prepare_messages(history, "groq")
    assert "extra_content" not in msgs[0]["tool_calls"][0]
    assert history[0]["tool_calls"][0]["extra_content"] == SIG
