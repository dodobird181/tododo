"""Local, non-version-controlled UI state (gitignored ``workspace.yaml``).

Holds per-machine view preferences that shouldn't be shared via git — currently
the set of minimized columns, namespaced per board (since one machine may switch
between several board files via CTRL+B). Stale references (e.g. a column that was
deleted from a board) are pruned on save.

Layout::

    current: roadmap   # board file stem opened on next startup (last one used)
    boards:
      board:        # board file stem (board.yaml -> "board")
        minimized: [Done]
        opened: 1782132995.9   # unix time the board was last opened (for ordering)
      roadmap:
        minimized: [Backlog, Done]
"""

from __future__ import annotations

import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
USERDATA = ROOT / "userdata"
PATH = USERDATA / "workspace.yaml"
_LEGACY_PATH = ROOT / "workspace.yaml"  # pre-migration location


class Workspace:
    def __init__(self, boards: dict | None = None, current: str | None = None):
        # board name -> {"minimized": set[str], "opened": float}
        self.boards: dict[str, dict] = boards or {}
        # board stem to reopen on startup (the last board the user looked at).
        self.current: str | None = current

    def _entry(self, board: str) -> dict:
        return self.boards.setdefault(board, {"minimized": set(), "opened": 0.0, "self_added": False})

    @classmethod
    def load(cls) -> "Workspace":
        USERDATA.mkdir(exist_ok=True)
        # Migrate legacy workspace.yaml from repo root to userdata/ on first load.
        if _LEGACY_PATH.exists() and not PATH.exists():
            _LEGACY_PATH.rename(PATH)
        data = {}
        if PATH.exists():
            try:
                data = yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
        boards: dict[str, dict] = {}
        for name, cfg in (data.get("boards") or {}).items():
            cfg = cfg or {}
            boards[str(name)] = {
                "minimized": set(cfg.get("minimized") or []),
                "opened": float(cfg.get("opened") or 0.0),
                "self_added": bool(cfg.get("self_added")),
            }
        # Migrate the legacy un-namespaced ``minimized`` list onto the default board.
        if "boards" not in data and data.get("minimized"):
            boards["board"] = {"minimized": set(data.get("minimized") or []), "opened": 0.0}
        current = data.get("current")
        return cls(boards, str(current) if current else None)

    def minimized(self, board: str) -> set[str]:
        """The (live, mutable) minimized-column set for a board."""
        return self._entry(board)["minimized"]

    def opened(self, board: str) -> float:
        return self._entry(board).get("opened", 0.0)

    def self_added(self, board: str) -> bool:
        """Whether the local user has already been auto-added to this board's
        collaborators (one-shot, so a later manual removal isn't undone)."""
        return bool(self._entry(board).get("self_added"))

    def mark_self_added(self, board: str) -> None:
        self._entry(board)["self_added"] = True

    def touch_opened(self, board: str, ts: float | None = None) -> None:
        self._entry(board)["opened"] = time.time() if ts is None else ts
        self.current = board  # the just-opened board becomes the startup target

    def save(self, board: str | None = None, valid_columns=None) -> None:
        if board is not None and valid_columns is not None:
            self.minimized(board).intersection_update(valid_columns)  # drop deleted columns
        boards = {}
        for name, cfg in self.boards.items():
            entry = {}
            if cfg.get("minimized"):
                entry["minimized"] = sorted(cfg["minimized"])
            if cfg.get("opened"):
                entry["opened"] = round(cfg["opened"], 3)
            if cfg.get("self_added"):
                entry["self_added"] = True
            if entry:
                boards[name] = entry
        data: dict = {}
        if self.current:
            data["current"] = self.current
        data["boards"] = boards
        USERDATA.mkdir(exist_ok=True)
        tmp = PATH.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        tmp.replace(PATH)
