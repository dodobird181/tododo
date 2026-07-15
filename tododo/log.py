"""
Append-only event store. The only writer of raw plaintext `events/<id>.yaml`
files. Events are immutable, so re-appending the same id is a harmless no-op.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from tododo.models import Event


class EventLog:
    """
    Filesystem-backed append-only log under `events_dir`. Filename == `Event.id`.
    """

    def __init__(self, events_dir: Path):
        self.events_dir = Path(events_dir)
        self.events_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, event_id: str) -> Path:
        return self.events_dir / f"{event_id}.yaml"

    def append(self, event: Event) -> Path:
        """
        Write one event atomically. Idempotent: an already-present id is left
        untouched (events never change).
        """
        destination = self.path_for(event.id)
        if destination.exists():
            return destination
        blob = yaml.safe_dump(event.to_dict(), sort_keys=True).encode("utf-8")
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(blob)
        os.replace(temporary, destination)
        return destination

    def read(self, event_id: str) -> Event:
        data = yaml.safe_load(self.path_for(event_id).read_text(encoding="utf-8"))
        return Event.from_dict(data)

    def read_ids(self) -> set[str]:
        return {path.stem for path in self.events_dir.glob("*.yaml")}

    def read_all(self) -> list[Event]:
        """
        Every event, ordered by `(at, id)` so replay is deterministic.
        """
        events = []
        for path in self.events_dir.glob("*.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            events.append(Event.from_dict(data))
        events.sort(key=Event.sort_key)
        return events
