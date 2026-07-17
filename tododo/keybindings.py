"""
Local keybinding config.

A plaintext YAML file at `userdata/keybindings.yaml` (gitignored, per-machine),
seeded from `DEFAULT_KEYBINDINGS` and described by `KEYBINDINGS_HELP`. These are
UI mnemonics local to one machine, so they are deliberately *not* event-sourced
— they mirror the OLD app's `keybindings.yaml` rather than syncing through the
event log.

Each action is persisted as a `{value, help}` pair so the YAML is
self-documenting (and a future keybindings UI can show what each action does).
The `help` text is authoritative in code and rewritten on every save. The API,
however, still exposes the *flat* `{action: key}` mapping the web UI's leader
model consumes — `load_keybindings` unwraps the pairs on the way out.

Values are the `e.key` pressed after the leader (a single character such as `n`
or `C`, or a name like `Enter`/`ArrowLeft`); `" "` is the space bar.
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_KEYBINDINGS = {
    "open_palette": " ",
    "create": "n",
    "delete": "d",
    "new_board": "m",
    "delete_board": "M",
    "new_column": "c",
    "switch_board": "b",
    "keybindings": "k",
    "view_yaml": "y",
    "search": "f",
    "due_date": "w",
    "themes": "t",
    "column_colors": "o",
    "calendar": "C",
    "kanban": "K",
    "reset_cal_cursor": "Escape",
    "confirm": "Enter",
    "cancel": "Escape",
}

KEYBINDINGS_HELP = {
    "open_palette": "Open the command palette.",
    "create": "Create a new item in the focused column.",
    "delete": "Delete the selected item(s).",
    "new_board": "Create a new board.",
    "delete_board": "Delete the current board.",
    "new_column": "Add a new column to the current board.",
    "switch_board": "Open the board switcher.",
    "keybindings": "Open the keybindings editor.",
    "view_yaml": "View the selected item's raw JSON.",
    "search": "Search items on the board by title.",
    "due_date": "Set the due date (end) of the selected item.",
    "themes": "Open the theme switcher.",
    "column_colors": "Edit the per-workspace column colours.",
    "calendar": "Toggle calendar mode: an agenda of items that have both a start and an end.",
    "kanban": "Switch from calendar mode back to the kanban board.",
    "reset_cal_cursor": "Collapse the calendar selection back down to the cursor.",
    "confirm": "Confirm / submit in dialogs.",
    "cancel": "Cancel / dismiss dialogs and exit modes.",
}


def load_keybindings(path: Path) -> dict[str, str]:
    """
    Read the mapping as the flat `{action: key}` the UI expects, backfilling any
    missing action from the defaults so the UI never sees a hole. Accepts both
    the structured `{value, help}` form and a legacy flat file.
    """
    merged = dict(DEFAULT_KEYBINDINGS)
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for action, entry in data.items():
            merged[action] = str(_value_of(entry))
    return merged


def save_keybindings(path: Path, mapping: dict) -> dict[str, str]:
    """
    Persist the known actions as `{value, help}` pairs (help taken from
    `KEYBINDINGS_HELP`) and return the flat mapping, merged over defaults. Only
    the known actions are written; `mapping` may be structured or flat.
    """
    structured = {}
    for action in DEFAULT_KEYBINDINGS:
        key = str(_value_of(mapping[action])) if action in mapping else DEFAULT_KEYBINDINGS[action]
        structured[action] = {"value": key, "help": KEYBINDINGS_HELP.get(action, "")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(structured, sort_keys=False), encoding="utf-8")
    return load_keybindings(path)


def _value_of(entry):
    if isinstance(entry, dict) and "value" in entry:
        return entry["value"]
    return entry
