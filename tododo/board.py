"""Board data model. A single YAML file is the source of truth."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_COLUMNS = ["Todo", "Doing", "Done"]


@dataclass
class Item:
    title: str
    column: str
    points: int = 0
    description: str = ""
    # Git identity of whoever created the item (for avatars). Optional so legacy
    # items stay clean; only serialized when set.
    author: str = ""
    author_email: str = ""
    # Unix timestamp of the last create/edit/move; 0 means unknown.
    updated: float = 0.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def touch(self) -> None:
        self.updated = time.time()

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "points": self.points,
            "description": self.description,
            "column": self.column,
        }
        if self.author:
            d["author"] = self.author
        if self.author_email:
            d["author_email"] = self.author_email
        if self.updated:
            d["updated"] = round(self.updated, 3)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        return cls(
            id=str(d.get("id", uuid.uuid4().hex)),
            title=str(d.get("title", "")),
            points=int(d.get("points", 0) or 0),
            description=str(d.get("description", "") or ""),
            author=str(d.get("author", "") or ""),
            author_email=str(d.get("author_email", "") or ""),
            updated=float(d.get("updated", 0) or 0),
            column=str(d.get("column", DEFAULT_COLUMNS[0])),
        )


@dataclass
class Board:
    path: Path
    columns: list[str] = field(default_factory=lambda: list(DEFAULT_COLUMNS))
    items: list[Item] = field(default_factory=list)

    # --- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Board":
        path = Path(path)
        if not path.exists():
            board = cls(path=path)
            board.save()
            return board
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        columns = data.get("columns") or list(DEFAULT_COLUMNS)
        items = [Item.from_dict(d) for d in (data.get("items") or [])]
        # Dedupe by id (keep first). A line-based git merge's main corruption mode
        # is a duplicated item block; drop the copies so the board stays sane.
        seen: set[str] = set()
        deduped = []
        for it in items:
            if it.id in seen:
                continue
            seen.add(it.id)
            deduped.append(it)
        items = deduped
        # Drop items whose column no longer exists, snapping them to first column.
        for it in items:
            if it.column not in columns:
                it.column = columns[0]
        return cls(path=path, columns=list(columns), items=items)

    def save(self) -> None:
        data = {"columns": self.columns, "items": [it.to_dict() for it in self.items]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
        tmp.replace(self.path)

    # --- queries ---------------------------------------------------------

    def items_in(self, column: str) -> list[Item]:
        return [it for it in self.items if it.column == column]

    def find(self, item_id: str) -> Item | None:
        return next((it for it in self.items if it.id == item_id), None)

    # --- mutations -------------------------------------------------------

    def create(self, title: str, column: str | None = None, points: int = 0,
               description: str = "", author: str = "", author_email: str = "") -> Item:
        item = Item(title=title, column=column or self.columns[0], points=points,
                    description=description, author=author, author_email=author_email)
        item.touch()
        self.items.append(item)
        return item

    def delete(self, item_id: str) -> None:
        self.items = [it for it in self.items if it.id != item_id]

    def move_to_column(self, item_id: str, column: str) -> None:
        item = self.find(item_id)
        if item and column in self.columns and item.column != column:
            item.column = column
            item.touch()

    def move_relative(self, item_id: str, delta: int) -> None:
        """Shift an item delta columns left (-1) or right (+1)."""
        item = self.find(item_id)
        if not item:
            return
        idx = self.columns.index(item.column)
        new_idx = max(0, min(len(self.columns) - 1, idx + delta))
        if new_idx != idx:
            item.column = self.columns[new_idx]
            item.touch()

    def move_within_column(self, item_id: str, delta: int) -> bool:
        """Shift an item up (-1) or down (+1) among its column siblings.

        Returns True if the item actually moved.
        """
        item = self.find(item_id)
        if not item:
            return False
        siblings = self.items_in(item.column)
        idx = siblings.index(item)
        target = idx + delta
        if target < 0 or target >= len(siblings):
            return False
        self.reorder(item_id, item.column, target)
        return True

    def reorder(self, item_id: str, column: str, position: int) -> None:
        """Move item into column at a given index among that column's items."""
        item = self.find(item_id)
        if not item or column not in self.columns:
            return
        self.items.remove(item)
        item.column = column
        item.touch()
        # Rebuild list preserving order, inserting at the requested slot.
        col_items = [it for it in self.items if it.column == column]
        position = max(0, min(len(col_items), position))
        if position >= len(col_items):
            anchor = None
        else:
            anchor = col_items[position]
        if anchor is None:
            self.items.append(item)
        else:
            self.items.insert(self.items.index(anchor), item)
