"""Entrypoint: wire board, keybindings, git sync, hooks, and the UI."""

from __future__ import annotations

from pathlib import Path

from .board import Board
from .githooks import install_hooks
from .gitsync import GitSync
from .keybindings import Keybindings
from .settings import Settings
from .ui import App
from .webhook import WebhookServer

ROOT = Path(__file__).resolve().parent.parent
BOARD_PATH = ROOT / "board.yaml"


def main() -> None:
    install_hooks(ROOT)
    board = Board.load(BOARD_PATH)
    keys = Keybindings.load()
    settings = Settings.load()
    git = GitSync(ROOT, BOARD_PATH, merge_option=settings.merge_option(),
                  push_interval=settings.push_interval(),
                  poll_interval=settings.poll_interval())
    git.start()

    webhook = None
    if settings.webhook_enabled:
        webhook = WebhookServer(settings.webhook_port(), settings.webhook_secret(),
                                on_event=git.request_sync)
        if not webhook.start():
            git._set_status(f"git: webhook port {settings.webhook_port()} unavailable")
            webhook = None

    try:
        app = App(board, keys, git, settings)
        app.run()
    finally:
        if webhook:
            webhook.stop()


if __name__ == "__main__":
    main()
