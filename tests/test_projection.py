"""
The core: folding, single-head winner, conflict detection, resolution,
idempotency, order-independence, and parent buffering.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from tododo.models import Event
from tododo.projection import Projection


def _create_item(target="i1", title="Buy milk", board="b1", column="todo"):
    return Event(
        op="CreateItem",
        target=target,
        field="",
        payload={"title": title, "board": board, "column": column},
        at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_create_seeds_fields():
    proj = Projection()
    proj.apply(_create_item())
    item = proj.item("i1")
    assert item.title.value == "Buy milk"
    assert item.column.value == "todo"
    assert item.board.value == "b1"


def test_single_head_winner(make_event):
    proj = Projection()
    proj.apply(_create_item())
    e1 = make_event(op="EditItem", target="i1", field="title", value="v1")
    e2 = make_event(op="EditItem", target="i1", field="title", value="v2", parent=[e1.id])
    proj.apply(e1)
    proj.apply(e2)
    assert proj.item("i1").title.value == "v2"
    assert proj.conflicts() == []


def test_conflict_two_heads_deterministic_interim(make_event):
    proj = Projection()
    proj.apply(_create_item())
    root = make_event(op="EditItem", target="i1", field="title", value="Buy milk")
    alice = make_event(op="EditItem", target="i1", field="title", value="oat", parent=[root.id])
    bob = make_event(op="EditItem", target="i1", field="title", value="almond", parent=[root.id])
    for event in (root, alice, bob):
        proj.apply(event)
    conflicts = proj.conflicts()
    assert len(conflicts) == 1
    assert {event.payload["value"] for event in conflicts[0].events} == {"oat", "almond"}
    winner = max(alice, bob, key=Event.sort_key)
    assert proj.item("i1").title.value == winner.payload["value"]


def test_different_fields_no_conflict(make_event):
    proj = Projection()
    proj.apply(_create_item())
    root = make_event(op="EditItem", target="i1", field="title", value="t")
    title_edit = make_event(op="EditItem", target="i1", field="title", value="t2", parent=[root.id])
    desc_edit = make_event(op="EditItem", target="i1", field="description", value="d")
    for event in (root, title_edit, desc_edit):
        proj.apply(event)
    assert proj.conflicts() == []


def test_resolve_collapses_heads(make_event):
    proj = Projection()
    proj.apply(_create_item())
    root = make_event(op="EditItem", target="i1", field="title", value="milk")
    alice = make_event(op="EditItem", target="i1", field="title", value="oat", parent=[root.id])
    bob = make_event(op="EditItem", target="i1", field="title", value="almond", parent=[root.id])
    for event in (root, alice, bob):
        proj.apply(event)
    resolve = make_event(
        op="ResolveConflict", target="i1", field="title", value="oat", parent=[alice.id, bob.id],
    )
    proj.apply(resolve)
    assert proj.conflicts() == []
    assert proj.item("i1").title.value == "oat"


def test_idempotent_apply(make_event):
    proj = Projection()
    proj.apply(_create_item())
    e1 = make_event(op="EditItem", target="i1", field="title", value="v1")
    proj.apply(e1)
    proj.apply(e1)
    proj.apply(e1)
    assert proj.item("i1").title.value == "v1"
    assert proj.conflicts() == []


def test_order_independence(make_event):
    root = make_event(op="EditItem", target="i1", field="title", value="milk")
    alice = make_event(op="EditItem", target="i1", field="title", value="oat", parent=[root.id])
    bob = make_event(op="EditItem", target="i1", field="title", value="almond", parent=[root.id])
    resolve = make_event(
        op="ResolveConflict", target="i1", field="title", value="oat", parent=[alice.id, bob.id],
    )
    create = _create_item()

    def build(order):
        proj = Projection()
        for event in order:
            proj.apply(event)
        return proj.item("i1").title.value, proj.conflicts()

    in_order = build([create, root, alice, bob, resolve])
    reversed_order = build([resolve, bob, alice, root, create])
    assert in_order[0] == reversed_order[0] == "oat"
    assert in_order[1] == reversed_order[1] == []


def test_buffering_holds_orphan_until_parent_arrives(make_event):
    proj = Projection()
    proj.apply(_create_item())
    root = make_event(op="EditItem", target="i1", field="title", value="v1")
    child = make_event(op="EditItem", target="i1", field="title", value="v2", parent=[root.id])
    proj.apply(child)
    assert proj.item("i1").title.value == "Buy milk"
    proj.apply(root)
    assert proj.item("i1").title.value == "v2"
    assert proj.conflicts() == []
