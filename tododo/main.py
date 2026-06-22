"""Entrypoint: wire board, keybindings, git sync, hooks, and the UI."""

from __future__ import annotations

from pathlib import Path

from .board import Board
from .githooks import install_hooks
from .gitsync import GitSync
from .keybindings import Keybindings
from .settings import Settings
from .ui import App

ROOT = Path(__file__).resolve().parent.parent
BOARD_PATH = ROOT / "board.yaml"


def main() -> None:
    install_hooks(ROOT)
    board = Board.load(BOARD_PATH)
    keys = Keybindings.load()
    settings = Settings.load()
    git = GitSync(ROOT, BOARD_PATH)
    git.start()
    app = App(board, keys, git, settings)
    app.run()


if __name__ == "__main__":
    main()
