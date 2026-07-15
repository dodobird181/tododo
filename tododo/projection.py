"""
Materialized in-memory projection of the event log.

State is rebuilt by folding events. Per `(target, field)` the projection keeps a
DAG of events linked by `parent`; **heads** are leaf events (no applied child).
One head -> its value wins (tiebreak `(at, id)`); more than one head -> a
`Conflict`. `apply` is idempotent (same id twice is a no-op) and
order-independent (an event whose parent has not arrived yet is buffered until
it does), so streaming events one-by-one builds the same DAG as a full replay.

Field values travel in `payload["value"]` as strings; list-valued fields
(`columns`) carry a JSON-encoded list. Whole-target lifecycle events
(`CreateItem`/`CreateBoard`/`DeleteItem`/`DeleteBoard`) use `field == ""` and
seed initial field values from their payload.
"""

from __future__ import annotations

import json

from tododo.models import Board
from tododo.models import Conflict
from tododo.models import Event
from tododo.models import Field
from tododo.models import Item


ITEM_FIELDS = (
    "title",
    "description",
    "start",
    "end",
    "column",
    "board",
    "assigned_to",
    "report_to",
    "order",
)


class _Dag:
    """
    The event DAG for one `(target, field)`. `children` maps a parent id to the
    applied events that named it as a parent; leaves (empty child set) are heads.
    """

    def __init__(self):
        self.events: dict[str, Event] = {}
        self.children: dict[str, set[str]] = {}

    def add(self, event: Event) -> None:
        self.events[event.id] = event
        self.children.setdefault(event.id, set())
        for parent in event.parent:
            self.children.setdefault(parent, set()).add(event.id)

    def heads(self) -> list[Event]:
        return [event for event_id, event in self.events.items() if not self.children.get(event_id)]

    def winner(self) -> Event | None:
        heads = self.heads()
        if not heads:
            return None
        return max(heads, key=Event.sort_key)


class Projection:
    """
    Holds all applied events and answers `boards()`, `items()`, `conflicts()`.
    """

    def __init__(self):
        self._dags: dict[tuple[str, str], _Dag] = {}
        self._applied: set[str] = set()
        self._pending: dict[str, list[Event]] = {}
        self._kinds: dict[str, str] = {}

    def replay(self, events: list[Event]) -> None:
        """
        Full fold from zero. Ordering is irrelevant to the final DAG, but sorting
        keeps the interim winner deterministic while a batch streams in.
        """
        for event in sorted(events, key=Event.sort_key):
            self.apply(event)

    def apply(self, event: Event) -> None:
        """
        Insert one event. Idempotent and order-independent; an event whose parent
        is not yet present is buffered until the parent arrives.
        """
        if event.id in self._applied:
            return
        missing = [parent for parent in event.parent if parent not in self._applied]
        if missing:
            for parent in missing:
                self._pending.setdefault(parent, []).append(event)
            return
        self._applied.add(event.id)
        self._dags.setdefault((event.target, event.field), _Dag()).add(event)
        if event.op == "CreateItem":
            self._kinds[event.target] = "item"
        elif event.op == "CreateBoard":
            self._kinds[event.target] = "board"
        for waiter in self._pending.pop(event.id, []):
            self.apply(waiter)

    def has(self, event_id: str) -> bool:
        return event_id in self._applied

    def _lifecycle(self, target: str) -> Event | None:
        dag = self._dags.get((target, ""))
        return dag.winner() if dag else None

    def _is_deleted(self, target: str, delete_op: str) -> bool:
        dag = self._dags.get((target, ""))
        if not dag:
            return False
        return any(event.op == delete_op for event in dag.events.values())

    def _field(self, target: str, field: str, create: Event | None) -> Field:
        dag = self._dags.get((target, field))
        if dag and dag.events:
            winner = dag.winner()
            return Field(
                value=winner.payload.get("value", ""),
                last_edited_at=winner.at,
                last_edited_by=winner.by,
                event=winner.id,
            )
        if create and field in create.payload:
            return Field(
                value=create.payload[field],
                last_edited_at=create.at,
                last_edited_by=create.by,
                event=create.id,
            )
        return Field()

    def _columns(self, target: str, create: Event | None) -> list[str]:
        dag = self._dags.get((target, "columns"))
        raw = None
        if dag and dag.events:
            raw = dag.winner().payload.get("value")
        elif create and "columns" in create.payload:
            raw = create.payload["columns"]
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(column) for column in parsed]

    def item(self, target: str) -> Item:
        create = self._lifecycle(target)
        item = Item(id=target, deleted=self._is_deleted(target, "DeleteItem"))
        for field in ITEM_FIELDS:
            setattr(item, field, self._field(target, field, create))
        return item

    def board(self, target: str) -> Board:
        create = self._lifecycle(target)
        return Board(
            id=target,
            name=self._field(target, "name", create),
            columns=self._columns(target, create),
            deleted=self._is_deleted(target, "DeleteBoard"),
        )

    def items(self, include_deleted: bool = False) -> list[Item]:
        result = [self.item(target) for target, kind in self._kinds.items() if kind == "item"]
        if not include_deleted:
            result = [item for item in result if not item.deleted]
        return result

    def boards(self, include_deleted: bool = False) -> list[Board]:
        result = [self.board(target) for target, kind in self._kinds.items() if kind == "board"]
        if not include_deleted:
            result = [board for board in result if not board.deleted]
        return result

    def head_for(self, target: str, field: str) -> Event | None:
        """
        The single winning event for a `(target, field)`, for the actor to use as
        the `parent` of the next edit. `None` if nothing has touched it yet.
        """
        dag = self._dags.get((target, field))
        return dag.winner() if dag else None

    def conflicts(self, board_id: str | None = None) -> list[Conflict]:
        """
        Every `(target, field)` with more than one head. If `board_id` is given,
        restrict to that board and the items whose `board` field points at it.
        """
        item_ids_on_board = None
        if board_id is not None:
            board = self.board(board_id)
            wanted_name = board.name.value or board_id
            item_ids_on_board = {
                item.id for item in self.items(include_deleted=True)
                if item.board.value in (board_id, wanted_name)
            }
        result = []
        for (target, field), dag in self._dags.items():
            heads = dag.heads()
            if len(heads) <= 1:
                continue
            if board_id is not None and target != board_id and (
                item_ids_on_board is None or target not in item_ids_on_board
            ):
                continue
            result.append(
                Conflict(
                    target=target,
                    field=field,
                    events=sorted(heads, key=Event.sort_key),
                )
            )
        return result
