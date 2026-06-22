"""Entrypoint: wire board, keybindings, git sync, hooks, and the UI."""

from __future__ import annotations

from pathlib import Path

from .board import Board
from .githooks import install_hooks
from .gitsync import GitSync
from .keybindings import Keybindings
from .settings import Settings
from .ui import App
from .workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
BOARD_PATH = ROOT / "board.yaml"


def _startup_board() -> Path:
    """Reopen the last board the user looked at (workspace ``current``), falling
    back to the default ``board.yaml`` when none is recorded or the file is gone."""
    stem = Workspace.load().current
    if stem:
        path = ROOT / f"{Path(stem).name}.yaml"
        if path.exists():
            return path
    return BOARD_PATH


def main() -> None:
    install_hooks(ROOT)
    board = Board.load(_startup_board())
    keys = Keybindings.load()
    settings = Settings.load()
    git = GitSync(ROOT, board.path, merge_option=settings.merge_option(),
                  push_interval=settings.push_interval(),
                  poll_interval=settings.poll_interval(),
                  poll_backoff_max=settings.poll_backoff_max())
    git.start()
    App(board, keys, git, settings).run()


if __name__ == "__main__":
    main()
