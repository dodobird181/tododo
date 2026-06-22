"""Semantic 3-way merge of the board by item ``id``.

Instead of letting git text-merge ``board.yaml`` line-by-line (which produces
false conflicts for unrelated edits and reordering), we merge the parsed board
structurally: items are keyed by ``id`` and reconciled against a common
ancestor. Only true same-item collisions become :class:`Conflict`s for the user
to resolve; everything else merges automatically.

Boards are plain dicts: ``{"columns": [...], "items": [ {id, title, points,
description, column}, ... ]}`` — i.e. the YAML shape produced by
:meth:`tododo.board.Board.save`.
"""

from __future__ import annotations

from dataclasses import dataclass

# Choices the UI can return for a conflict.
CURRENT = "current"
INCOMING = "incoming"
BOTH = "both"


@dataclass
class Conflict:
    id: str
    kind: str               # "edit" | "edit_delete" | "add"
    ours: dict | None       # None => deleted on our side
    theirs: dict | None     # None => deleted on their side
    base: dict | None = None

    @property
    def title(self) -> str:
        item = self.ours or self.theirs or {}
        return str(item.get("title", "(untitled)"))


def _by_id(board: dict | None) -> dict[str, dict]:
    if not board:
        return {}
    return {str(it["id"]): it for it in board.get("items", []) if "id" in it}


def _columns(base, ours, theirs) -> list[str]:
    b = (base or {}).get("columns")
    o = (ours or {}).get("columns")
    t = (theirs or {}).get("columns")
    if o == t:
        return list(o or [])
    if o == b:
        return list(t or [])
    if t == b:
        return list(o or [])
    return list(o or [])  # both diverged from base: keep ours


def merge_boards(base: dict | None, ours: dict, theirs: dict) -> tuple[dict, list[Conflict]]:
    """Merge ``ours`` and ``theirs`` against common ancestor ``base``.

    Returns ``(merged_board, conflicts)``. Conflicted items are left as OURS in
    the merged result; resolutions are applied later via :func:`apply_resolutions`.
    """
    base_items = _by_id(base)
    our_items = _by_id(ours)
    their_items = _by_id(theirs)
    all_ids = set(base_items) | set(our_items) | set(their_items)

    merged: dict[str, dict] = {}
    conflicts: list[Conflict] = []

    for item_id in all_ids:
        b = base_items.get(item_id)
        o = our_items.get(item_id)
        t = their_items.get(item_id)

        if o == t:
            if o is not None:           # both identical (or both deleted)
                merged[item_id] = o
            continue

        if b is None:
            # Added on at least one side (not in ancestor).
            if o is None:
                merged[item_id] = t     # added by them only
            elif t is None:
                merged[item_id] = o     # added by us only
            else:
                conflicts.append(Conflict(item_id, "add", o, t))
                merged[item_id] = o
            continue

        # Existed in the ancestor.
        if o is None or t is None:
            # Deleted on one side.
            other = t if o is None else o
            if other == b:
                continue                # deleted one side, untouched other => delete
            conflicts.append(Conflict(item_id, "edit_delete", o, t, b))
            if o is not None:
                merged[item_id] = o     # tentatively keep ours
            continue

        # Edited on at least one side.
        if o == b:
            merged[item_id] = t         # only they changed it
        elif t == b:
            merged[item_id] = o         # only we changed it
        else:
            conflicts.append(Conflict(item_id, "edit", o, t, b))
            merged[item_id] = o         # tentatively keep ours

    columns = _columns(base, ours, theirs)
    ordered = _order_items(merged, theirs, ours, columns)
    return {"columns": columns, "items": ordered}, conflicts


def _order_items(merged: dict[str, dict], theirs: dict, ours: dict, columns) -> list[dict]:
    """Order surviving items using theirs as the spine, appending ours-only items.

    Last-writer-wins on ordering: incoming order is the backbone; items that
    only we still have are appended after, preserving our relative order.
    """
    their_order = [str(it["id"]) for it in (theirs or {}).get("items", []) if "id" in it]
    our_order = [str(it["id"]) for it in (ours or {}).get("items", []) if "id" in it]

    seen: set[str] = set()
    sequence: list[str] = []
    for item_id in their_order:
        if item_id in merged and item_id not in seen:
            sequence.append(item_id)
            seen.add(item_id)
    for item_id in our_order:
        if item_id in merged and item_id not in seen:
            sequence.append(item_id)
            seen.add(item_id)
    # Any remaining (e.g. only-theirs adds not in either order list) at the end.
    for item_id in merged:
        if item_id not in seen:
            sequence.append(item_id)
            seen.add(item_id)

    known = set(columns)
    items = []
    for item_id in sequence:
        item = dict(merged[item_id])
        if item.get("column") not in known and columns:
            item["column"] = columns[0]
        items.append(item)
    return items


def apply_resolutions(merged: dict, conflicts: list[Conflict],
                      choices: dict[str, str], new_id) -> dict:
    """Return a new board with the user's per-conflict ``choices`` applied.

    ``choices`` maps conflict id -> CURRENT / INCOMING / BOTH. ``new_id`` is a
    callable returning a fresh unique id (used for BOTH on edit/add conflicts).
    """
    items_by_id = {str(it["id"]): dict(it) for it in merged.get("items", [])}
    order = [str(it["id"]) for it in merged.get("items", [])]
    extras: list[dict] = []

    for c in conflicts:
        choice = choices.get(c.id, CURRENT)
        if choice == INCOMING:
            if c.theirs is None:
                items_by_id.pop(c.id, None)
            else:
                items_by_id[c.id] = dict(c.theirs)
        elif choice == BOTH:
            # Keep current as-is; clone incoming as a brand-new item.
            if c.ours is not None:
                items_by_id[c.id] = dict(c.ours)
            if c.theirs is not None:
                clone = dict(c.theirs)
                clone["id"] = new_id()
                extras.append(clone)
        else:  # CURRENT
            if c.ours is None:
                items_by_id.pop(c.id, None)
            else:
                items_by_id[c.id] = dict(c.ours)

    items = [items_by_id[i] for i in order if i in items_by_id]
    items.extend(extras)
    return {"columns": list(merged.get("columns", [])), "items": items}
