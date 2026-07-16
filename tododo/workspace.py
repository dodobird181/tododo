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
    boards:
      <board id>:
        minimized: [<column name>, ...]
        opened:    <unix timestamp>   # last time this board was selected
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_WORKSPACE = {"current": "", "theme": "default", "edit_pane": False, "boards": {}}


def load_workspace(path: Path) -> dict:
    workspace = {"current": "", "theme": "default", "edit_pane": False, "boards": {}}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        workspace["current"] = str(data.get("current", ""))
        workspace["theme"] = str(data.get("theme", "default"))
        workspace["edit_pane"] = bool(data.get("edit_pane", False))
        boards = data.get("boards", {}) or {}
        for board_id, state in boards.items():
            workspace["boards"][str(board_id)] = _board_state(state)
    return workspace


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
        "boards": boards,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cleaned, sort_keys=False), encoding="utf-8")
    return cleaned
