"""Structured event bus shared by the CLI renderer and the HTTP/SSE server."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

EventKind = str


@dataclass
class Event:
    kind: EventKind
    data: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "kind": self.kind,
            "run_id": self.run_id,
            "data": self.data,
        }


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Callable[[Event], None]] = []
        self.history: list[Event] = []

    def subscribe(self, handler: Callable[[Event], None]) -> Callable[[], None]:
        self._subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in self._subscribers:
                self._subscribers.remove(handler)

        return unsubscribe

    def emit(self, kind: EventKind, run_id: str | None = None, **data: Any) -> Event:
        event = Event(kind=kind, data=data, run_id=run_id)
        self.history.append(event)
        for handler in list(self._subscribers):
            try:
                handler(event)
            except Exception:  # a broken listener must not kill the run
                pass
        return event


# Event kinds used across the system.
RUN_STARTED = "run.started"
RUN_FINISHED = "run.finished"
RUN_FAILED = "run.failed"
PHASE = "phase"
PLAN = "plan"
THOUGHT = "thought"
TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
APPROVAL = "approval"
PATCH = "patch"
TEST_RESULT = "test.result"
FINDING = "finding"
USAGE = "usage"
NOTICE = "notice"
