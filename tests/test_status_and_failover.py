from forge.events import Event, TOOL_CALL, PHASE, RUN_FINISHED
from forge.ui.render import CliRenderer, make_console
from forge.models.client import UpstreamUnavailable, ModelError


def test_status_verbs():
    s = CliRenderer.status_text
    assert s(Event(TOOL_CALL, {"tool": "edit_file", "args": {"path": "a.py"}})) == "editing a.py..."
    assert s(Event(TOOL_CALL, {"tool": "write_file", "args": {"path": "b.py"}})) == "writing b.py..."
    assert s(Event(TOOL_CALL, {"tool": "delete_file", "args": {"path": "c"}})) == "deleting c..."
    assert s(Event(TOOL_CALL, {"tool": "mystery", "args": {}})) == "working..."
    assert s(Event(PHASE, {"phase": "code"})) == "thinking..."
    assert s(Event(RUN_FINISHED, {})) is None


def test_renderer_handles_without_tty():
    r = CliRenderer(make_console())
    r.handle(Event(TOOL_CALL, {"tool": "edit_file", "args": {"path": "x"}}))
    r.handle(Event(RUN_FINISHED, {}))


def test_upstream_is_model_error():
    assert issubclass(UpstreamUnavailable, ModelError)
