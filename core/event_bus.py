from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Event:
    name: str
    data: dict[str, Any] = field(default_factory=dict)


Listener = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self._queue: list[Event] = []

    def subscribe(self, event_name: str, listener: Listener) -> None:
        self._listeners[event_name].append(listener)

    def publish(self, event: Event) -> None:
        """Queue an event for processing on next flush()."""
        self._queue.append(event)

    def emit(self, event: Event) -> None:
        """Dispatch an event immediately (synchronous)."""
        for listener in self._listeners.get(event.name, []):
            listener(event)

    def flush(self) -> None:
        """Process all queued events."""
        while self._queue:
            event = self._queue.pop(0)
            for listener in self._listeners.get(event.name, []):
                listener(event)


# Global singleton
bus = EventBus()
