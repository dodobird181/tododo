"""Data store: the single owner of all filesystem, git, and encryption I/O.

Items live one-per-file under ``items/<uuid>.yaml`` and boards under
``boards/<name>.yaml``. The HTTP server (``tododo.server``) is a thin routing
layer over this; nothing else touches disk. Encryption (when enabled) is applied
transparently on read/write. Every mutation is committed via :class:`GitSync`.

Locking: an item's ``lock.value`` holds the github username of the exclusive
editor. A lock older than ``lock_ttl`` seconds is considered stale and may be
reclaimed (covers crashed clients that never released).
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .crypto import Cipher, load_key
from .gitsync import GitSync
from .models import Board, Item, now_iso

ROOT = Path(__file__).resolve().parent.parent
ITEMS_DIR = ROOT / "items"
BOARDS_DIR = ROOT / "boards"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _iso_to_epoch(s: str) -> float:
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


class Store:
    def __init__(self, settings, git: GitSync):
        self.settings = settings
        self.git = git
        self.lock_ttl = float(settings.get("lock_ttl", 300) or 300)
        key = load_key(settings.get("encryption_key_file"))
        self.cipher = Cipher(key if settings.get("encryption") else None)
        # Serializes read-modify-write on a single item/board file.
        self._lock = threading.RLock()
        ITEMS_DIR.mkdir(exist_ok=True)
        BOARDS_DIR.mkdir(exist_ok=True)

    # --- low-level file I/O (encryption-aware) ---------------------------

    def _read_yaml(self, path: Path) -> dict | None:
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        try:
            data = yaml.safe_load(self.cipher.decrypt(raw).decode("utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _write_yaml(self, path: Path, data: dict) -> None:
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        blob = self.cipher.encrypt(text.encode("utf-8"))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(blob)
        tmp.replace(path)

    def _commit(self, message: str) -> None:
        self.git.push_change(message)

    # --- items -----------------------------------------------------------

    def _item_path(self, item_id: str) -> Path:
        return ITEMS_DIR / f"{item_id}.yaml"

    def get_item(self, item_id: str) -> Item | None:
        data = self._read_yaml(self._item_path(item_id))
        return Item.from_dict(data) if data else None

    def all_items(self) -> list[Item]:
        items = []
        for p in sorted(ITEMS_DIR.glob("*.yaml")):
            data = self._read_yaml(p)
            if data:
                items.append(Item.from_dict(data))
        return items

    def list_items(self, filters: dict) -> list[Item]:
        return [it for it in self.all_items() if _match(it, filters)]

    def create_item(self, actor: str, board: str, column: str, **values) -> Item:
        with self._lock:
            item = Item()
            item.set("board", board, actor)
            item.set("column", column, actor)
            for key in ("title", "description", "start", "end", "assigned_to", "report_to"):
                if key in values and values[key] is not None:
                    item.set(key, values[key], actor)
            self._write_yaml(self._item_path(item.id), item.to_dict())
        self._commit(f"create '{item.title}'")
        return item

    def update_item(self, actor: str, item_id: str, values: dict) -> Item | None:
        """Update fields. Caller must already hold the lock (enforced by server)."""
        with self._lock:
            item = self.get_item(item_id)
            if not item:
                return None
            for key, val in values.items():
                if key in item.fields and val is not None:
                    item.set(key, val, actor)
            self._write_yaml(self._item_path(item.id), item.to_dict())
        self._commit(f"edit '{item.title}'")
        return item

    def delete_item(self, item_id: str) -> bool:
        with self._lock:
            item = self.get_item(item_id)
            if not item:
                return False
            self._item_path(item_id).unlink(missing_ok=True)
        self._commit(f"delete '{item.title}'")
        return True

    # --- item locks ------------------------------------------------------

    def _lock_is_stale(self, item: Item) -> bool:
        if not item.lock_value:
            return True
        age = _now_epoch() - _iso_to_epoch(item.lock_edited_at)
        return age > self.lock_ttl

    def acquire_lock(self, actor: str, item_id: str) -> tuple[bool, str | None]:
        """Grab the lock if free/stale/already-ours. Returns (locked, holder)."""
        with self._lock:
            item = self.get_item(item_id)
            if not item:
                return False, None
            if item.lock_value and item.lock_value != actor and not self._lock_is_stale(item):
                return False, item.lock_value
            item.lock_value = actor
            item.lock_edited_at = now_iso()
            self._write_yaml(self._item_path(item.id), item.to_dict())
        self._commit(f"lock '{item.title}' by {actor}")
        return True, actor

    def release_lock(self, actor: str, item_id: str) -> bool:
        with self._lock:
            item = self.get_item(item_id)
            if not item or item.lock_value != actor:
                return False
            item.lock_value = None
            item.lock_edited_at = now_iso()
            self._write_yaml(self._item_path(item.id), item.to_dict())
        self._commit(f"unlock '{item.title}'")
        return True

    def holds_lock(self, actor: str, item_id: str) -> bool:
        item = self.get_item(item_id)
        return bool(item and item.lock_value == actor and not self._lock_is_stale(item))

    # --- history ---------------------------------------------------------

    def item_history(self, item_id: str, max_count: int = 50) -> list[dict]:
        """Commits touching an item's file, newest-first (from git)."""
        rel = str(self._item_path(item_id).relative_to(ROOT))
        raw = self.git.file_log(rel, max_count)
        events = []
        for line in raw.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                h, email, name, when = parts
                events.append({"commit": h.strip(), "email": email.strip(),
                               "name": name.strip(), "timestamp": when.strip()})
        return events

    # --- boards ----------------------------------------------------------

    def _board_path(self, name: str) -> Path:
        return BOARDS_DIR / f"{_SAFE_NAME.sub('_', name)}.yaml"

    def get_board(self, name: str) -> Board | None:
        data = self._read_yaml(self._board_path(name))
        return Board.from_dict(data) if data else None

    def list_boards(self) -> list[Board]:
        boards = []
        for p in sorted(BOARDS_DIR.glob("*.yaml")):
            data = self._read_yaml(p)
            if data:
                boards.append(Board.from_dict(data))
        return boards

    def create_board(self, name: str, columns: list[str]) -> Board | None:
        with self._lock:
            if self._board_path(name).exists():
                return None  # unique on name
            board = Board.from_dict({"name": name, "columns": columns})
            self._write_yaml(self._board_path(name), board.to_dict())
        self._commit(f"create board '{name}'")
        return board

    def update_board(self, name: str, new_name: str | None = None,
                     columns: list[str] | None = None) -> Board | None:
        with self._lock:
            board = self.get_board(name)
            if not board:
                return None
            if columns is not None:
                board = Board.from_dict({"name": board.name, "columns": columns})
            if new_name and new_name != name:
                if self._board_path(new_name).exists():
                    return None
                board.name = new_name
                self._board_path(name).unlink(missing_ok=True)
            self._write_yaml(self._board_path(board.name), board.to_dict())
        self._commit(f"edit board '{board.name}'")
        return board

    def delete_board(self, name: str) -> bool:
        with self._lock:
            if not self._board_path(name).exists():
                return False
            self._board_path(name).unlink(missing_ok=True)
        self._commit(f"delete board '{name}'")
        return True

    # --- board locks (coarse, for rename / add-column dialogs) -----------
    # Held in-memory only; a board edit is a single quick dialog on one machine.

    _board_locks: dict[str, tuple[str, float]] = {}

    def acquire_board_lock(self, actor: str, name: str) -> tuple[bool, str | None]:
        with self._lock:
            holder = self._board_locks.get(name)
            if holder and holder[0] != actor and _now_epoch() - holder[1] <= self.lock_ttl:
                return False, holder[0]
            self._board_locks[name] = (actor, _now_epoch())
            return True, actor

    def release_board_lock(self, actor: str, name: str) -> bool:
        with self._lock:
            holder = self._board_locks.get(name)
            if not holder or holder[0] != actor:
                return False
            del self._board_locks[name]
            return True


def _now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


# --- LIST filtering ------------------------------------------------------

def _match(item: Item, f: dict) -> bool:
    title, desc = item.value("title"), item.value("description")
    start, end = item.value("start"), item.value("end")
    checks = [
        ("title_contains", lambda v: v.lower() in title.lower()),
        ("title_does_not_contain", lambda v: v.lower() not in title.lower()),
        ("description_contains", lambda v: v.lower() in desc.lower()),
        ("description_does_not_contain", lambda v: v.lower() not in desc.lower()),
        ("started_before", lambda v: start != "" and start < v),
        ("started_after", lambda v: start != "" and start > v),
        ("ended_before", lambda v: end != "" and end < v),
        ("ended_after", lambda v: end != "" and end > v),
        ("in_column", lambda v: item.value("column") == v),
        ("not_in_column", lambda v: item.value("column") != v),
        ("in_board", lambda v: item.value("board") == v),
        ("not_in_board", lambda v: item.value("board") != v),
        ("locked_by", lambda v: item.lock_value == v),
        ("not_locked_by", lambda v: item.lock_value != v),
    ]
    for key, pred in checks:
        if key in f and f[key] not in (None, "") and not pred(str(f[key])):
            return False
    return True
