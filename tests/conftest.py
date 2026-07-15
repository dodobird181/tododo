"""
Shared fixtures: a temp events dir, a synthetic Event factory, and a
git-disabled Backend wired against a tmpdir.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest

from tododo.app import Backend
from tododo.log import EventLog
from tododo.models import Event
from tododo.projection import Projection


@pytest.fixture
def events_dir(tmp_path: Path) -> Path:
    return tmp_path / "events"


@pytest.fixture
def log(events_dir: Path) -> EventLog:
    return EventLog(events_dir)


@pytest.fixture
def projection() -> Projection:
    return Projection()


@pytest.fixture
def make_event():
    counter = {"n": 0}

    def factory(op="EditItem", target="item-1", field="title", value=None, parent=None, by="tester", at=None):
        counter["n"] += 1
        payload = {} if value is None else {"value": value}
        return Event(
            op=op,
            target=target,
            field=field,
            by=by,
            at=at or datetime(2026, 1, 1, tzinfo=timezone.utc).replace(microsecond=counter["n"]),
            parent=list(parent or []),
            payload=payload,
        )

    return factory


@pytest.fixture
def backend(tmp_path: Path) -> Backend:
    return Backend(tmp_path, passphrase="test-pass", default_by="tester", enable_git=False)
