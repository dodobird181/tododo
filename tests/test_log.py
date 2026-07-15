"""
Append writes `events/<id>.yaml`, read-all is ordered, re-append is idempotent.
"""

from __future__ import annotations

from tododo.log import EventLog


def test_append_writes_named_file(log: EventLog, make_event):
    event = make_event(value="v1")
    path = log.append(event)
    assert path.name == f"{event.id}.yaml"
    assert path.exists()


def test_read_round_trip(log: EventLog, make_event):
    event = make_event(op="EditItem", target="i1", field="title", value="v1", parent=["p"])
    log.append(event)
    assert log.read(event.id) == event


def test_read_all_sorted(log: EventLog, make_event):
    a = make_event(value="a")
    b = make_event(value="b")
    log.append(b)
    log.append(a)
    ids = [event.id for event in log.read_all()]
    assert ids == [a.id, b.id]


def test_idempotent_reappend(log: EventLog, make_event):
    event = make_event(value="v1")
    log.append(event)
    log.append(event)
    assert log.read_ids() == {event.id}
    assert len(log.read_all()) == 1
