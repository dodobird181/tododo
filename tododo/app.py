"""
Backend wiring: one process, three cooperating threads (DD1).

`Backend` owns the log, the in-memory projection, and the actor / filewatcher /
gitsync threads, connecting them through an explicit in-memory command queue and
a shared projection lock. Durability comes from the event files, not the queue.

Flow:
  - startup: replay the whole log once into the projection.
  - write:   actor builds+appends an event, applies it, then the filewatcher
             encrypts it and gitsync commits+pushes.
  - inbound: gitsync pulls new `.enc` files -> filewatcher decrypts -> each new
             event streams through `projection.apply` (no full replay).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from tododo.actor import Actor
from tododo.actor import Command
from tododo.actor import Job
from tododo.filewatcher import FileWatcher
from tododo.gitsync import GitSync
from tododo.log import EventLog
from tododo.models import Board
from tododo.models import Conflict
from tododo.models import Event
from tododo.models import Item
from tododo.projection import Projection


class Backend:
    """
    The composed backend. Construct with the repo root and the encryption
    passphrase; call `start()` to spin up the background threads.
    """

    def __init__(self, root: Path, passphrase: str, default_by: str = "", enable_git: bool = True):
        self.root = Path(root)
        self.default_by = default_by
        self.lock = threading.Lock()

        self.log = EventLog(self.root / "events")
        self.projection = Projection()
        self.projection.replay(self.log.read_all())

        self.actor = Actor(self.log, self.projection, self.lock)
        self.actor.on_event = self._on_event

        self.filewatcher = FileWatcher(
            self.root / "events",
            self.root / "events-encrypted",
            passphrase,
            on_inbound=self._apply_inbound,
        )
        self.gitsync = GitSync(self.root, on_pull=self._on_pull) if enable_git else None

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.filewatcher.encrypt_all()
        self.filewatcher.ingest()
        self.filewatcher.start()
        self.actor.start()
        if self.gitsync is not None:
            self.gitsync.start()

    def stop(self) -> None:
        if self.gitsync is not None:
            self.gitsync.stop()
        self.actor.stop()
        self.filewatcher.stop()

    # --- thread wiring ---------------------------------------------------

    def _on_event(self, event: Event) -> None:
        self.filewatcher.encrypt_file(event.id)
        if self.gitsync is not None:
            self.gitsync.push_change(f"event {event.id}")

    def _apply_inbound(self, event: Event) -> None:
        with self.lock:
            self.projection.apply(event)

    def _on_pull(self) -> None:
        self.filewatcher.ingest()

    # --- writes ----------------------------------------------------------

    def submit(self, command: Command) -> str:
        if not command.by:
            command.by = self.default_by
        return self.actor.submit(command)

    def poll(self, uuid: str) -> Job | None:
        return self.actor.poll(uuid)

    def execute(self, command: Command) -> Job:
        if not command.by:
            command.by = self.default_by
        return self.actor.execute(command)

    # --- reads (fold the projection synchronously) -----------------------

    def boards(self) -> list[Board]:
        with self.lock:
            return self.projection.boards()

    def board(self, board_id: str) -> Board:
        with self.lock:
            return self.projection.board(board_id)

    def items(self) -> list[Item]:
        with self.lock:
            return self.projection.items()

    def item(self, item_id: str) -> Item:
        with self.lock:
            return self.projection.item(item_id)

    def conflicts(self, board_id: str | None = None) -> list[Conflict]:
        with self.lock:
            return self.projection.conflicts(board_id)

    # --- operations (shared by the HTTP and MCP adapters) ----------------

    def create_board(self, name: str, columns: list[str], by: str = "") -> str:
        return self.submit(Command(
            op="CreateBoard", by=by, args={"name": name, "columns": json.dumps(list(columns))},
        ))

    def rename_board(self, target: str, name: str, by: str = "") -> str:
        return self.submit(Command(op="RenameBoard", by=by, target=target, field="name", value=name))

    def delete_board(self, target: str, by: str = "") -> str:
        return self.submit(Command(op="DeleteBoard", by=by, target=target))

    def create_column(self, board: str, name: str, by: str = "") -> str:
        return self.submit(Command(op="CreateColumn", by=by, target=board, args={"name": name}))

    def rename_column(self, board: str, col: str, name: str, by: str = "") -> str:
        return self.submit(Command(op="RenameColumn", by=by, target=board, args={"col": col, "name": name}))

    def swap_column(self, board: str, col: str, other: str, by: str = "") -> str:
        return self.submit(Command(op="SwapColumn", by=by, target=board, args={"col": col, "with": other}))

    def delete_column(self, board: str, col: str, by: str = "") -> str:
        return self.submit(Command(op="DeleteColumn", by=by, target=board, args={"col": col}))

    def create_item(self, board: str, column: str, title: str, by: str = "") -> str:
        return self.submit(Command(
            op="CreateItem", by=by, args={"board": board, "column": column, "title": title},
        ))

    def edit_item(self, target: str, field: str, value: str, by: str = "") -> str:
        return self.submit(Command(op="EditItem", by=by, target=target, field=field, value=value))

    def delete_item(self, target: str, by: str = "") -> str:
        return self.submit(Command(op="DeleteItem", by=by, target=target))

    def resolve_conflict(self, target: str, field: str, parents: list[str], value: str, by: str = "") -> str:
        return self.submit(Command(
            op="ResolveConflict", by=by, target=target, field=field, value=value, parent=list(parents),
        ))
