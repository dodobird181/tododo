"""One-shot migration: legacy monolithic ``*.yaml`` boards → per-item files.

Each legacy root board file (``columns:`` + ``items:``) becomes a
``boards/<stem>.yaml`` plus one ``items/<uuid>.yaml`` per item, in the new
per-field format. Idempotent: does nothing once ``items/`` is populated. Legacy
files are left in place (git history preserved) but no longer read.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import Board, Item, now_iso
from .store import ITEMS_DIR, ROOT, Store

# Legacy → new field name mapping (points / author are dropped).
_FIELD_MAP = {
    "title": "title",
    "description": "description",
    "column": "column",
    "due": "end",
    "assignee": "assigned_to",
    "reporter": "report_to",
}


def _is_legacy_board(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("items"), list) \
        and "columns" in data


def _legacy_boards() -> list[tuple[str, dict]]:
    out = []
    for p in sorted(ROOT.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if _is_legacy_board(data):
            out.append((p.stem, data))
    return out


def run(store: Store, actor: str) -> int:
    """Migrate legacy boards into the store. Returns the number of items written."""
    if any(ITEMS_DIR.glob("*.yaml")):
        return 0  # already migrated
    when = now_iso()
    count = 0
    for stem, data in _legacy_boards():
        columns = [str(c) for c in (data.get("columns") or [])]
        store.create_board(stem, columns)
        for raw in data.get("items", []):
            if not isinstance(raw, dict):
                continue
            item = Item(id=str(raw.get("id") or Item().id))
            item.set("board", stem, actor, when)
            for legacy_key, new_key in _FIELD_MAP.items():
                val = raw.get(legacy_key)
                if val not in (None, ""):
                    item.set(new_key, str(val), actor, when)
            store._write_yaml(store._item_path(item.id), item.to_dict())
            count += 1
    if count:
        store._commit(f"migrate {count} items to per-item format")
    return count
