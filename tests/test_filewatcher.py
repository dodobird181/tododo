"""
Raw write -> `.enc` mirror; inbound decrypt applies each new event once.
Handler methods are driven directly (no real fs-event waiting) for determinism.
"""

from __future__ import annotations

from pathlib import Path

from tododo.filewatcher import FileWatcher
from tododo.log import EventLog
from tododo.models import Event


def _watcher(root: Path, on_inbound=None) -> FileWatcher:
    return FileWatcher(root / "events", root / "events-encrypted", "pw", on_inbound=on_inbound)


def test_encrypt_all_mirrors_raw(tmp_path: Path):
    log = EventLog(tmp_path / "events")
    event = Event(op="CreateItem", target="i1")
    log.append(event)
    watcher = _watcher(tmp_path)
    watcher.encrypt_all()
    mirror = watcher.enc_path_for(event.id)
    assert mirror.exists()
    assert mirror.read_bytes() != log.path_for(event.id).read_bytes()


def test_encrypt_is_once_only(tmp_path: Path):
    log = EventLog(tmp_path / "events")
    event = Event(op="CreateItem", target="i1")
    log.append(event)
    watcher = _watcher(tmp_path)
    watcher.encrypt_file(event.id)
    first = watcher.enc_path_for(event.id).read_bytes()
    watcher.encrypt_file(event.id)
    assert watcher.enc_path_for(event.id).read_bytes() == first


def test_inbound_decrypt_round_trip(tmp_path: Path):
    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    log = EventLog(origin / "events")
    event = Event(op="EditItem", target="i1", field="title", payload={"value": "hi"})
    log.append(event)
    origin_watcher = _watcher(origin)
    origin_watcher.encrypt_all()

    mirror = origin_watcher.enc_path_for(event.id)
    clone_mirror = clone / "events-encrypted" / mirror.name
    clone_mirror.parent.mkdir(parents=True)
    clone_mirror.write_bytes(mirror.read_bytes())

    applied = []
    clone_watcher = _watcher(clone, on_inbound=applied.append)
    ingested = clone_watcher.ingest()
    assert [event.id] == [e.id for e in ingested] == [e.id for e in applied]
    assert clone_watcher.raw_path_for(event.id).exists()

    # Re-ingest is a no-op (raw counterpart now exists).
    assert clone_watcher.ingest() == []
