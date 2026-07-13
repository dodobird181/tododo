"""Per-item data model (see ``itemspec.yaml``).

Each item is a single YAML file under ``items/``. Every user-facing field carries
its own edit provenance (``value`` / ``last_edited_at`` / ``last_edited_by``) so
authorship no longer needs a separate registry or git blame. ``lock`` records the
github username currently holding an exclusive edit lock (or ``None``).

A board is just a name plus an ordered, unique list of column names, stored one
file per board under ``boards/``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

# User-editable fields, each stored as a {value, last_edited_at, last_edited_by} block.
FIELD_KEYS = [
    "title",
    "description",
    "start",       # ISO datetime string, "" == none
    "end",         # ISO datetime string, "" == none
    "column",
    "board",
    "assigned_to",
    "report_to",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Field:
    """One provenance-tracked value."""

    value: str = ""
    last_edited_at: str = ""
    last_edited_by: str = ""

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "last_edited_at": self.last_edited_at,
            "last_edited_by": self.last_edited_by,
        }

    @classmethod
    def from_dict(cls, d) -> "Field":
        if not isinstance(d, dict):
            # Tolerate a bare scalar (e.g. hand-written YAML) as the value.
            return cls(value="" if d is None else str(d))
        return cls(
            value="" if d.get("value") is None else str(d.get("value")),
            last_edited_at=str(d.get("last_edited_at") or ""),
            last_edited_by=str(d.get("last_edited_by") or ""),
        )


@dataclass
class Item:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    fields: dict[str, Field] = field(default_factory=dict)
    # Lock: github username of the holder (or None), plus when it was last touched.
    lock_value: str | None = None
    lock_edited_at: str = ""

    def __post_init__(self) -> None:
        for key in FIELD_KEYS:
            self.fields.setdefault(key, Field())

    # --- convenience accessors ------------------------------------------

    def value(self, key: str) -> str:
        return self.fields[key].value

    def set(self, key: str, value: str, actor: str, when: str | None = None) -> None:
        """Set a field's value and stamp its provenance."""
        self.fields[key] = Field(value=str(value), last_edited_at=when or now_iso(),
                                 last_edited_by=actor)

    @property
    def title(self) -> str:
        return self.value("title")

    @property
    def column(self) -> str:
        return self.value("column")

    @property
    def board(self) -> str:
        return self.value("board")

    @property
    def locked(self) -> bool:
        return bool(self.lock_value)

    def locked_by_other(self, actor: str) -> bool:
        return bool(self.lock_value) and self.lock_value != actor

    def last_edited_at(self) -> str:
        """Newest field edit time across all fields (for sorting / blame)."""
        return max((f.last_edited_at for f in self.fields.values() if f.last_edited_at),
                   default="")

    # --- serialization ---------------------------------------------------

    def to_dict(self) -> dict:
        d: dict = {"id": self.id}
        for key in FIELD_KEYS:
            d[key] = self.fields[key].to_dict()
        d["lock"] = {"value": self.lock_value, "last_edited_at": self.lock_edited_at}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        fields = {key: Field.from_dict(d.get(key)) for key in FIELD_KEYS}
        lock = d.get("lock") or {}
        if not isinstance(lock, dict):
            lock = {}
        lv = lock.get("value")
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            fields=fields,
            lock_value=(str(lv) if lv else None),
            lock_edited_at=str(lock.get("last_edited_at") or ""),
        )


@dataclass
class Board:
    name: str
    columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "columns": list(self.columns)}

    @classmethod
    def from_dict(cls, d: dict) -> "Board":
        # Preserve order, drop duplicate columns (unique per board).
        seen: set[str] = set()
        cols: list[str] = []
        for c in (d.get("columns") or []):
            c = str(c)
            if c not in seen:
                seen.add(c)
                cols.append(c)
        return cls(name=str(d.get("name") or ""), columns=cols)
