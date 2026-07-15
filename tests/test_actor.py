"""
Enqueue a command -> correct Event built, `parent` lineage correct,
uuid -> result recorded.
"""

from __future__ import annotations

import json

from tododo.actor import Actor
from tododo.actor import Command
from tododo.log import EventLog
from tododo.projection import Projection


def _actor(tmp_path):
    log = EventLog(tmp_path / "events")
    proj = Projection()
    return Actor(log, proj), log, proj


def test_create_then_edit_parent_lineage(tmp_path):
    actor, log, proj = _actor(tmp_path)
    create = actor.execute(Command(op="CreateItem", args={"board": "b", "column": "todo", "title": "t"}))
    item_id = create.event.target

    first = actor.execute(Command(op="EditItem", target=item_id, field="title", value="v1"))
    assert first.event.parent == []

    second = actor.execute(Command(op="EditItem", target=item_id, field="title", value="v2"))
    assert second.event.parent == [first.event.id]
    assert proj.item(item_id).title.value == "v2"


def test_event_written_to_disk(tmp_path):
    actor, log, proj = _actor(tmp_path)
    job = actor.execute(Command(op="CreateBoard", args={"name": "Work", "columns": json.dumps(["a"])}))
    assert log.path_for(job.event.id).exists()


def test_column_op_computes_new_list(tmp_path):
    actor, log, proj = _actor(tmp_path)
    board = actor.execute(Command(op="CreateBoard", args={"name": "W", "columns": json.dumps(["a", "b"])}))
    board_id = board.event.target
    actor.execute(Command(op="CreateColumn", target=board_id, args={"name": "c"}))
    actor.execute(Command(op="SwapColumn", target=board_id, args={"col": "a", "with": "c"}))
    assert proj.board(board_id).columns == ["c", "b", "a"]


def test_submit_records_job(tmp_path):
    actor, log, proj = _actor(tmp_path)
    command = Command(op="CreateItem", args={"board": "b", "column": "c", "title": "t"})
    uuid = command.uuid
    actor.execute(command)
    job = actor.poll(uuid)
    assert job is not None
    assert job.status == "done"
    assert job.event.op == "CreateItem"
