"""GUI entrypoint: connect to the local data server and run the pygame app.

The server (``python -m tododo.server``) owns the filesystem, git, and
encryption; the GUI is a pure HTTP client. If no server is reachable this
process starts one as a child so ``python -m tododo`` still "just works".
"""

from __future__ import annotations

import sys

from .client import Client, ensure_server
from .keybindings import Keybindings
from .settings import Settings
from .ui import App


def _startup_board(client: Client) -> str:
    """Pick the board to open: the first existing board, else a default one."""
    boards = client.list_boards()
    if boards:
        return boards[0]["name"]
    client.create_board("board", ["Todo", "Doing", "Done"])
    return "board"


def main() -> None:
    settings = Settings.load()
    client = Client(port=settings.server_port())
    child = ensure_server(client)
    if not client.ping():
        print("could not reach or start the tododo server", file=sys.stderr)
        raise SystemExit(1)
    user = client.user() or {}
    keys = Keybindings(client.get_keybindings())
    board_name = _startup_board(client)
    try:
        App(client, keys, settings, user, board_name).run()
    finally:
        if child is not None:
            child.terminate()


if __name__ == "__main__":
    main()
