"""
Round-trips and tolerance for the persisted `Event` and the projection models.
"""

from __future__ import annotations

from tododo.models import Board
from tododo.models import Conflict
from tododo.models import Event
from tododo.models import Field
from tododo.models import Item


def test_event_round_trip(make_event):
    event = make_event(op="EditItem", target="i1", field="title", value="hi", parent=["p1"])
    restored = Event.from_dict(event.to_dict())
    assert restored == event


def test_event_defaults_mint_id_and_time():
    a = Event(op="CreateItem", target="i1")
    b = Event(op="CreateItem", target="i1")
    assert a.id != b.id
    assert a.at is not None


def test_event_sort_key_orders_by_at_then_id(make_event):
    early = make_event(value="a")
    late = make_event(value="b")
    assert early.sort_key() < late.sort_key()


def test_event_tolerates_missing_optional_fields():
    event = Event.from_dict({"op": "DeleteItem", "target": "i1"})
    assert event.field == ""
    assert event.parent == []
    assert event.payload == {}


def test_projection_models_round_trip():
    item = Item(id="i1", title=Field(value="t"))
    board = Board(id="b1", name=Field(value="Work"), columns=["a", "b"])
    conflict = Conflict(target="i1", field="title", events=[Event(op="EditItem", target="i1")])
    assert Item.model_validate(item.model_dump()) == item
    assert Board.model_validate(board.model_dump()) == board
    assert Conflict.model_validate(conflict.model_dump()) == conflict
