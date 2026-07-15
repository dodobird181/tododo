"""Command-line board access for agents / automation.

Lets a non-interactive caller (an LLM agent, a script, a cron loop) inspect and
advance board items without a GUI. It is a thin CLI over the same local HTTP
server the pygame app uses (:mod:`tododo.server`), so the server stays the sole
owner of the filesystem, git, and encryption — no second process races it. If no
server is running, one is started automatically (and left running).

The server owns identity (its git github-username) and auto-commits every
mutation, so there is no ``--actor`` or ``--commit`` flag; attribution and
history come from git via the server.

Usage::

    python -m tododo.agent list   [--board B] [--column C] [--search KW]
    python -m tododo.agent boards
    python -m tododo.agent columns --board B
    python -m tododo.agent show <id>
    python -m tododo.agent create <title> [--board B] [--column C] [--description D]
    python -m tododo.agent move   <id> <column>
    python -m tododo.agent edit   <id> [--title T] [--description D]
"""

from __future__ import annotations

import argparse
import json
import sys

import yaml

from .client import Client, ensure_server
from .settings import Settings


def _err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def _field(item: dict, key: str) -> str:
    """Read a provenance-tracked field's ``value`` from an item dict."""
    f = item.get(key)
    return f.get("value", "") if isinstance(f, dict) else ""


def _summary(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "title": _field(item, "title"),
        "board": _field(item, "board"),
        "column": _field(item, "column"),
        "description": _field(item, "description"),
    }


def _print_json(obj) -> None:
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _with_lock(client: Client, item_id: str, fn):
    """Acquire the item lock, run ``fn`` (returns exit code), always release."""
    locked, holder = client.lock_item(item_id)
    if not locked:
        return _err(f"item {item_id} is locked by {holder or 'another user'}")
    try:
        return fn()
    finally:
        client.unlock_item(item_id)


# --- commands ------------------------------------------------------------

def cmd_list(args, client: Client) -> int:
    filters: dict = {}
    if args.board:
        filters["in_board"] = args.board
    if args.column:
        filters["in_column"] = args.column
    if args.search:
        filters["title_contains"] = args.search
    items = client.list_items(filters)
    _print_json([_summary(it) for it in items])
    return 0


def cmd_boards(args, client: Client) -> int:
    _print_json(client.list_boards())
    return 0


def cmd_columns(args, client: Client) -> int:
    board = client.get_board(args.board)
    if not board:
        return _err(f"no board named {args.board!r}")
    _print_json(board.get("columns", []))
    return 0


def cmd_show(args, client: Client) -> int:
    item = client.get_item(args.id)
    if not item:
        return _err(f"no item with id {args.id}")
    yaml.safe_dump(item, sys.stdout, sort_keys=False, allow_unicode=True)
    return 0


def cmd_create(args, client: Client) -> int:
    board = args.board
    if not board:
        boards = client.list_boards()
        if not boards:
            return _err("no boards exist; create one first")
        if len(boards) > 1:
            names = ", ".join(b.get("name", "") for b in boards)
            return _err(f"multiple boards exist; pass --board (one of: {names})")
        board = boards[0].get("name")
    board_data = client.get_board(board)
    if not board_data:
        return _err(f"no board named {board!r}")
    columns = board_data.get("columns", [])
    if not columns:
        return _err(f"board {board!r} has no columns")
    column = args.column
    if column is None:
        column = "Todo" if "Todo" in columns else columns[0]
    elif column not in columns:
        return _err(f"no column {column!r} in board {board!r}; columns are {columns}")
    item = client.create_item(board=board, column=column,
                              title=args.title, description=args.description or "")
    if not item or "error" in item:
        return _err(f"create failed: {item.get('error') if item else 'no response'}")
    print(f"create '{_field(item, 'title')}' (id {item.get('id')})")
    return 0


def cmd_move(args, client: Client) -> int:
    item = client.get_item(args.id)
    if not item:
        return _err(f"no item with id {args.id}")
    board = _field(item, "board")
    board_data = client.get_board(board)
    columns = board_data.get("columns", []) if board_data else []
    if args.column not in columns:
        return _err(f"no column {args.column!r} in board {board!r}; columns are {columns}")

    def do_move() -> int:
        res = client.update_item(args.id, column=args.column)
        if not res or "error" in res:
            return _err(f"move failed: {res.get('error') if res else 'no response'}")
        print(f"move '{_field(res, 'title')}' to {args.column}")
        return 0

    return _with_lock(client, args.id, do_move)


def cmd_edit(args, client: Client) -> int:
    fields: dict = {}
    if args.title is not None:
        fields["title"] = args.title
    if args.description is not None:
        fields["description"] = args.description
    if not fields:
        return _err("nothing to edit: pass --title and/or --description")
    item = client.get_item(args.id)
    if not item:
        return _err(f"no item with id {args.id}")

    def do_edit() -> int:
        res = client.update_item(args.id, **fields)
        if not res or "error" in res:
            return _err(f"edit failed: {res.get('error') if res else 'no response'}")
        print(f"edit '{_field(res, 'title')}'")
        return 0

    return _with_lock(client, args.id, do_edit)


# --- entrypoint ----------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tododo.agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="print items (optionally filtered) as JSON")
    p_list.add_argument("--board")
    p_list.add_argument("--column")
    p_list.add_argument("--search", help="only items whose title contains this")
    p_list.set_defaults(func=cmd_list)

    p_boards = sub.add_parser("boards", help="list boards as JSON")
    p_boards.set_defaults(func=cmd_boards)

    p_columns = sub.add_parser("columns", help="list a board's columns as JSON")
    p_columns.add_argument("--board", required=True)
    p_columns.set_defaults(func=cmd_columns)

    p_show = sub.add_parser("show", help="print one item's full detail as YAML")
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_show)

    p_create = sub.add_parser("create", help="create a new item")
    p_create.add_argument("title")
    p_create.add_argument("--board")
    p_create.add_argument("--column")
    p_create.add_argument("--description")
    p_create.set_defaults(func=cmd_create)

    p_move = sub.add_parser("move", help="move an item to a column")
    p_move.add_argument("id")
    p_move.add_argument("column")
    p_move.set_defaults(func=cmd_move)

    p_edit = sub.add_parser("edit", help="edit an item's title/description")
    p_edit.add_argument("id")
    p_edit.add_argument("--title")
    p_edit.add_argument("--description")
    p_edit.set_defaults(func=cmd_edit)

    return parser


def main(argv=None, client: Client | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if client is None:
        settings = Settings.load()
        client = Client(port=settings.server_port())
        ensure_server(client)  # leave it running for the GUI / next invocation
        if not client.ping():
            return _err("could not reach or start the tododo server")
    return args.func(args, client)


if __name__ == "__main__":
    raise SystemExit(main())
