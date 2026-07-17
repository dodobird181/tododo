"""
Local workspace config.

A plaintext YAML file at `userdata/workspace.yaml` (gitignored, per-machine),
mirroring the OLD app's workspace file. Holds view state that is *not* part of
the shared domain: which columns are minimized per board, the active board, and
the selected theme. Deliberately not event-sourced.

Schema::

    current:   <board id>
    theme:     <theme name>
    edit_pane: <bool>              # edit items in a right-hand pane vs a modal
    edit_pane_width: <int>         # width in px of the right-hand edit pane
    calendar_mode: <bool>          # whether the calendar view (vs kanban) is active
    column_colors: [<css color>, ...]  # per-workspace overrides for the 8 positional
                                       # column colours (--col1..8); blank entry = theme default
    boards:
      <board id>:
        minimized: [<column name>, ...]
        opened:    <unix timestamp>   # last time this board was selected
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_WORKSPACE = {"current": "", "theme": "default", "edit_pane": False, "edit_pane_width": 400, "calendar_mode": False, "column_colors": [], "boards": {}}


def load_workspace(path: Path) -> dict:
    workspace = {"current": "", "theme": "default", "edit_pane": False, "edit_pane_width": 400, "calendar_mode": False, "column_colors": [], "boards": {}}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        workspace["current"] = str(data.get("current", ""))
        workspace["theme"] = str(data.get("theme", "default"))
        workspace["edit_pane"] = bool(data.get("edit_pane", False))
        workspace["edit_pane_width"] = _as_pane_width(data.get("edit_pane_width"))
        workspace["calendar_mode"] = bool(data.get("calendar_mode", False))
        workspace["column_colors"] = _as_column_colors(data.get("column_colors"))
        boards = data.get("boards", {}) or {}
        for board_id, state in boards.items():
            workspace["boards"][str(board_id)] = _board_state(state)
    return workspace


def _as_column_colors(value) -> list[str]:
    """
    Up to 8 CSS-colour strings overriding the positional column colours; a blank
    or missing entry falls back to the active theme's default.
    """
    if not isinstance(value, list):
        return []
    return [str(entry).strip() for entry in value[:8]]


def _as_pane_width(value) -> int:
    """
    Coerce the stored pane width to a sane pixel integer, defaulting to 400.
    """
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 400


def _board_state(state: dict | None) -> dict:
    state = state or {}
    minimized = state.get("minimized", []) or []
    result = {"minimized": [str(name) for name in minimized]}
    if state.get("opened") is not None:
        result["opened"] = float(state["opened"])
    return result


def save_workspace(path: Path, data: dict) -> dict:
    boards = {}
    for board_id, state in (data.get("boards", {}) or {}).items():
        boards[str(board_id)] = _board_state(state)
    cleaned = {
        "current": str(data.get("current", "")),
        "theme": str(data.get("theme", "default")),
        "edit_pane": bool(data.get("edit_pane", False)),
        "edit_pane_width": _as_pane_width(data.get("edit_pane_width")),
        "calendar_mode": bool(data.get("calendar_mode", False)),
        "column_colors": _as_column_colors(data.get("column_colors")),
        "boards": boards,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cleaned, sort_keys=False), encoding="utf-8")
    return cleaned
