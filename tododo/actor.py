"""
Actor process: the only thing that turns commands into events.

For each command it replays-to-current (the projection is already current in
memory), resolves the `parent` for the touched `(target, field)`, builds one
`Event`, appends it to the log and applies it to the projection. Results are
tracked by the command's `uuid` so an async caller can poll.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Callable

from pydantic import BaseModel
from pydantic import Field as PydanticField

from tododo.log import EventLog
from tododo.models import Event
from tododo.models import new_id
from tododo.projection import Projection


CREATE_OPS = {"CreateBoard", "CreateItem"}
DELETE_OPS = {"DeleteBoard", "DeleteItem"}
COLUMN_OPS = {"CreateColumn", "RenameColumn", "SwapColumn", "DeleteColumn"}


class Command(BaseModel):
    """
    A request to mutate state. `args` carries op-specific arguments (initial
    fields for creates, column names, etc.); `parent` is only set explicitly for
    `ResolveConflict`.
    """

    uuid: str = PydanticField(default_factory=new_id)
    op: str
    by: str = ""
    target: str = ""
    field: str = ""
    value: str = ""
    args: dict[str, str] = PydanticField(default_factory=dict)
    parent: list[str] = PydanticField(default_factory=list)


class Job(BaseModel):
    """
    The tracked outcome of a command, polled by `uuid`.
    """

    uuid: str
    status: str = "pending"
    event: Event | None = None
    error: str = ""


def _compute_columns(columns: list[str], op: str, args: dict[str, str]) -> list[str]:
    result = list(columns)
    if op == "CreateColumn":
        name = args["name"]
        if name not in result:
            result.append(name)
    elif op == "RenameColumn":
        old, name = args["col"], args["name"]
        result = [name if column == old else column for column in result]
    elif op == "SwapColumn":
        first, second = args["col"], args["with"]
        if first in result and second in result:
            i, j = result.index(first), result.index(second)
            result[i], result[j] = result[j], result[i]
    elif op == "DeleteColumn":
        result = [column for column in result if column != args["col"]]
    return result


def build_event(projection: Projection, command: Command) -> Event:
    """
    Resolve the `parent` from the current projection and build the `Event`.
    """
    op = command.op
    if op in CREATE_OPS:
        return Event(op=op, by=command.by, target=new_id(), field="", payload=dict(command.args))
    if op in DELETE_OPS:
        return Event(op=op, by=command.by, target=command.target, field="", payload={})
    if op in COLUMN_OPS:
        board = projection.board(command.target)
        new_columns = _compute_columns(board.columns, op, command.args)
        head = projection.head_for(command.target, "columns")
        return Event(
            op=op,
            by=command.by,
            target=command.target,
            field="columns",
            parent=[head.id] if head else [],
            payload={"value": json.dumps(new_columns)},
        )
    if op == "ResolveConflict":
        return Event(
            op=op,
            by=command.by,
            target=command.target,
            field=command.field,
            parent=list(command.parent),
            payload={"value": command.value},
        )
    head = projection.head_for(command.target, command.field)
    return Event(
        op=op,
        by=command.by,
        target=command.target,
        field=command.field,
        parent=[head.id] if head else [],
        payload={"value": command.value},
    )


class Actor:
    """
    Drains a command queue against a single `(log, projection)` pair. `lock`
    guards the projection so server reads see a consistent state.
    """

    def __init__(self, log: EventLog, projection: Projection, lock: threading.Lock | None = None):
        self.log = log
        self.projection = projection
        self.lock = lock or threading.Lock()
        self.on_event: Callable[[Event], None] | None = None
        self._queue: "queue.Queue[Command | None]" = queue.Queue()
        self._jobs: dict[str, Job] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._drain, name="actor", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)

    def submit(self, command: Command) -> str:
        """
        Enqueue a command and return its `uuid` immediately.
        """
        self._jobs[command.uuid] = Job(uuid=command.uuid)
        self._queue.put(command)
        return command.uuid

    def poll(self, uuid: str) -> Job | None:
        return self._jobs.get(uuid)

    def execute(self, command: Command) -> Job:
        """
        Build, append and apply one command synchronously. Used by the drain loop
        and directly by tests.
        """
        job = self._jobs.setdefault(command.uuid, Job(uuid=command.uuid))
        try:
            with self.lock:
                event = build_event(self.projection, command)
                self.log.append(event)
                self.projection.apply(event)
            job.status = "done"
            job.event = event
            if self.on_event:
                self.on_event(event)
        except Exception as error:
            job.status = "error"
            job.error = str(error)
        return job

    def _drain(self) -> None:
        while not self._stop.is_set():
            command = self._queue.get()
            if command is None:
                break
            self.execute(command)
