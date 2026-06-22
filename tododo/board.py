"""Board data model. A single YAML file is the source of truth."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_COLUMNS = ["Todo", "Doing", "Done"]
DEFAULT_RELATIONSHIPS = ["Assigned to", "Reporter"]


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
    # Lifecycle timestamps (unix seconds; 0 == unknown) and blame.
    created: float = 0.0   # when the item was created
    edited: float = 0.0    # last title/description/points edit
    moved: float = 0.0     # last column change
    moved_to: str = ""     # column the item was last moved into
    viewed: float = 0.0    # last time the edit view was opened+closed with no change
    actor: str = ""        # who performed the most recent action (display name)
    # Optional due date as an ISO ``YYYY-MM-DD`` string; "" == none.
    due: str = ""
    # Per-item relationships: {relationship name -> collaborator github username}.
    # Keys come from the board's top-level ``relationships`` list, values from its
    # ``collaborators`` list. Only set entries are stored.
    relationships: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def mark_created(self, actor: str = "") -> None:
        self.created = self.edited = time.time()
        self.actor = actor

    def mark_edited(self, actor: str = "") -> None:
        self.edited = time.time()
        self.actor = actor

    def mark_moved(self, column: str, actor: str = "") -> None:
        self.moved = time.time()
        self.moved_to = column
        self.actor = actor

    def mark_viewed(self) -> None:
        """Record that the item was opened and closed without an edit."""
        self.viewed = time.time()

    @property
    def last_active(self) -> float:
        """Timestamp of the most recent action of any kind."""
        return max(self.created, self.edited, self.moved, self.viewed)

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
        if self.created:
            d["created"] = round(self.created, 3)
        if self.edited:
            d["edited"] = round(self.edited, 3)
        if self.moved:
            d["moved"] = round(self.moved, 3)
        if self.moved_to:
            d["moved_to"] = self.moved_to
        if self.viewed:
            d["viewed"] = round(self.viewed, 3)
        if self.actor:
            d["actor"] = self.actor
        if self.due:
            d["due"] = self.due
        rels = {k: v for k, v in self.relationships.items() if v}
        if rels:
            d["relationships"] = rels
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        # Migrate the legacy single ``updated`` field onto the new timestamps.
        legacy = float(d.get("updated", 0) or 0)
        return cls(
            id=str(d.get("id", uuid.uuid4().hex)),
            title=str(d.get("title", "")),
            points=int(d.get("points", 0) or 0),
            description=str(d.get("description", "") or ""),
            author=str(d.get("author", "") or ""),
            author_email=str(d.get("author_email", "") or ""),
            created=float(d.get("created", legacy) or 0),
            edited=float(d.get("edited", legacy) or 0),
            moved=float(d.get("moved", 0) or 0),
            moved_to=str(d.get("moved_to", "") or ""),
            viewed=float(d.get("viewed", 0) or 0),
            actor=str(d.get("actor", "") or ""),
            due=str(d.get("due", "") or ""),
            relationships={str(k): str(v) for k, v in (d.get("relationships") or {}).items()},
            column=str(d.get("column", DEFAULT_COLUMNS[0])),
        )


@dataclass
class Board:
    path: Path
    columns: list[str] = field(default_factory=lambda: list(DEFAULT_COLUMNS))
    items: list[Item] = field(default_factory=list)
    # Registry of contributors, keyed by email: {name, github, avatar_url}.
    authors: dict[str, dict] = field(default_factory=dict)
    # Relationship kinds an item may declare (e.g. "Assigned to", "Reporter").
    relationships: list[str] = field(default_factory=lambda: list(DEFAULT_RELATIONSHIPS))
    # Github usernames assignable as relationship values.
    collaborators: list[str] = field(default_factory=list)

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
        authors = data.get("authors") or {}
        relationships = data.get("relationships") or list(DEFAULT_RELATIONSHIPS)
        collaborators = data.get("collaborators") or []
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
        return cls(path=path, columns=list(columns), items=items,
                   authors=dict(authors),
                   relationships=[str(r) for r in relationships],
                   collaborators=[str(c) for c in collaborators])

    def save(self) -> None:
        data: dict = {"columns": self.columns}
        if self.authors:
            data["authors"] = self.authors
        if self.relationships:
            data["relationships"] = self.relationships
        if self.collaborators:
            data["collaborators"] = self.collaborators
        data["items"] = [it.to_dict() for it in self.items]
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
        tmp.replace(self.path)

    # --- authors registry ------------------------------------------------

    def register_author(self, email: str, name: str = "") -> bool:
        """Ensure an author exists in the registry. Returns True if it changed."""
        if not email:
            return False
        rec = self.authors.get(email)
        if rec is None:
            self.authors[email] = {"name": name} if name else {}
            return True
        if name and rec.get("name") != name:
            rec["name"] = name
            return True
        return False

    def set_author_meta(self, email: str, **fields) -> bool:
        """Update an author's github/avatar_url etc. Returns True if it changed."""
        if not email:
            return False
        rec = self.authors.setdefault(email, {})
        changed = False
        for key, value in fields.items():
            if value and rec.get(key) != value:
                rec[key] = value
                changed = True
        return changed

    # --- queries ---------------------------------------------------------

    def items_in(self, column: str) -> list[Item]:
        return [it for it in self.items if it.column == column]

    def find(self, item_id: str) -> Item | None:
        return next((it for it in self.items if it.id == item_id), None)

    # --- mutations -------------------------------------------------------

    def create(self, title: str, column: str | None = None, points: int = 0,
               description: str = "", author: str = "", author_email: str = "",
               actor: str = "") -> Item:
        item = Item(title=title, column=column or self.columns[0], points=points,
                    description=description, author=author, author_email=author_email)
        item.mark_created(actor or author)
        self.items.append(item)
        return item

    def delete(self, item_id: str) -> None:
        self.items = [it for it in self.items if it.id != item_id]

    def move_to_column(self, item_id: str, column: str, actor: str = "") -> None:
        item = self.find(item_id)
        if item and column in self.columns and item.column != column:
            item.column = column
            item.mark_moved(column, actor)

    def move_relative(self, item_id: str, delta: int, actor: str = "") -> None:
        """Shift an item delta columns left (-1) or right (+1)."""
        item = self.find(item_id)
        if not item:
            return
        idx = self.columns.index(item.column)
        new_idx = max(0, min(len(self.columns) - 1, idx + delta))
        if new_idx != idx:
            item.column = self.columns[new_idx]
            item.mark_moved(item.column, actor)

    def move_within_column(self, item_id: str, delta: int, actor: str = "") -> bool:
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
        self.reorder(item_id, item.column, target, actor)
        return True

    def reorder(self, item_id: str, column: str, position: int, actor: str = "") -> None:
        """Move item into column at a given index among that column's items."""
        item = self.find(item_id)
        if not item or column not in self.columns:
            return
        old_column = item.column
        self.items.remove(item)
        item.column = column
        if column != old_column:  # a cross-column move is blame-worthy; a reorder isn't
            item.mark_moved(column, actor)
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
