"""Client-backed adapters that present the old ``Board``/``Item`` interface to
the pygame UI while routing all reads/writes through the HTTP :class:`Client`.

The UI keeps its "mutate the object, then call ``persist()``" style: field
setters on :class:`UiItem` buffer into ``_pending`` and update a local cache so
the change shows immediately; :meth:`UiBoard.flush` sends the buffered edits to
the server (acquiring the lock first). Structural changes (create/delete/move)
go to the server eagerly. Manual within-column reordering is not persisted — the
per-item format has no ordering field — so those calls are no-ops.

Legacy fields are mapped onto the new schema: ``due``→``end``,
``assignee``→``assigned_to``, ``reporter``→``report_to``. ``points`` and the
per-board ``authors`` registry no longer exist and are stubbed so old UI code
keeps working.
"""

from __future__ import annotations

# Legacy UI field name -> new item field name.
_ALIAS = {"due": "end", "assignee": "assigned_to", "reporter": "report_to"}


class UiItem:
    def __init__(self, data: dict, board: "UiBoard"):
        self._d = data
        self._board = board
        self._pending: dict[str, str] = {}

    @property
    def id(self) -> str:
        return self._d.get("id", "")

    def _fv(self, key: str) -> str:
        f = self._d.get(key)
        return (f or {}).get("value", "") if isinstance(f, dict) else ""

    def _set(self, key: str, value) -> None:
        value = "" if value is None else str(value)
        self._pending[key] = value
        # Reflect locally so the UI updates before the server round-trip.
        self._d.setdefault(key, {})
        if isinstance(self._d[key], dict):
            self._d[key]["value"] = value

    # --- direct fields ---------------------------------------------------

    @property
    def title(self) -> str:
        return self._fv("title")

    @title.setter
    def title(self, v):
        self._set("title", v)

    @property
    def description(self) -> str:
        return self._fv("description")

    @description.setter
    def description(self, v):
        self._set("description", v)

    @property
    def column(self) -> str:
        return self._fv("column")

    @column.setter
    def column(self, v):
        self._set("column", v)

    @property
    def start(self) -> str:
        return self._fv("start")

    # --- aliased legacy fields ------------------------------------------

    @property
    def due(self) -> str:
        return self._fv("end")

    @due.setter
    def due(self, v):
        self._set("end", v)

    @property
    def assignee(self) -> str:
        return self._fv("assigned_to")

    @assignee.setter
    def assignee(self, v):
        self._set("assigned_to", v)

    @property
    def reporter(self) -> str:
        return self._fv("report_to")

    @reporter.setter
    def reporter(self, v):
        self._set("report_to", v)

    # --- provenance / removed features ----------------------------------

    @property
    def author(self) -> str:
        """Best-effort creator: the most recent editor across fields."""
        best_at, best_by = "", ""
        for key in ("title", "column", "board", "description"):
            f = self._d.get(key)
            if isinstance(f, dict) and f.get("last_edited_at", "") >= best_at:
                best_at, best_by = f.get("last_edited_at", ""), f.get("last_edited_by", "")
        return best_by

    @property
    def points(self) -> int:
        return 0  # points removed from the schema

    def last_edited_at(self) -> str:
        return max((f.get("last_edited_at", "") for f in self._d.values()
                    if isinstance(f, dict)), default="")

    # --- locking ---------------------------------------------------------

    @property
    def lock_value(self) -> str | None:
        lock = self._d.get("lock") or {}
        return lock.get("value") if isinstance(lock, dict) else None

    def locked_by_other(self, actor: str) -> bool:
        return bool(self.lock_value) and self.lock_value != actor

    def to_dict(self) -> dict:
        return self._d

    # --- flush -----------------------------------------------------------

    def flush(self, client, actor: str) -> None:
        if not self._pending:
            return
        client.lock_item(self.id)  # ensure we hold the lock before editing
        client.update_item(self.id, **self._pending)
        self._pending.clear()


class UiBoard:
    """Presents one board (by name) as the old Board object, backed by the server."""

    # Authors registry is gone; expose an empty stub so old UI code is a no-op.
    authors: dict = {}

    def __init__(self, client, name: str, actor: str):
        self.client = client
        self.name = name
        self.actor = actor
        self.columns: list[str] = []
        self.items: list[UiItem] = []
        self.refresh()

    # --- loading ---------------------------------------------------------

    def refresh(self) -> None:
        board = self.client.get_board(self.name)
        self.columns = list(board.get("columns", [])) if board else []
        raw = self.client.list_items({"in_board": self.name})
        # Stable order: by column, then creation-ish (last_edited fallback), then id.
        items = [UiItem(d, self) for d in raw]
        self.items = items

    # --- queries ---------------------------------------------------------

    def items_in(self, column: str) -> list[UiItem]:
        return [it for it in self.items if it.column == column]

    def find(self, item_id: str) -> UiItem | None:
        return next((it for it in self.items if it.id == item_id), None)

    # --- mutations (eager to the server) --------------------------------

    def create(self, title: str, column: str | None = None, points: int = 0,
               description: str = "", author: str = "") -> UiItem:
        col = column or (self.columns[0] if self.columns else "")
        data = self.client.create_item(board=self.name, column=col,
                                        title=title, description=description)
        item = UiItem(data or {"id": ""}, self)
        self.items.append(item)
        return item

    def delete(self, item_id: str) -> None:
        self.client.lock_item(item_id)
        self.client.delete_item(item_id)
        self.items = [it for it in self.items if it.id != item_id]

    def move_to_column(self, item_id: str, column: str) -> None:
        item = self.find(item_id)
        if item and column in self.columns and item.column != column:
            item.column = column  # buffered; flushed on persist

    def move_relative(self, item_id: str, delta: int) -> None:
        item = self.find(item_id)
        if not item or item.column not in self.columns:
            return
        idx = self.columns.index(item.column)
        new_idx = max(0, min(len(self.columns) - 1, idx + delta))
        if new_idx != idx:
            item.column = self.columns[new_idx]

    def move_within_column(self, item_id: str, delta: int) -> bool:
        return False  # no ordering field in the per-item format

    def reorder(self, item_id: str, column: str, position: int) -> None:
        # Only the column change is meaningful; position isn't persisted.
        self.move_to_column(item_id, column)

    # --- authors stubs (registry removed) --------------------------------

    def register_author(self, email: str, name: str = "") -> bool:
        return False

    def set_author_meta(self, email: str, **fields) -> bool:
        return False

    # --- flush pending field edits --------------------------------------

    def flush(self) -> None:
        for it in list(self.items):
            it.flush(self.client, self.actor)
