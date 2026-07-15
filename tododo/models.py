"""
Pydantic models for the Tododo event-sourced backend.

The only thing ever persisted is an `Event`. `Item`, `Board`, `Field` and
`Conflict` are projections: rebuilt in memory by replaying the event log. See
docs/tododo.dsl.md for the domain definition.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from uuid import uuid4

from pydantic import BaseModel
from pydantic import Field as PydanticField


def new_id() -> str:
    """
    Mint a unique event/target id. Also the on-disk filename of an event.
    """
    return uuid4().hex


def now() -> datetime:
    """
    Wall-clock of the author, timezone-aware UTC.
    """
    return datetime.now(timezone.utc)


class Event(BaseModel):
    """
    The only persisted type. Immutable; filename == `id`.

    `parent` lists the event(s) this one is based on for its `target`+`field`.
    Two events sharing a `parent` and touching the same `target`+`field` are
    concurrent -> a conflict. `ResolveConflict` names every conflicting event as
    a parent (a merge commit) to re-join the lineage.
    """

    id: str = PydanticField(default_factory=new_id)
    at: datetime = PydanticField(default_factory=now)
    by: str = ""
    op: str
    target: str
    field: str = ""
    parent: list[str] = PydanticField(default_factory=list)
    payload: dict[str, str] = PydanticField(default_factory=dict)

    def sort_key(self) -> tuple[datetime, str]:
        """
        Deterministic ordering across machines: `(at, id)`.
        """
        return (self.at, self.id)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls.model_validate(data)


class Field(BaseModel):
    """
    A projected field value plus the provenance of the event that won it.
    """

    value: str = ""
    last_edited_at: datetime | None = None
    last_edited_by: str = ""
    event: str = ""


class Item(BaseModel):
    """
    Projection: never stored, folded from events with `target == id`.
    """

    id: str
    title: Field = PydanticField(default_factory=Field)
    description: Field = PydanticField(default_factory=Field)
    start: Field = PydanticField(default_factory=Field)
    end: Field = PydanticField(default_factory=Field)
    column: Field = PydanticField(default_factory=Field)
    board: Field = PydanticField(default_factory=Field)
    assigned_to: Field = PydanticField(default_factory=Field)
    report_to: Field = PydanticField(default_factory=Field)
    order: Field = PydanticField(default_factory=Field)
    deleted: bool = False


class Board(BaseModel):
    """
    Projection, folded from events with `target == id`.
    """

    id: str
    name: Field = PydanticField(default_factory=Field)
    columns: list[str] = PydanticField(default_factory=list)
    deleted: bool = False


class Conflict(BaseModel):
    """
    Not persisted; computed during replay, consumed by the UI. `events` are the
    2+ concurrent events, each a candidate value for `field`.
    """

    target: str
    field: str
    events: list[Event]
