"""Command-line board access for agents / automation.

Lets a non-interactive caller (an LLM agent, a script, a cron loop) inspect and
advance board items without hand-editing YAML — ``Board.load`` already
dedupes/normalizes, so the file stays sane. Every mutation is attributed to an
``--actor`` name, which is:

  * stored on the item (``actor``) so the in-app blame line reads, e.g.,
    "Marked 'Done' 3 minutes ago by Claude", and
  * used as the git *author* of the optional ``--commit`` so the same
    attribution is visible in the history (``git log``).

Usage::

    python -m tododo.agent list [--column Todo] [--board board.yaml]
    python -m tododo.agent move <id> <column> [--actor NAME] [--commit]
    python -m tododo.agent edit <id> [--title T] [--description D] [--actor NAME] [--commit]
    python -m tododo.agent create <title> [--column C] [--actor NAME] [--commit]

The actor defaults to the ``TODODO_ACTOR`` environment variable, then "agent".
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .board import Board

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BOARD = ROOT / "board.yaml"


def _actor(arg: str | None) -> str:
    return arg or os.environ.get("TODODO_ACTOR") or "agent"


def _commit(board: Board, message: str, actor: str) -> None:
    """Commit the board file with the agent as the git author (best-effort)."""
    rel = board.path.name
    email = os.environ.get("TODODO_ACTOR_EMAIL", "agent@tododo.local")
    env = {**os.environ, "GIT_AUTHOR_NAME": actor, "GIT_AUTHOR_EMAIL": email}
    subprocess.run(["git", "add", rel], cwd=ROOT, env=env)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet", "--", rel], cwd=ROOT, env=env)
    if staged.returncode == 0:
        return  # nothing to commit
    subprocess.run(["git", "commit", "-m", message], cwd=ROOT, env=env,
                   capture_output=True, text=True)


def cmd_list(args) -> int:
    board = Board.load(args.board)
    items = [
        {"id": it.id, "title": it.title, "column": it.column,
         "points": it.points, "description": it.description}
        for it in board.items
        if args.column is None or it.column == args.column
    ]
    json.dump(items, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_move(args) -> int:
    board = Board.load(args.board)
    item = board.find(args.id)
    if not item:
        print(f"no item with id {args.id}", file=sys.stderr)
        return 1
    actor = _actor(args.actor)
    board.move_to_column(args.id, args.column, actor=actor)
    board.save()
    msg = f"move '{item.title}' to {item.column}"
    if args.commit:
        _commit(board, msg, actor)
    print(msg)
    return 0


def cmd_edit(args) -> int:
    board = Board.load(args.board)
    item = board.find(args.id)
    if not item:
        print(f"no item with id {args.id}", file=sys.stderr)
        return 1
    if args.title is not None:
        item.title = args.title
    if args.description is not None:
        item.description = args.description
    actor = _actor(args.actor)
    item.mark_edited(actor)
    board.save()
    msg = f"edit '{item.title}'"
    if args.commit:
        _commit(board, msg, actor)
    print(msg)
    return 0


def cmd_create(args) -> int:
    board = Board.load(args.board)
    actor = _actor(args.actor)
    item = board.create(args.title, column=args.column, actor=actor)
    board.save()
    msg = f"create '{item.title}'"
    if args.commit:
        _commit(board, msg, actor)
    print(f"{msg} (id {item.id})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tododo.agent")
    parser.add_argument("--board", type=Path, default=DEFAULT_BOARD)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="print items (optionally one column) as JSON")
    p_list.add_argument("--column", default="Todo")
    p_list.set_defaults(func=cmd_list)

    p_move = sub.add_parser("move", help="move an item to a column")
    p_move.add_argument("id")
    p_move.add_argument("column")
    p_move.add_argument("--actor")
    p_move.add_argument("--commit", action="store_true")
    p_move.set_defaults(func=cmd_move)

    p_edit = sub.add_parser("edit", help="edit an item's title/description")
    p_edit.add_argument("id")
    p_edit.add_argument("--title")
    p_edit.add_argument("--description")
    p_edit.add_argument("--actor")
    p_edit.add_argument("--commit", action="store_true")
    p_edit.set_defaults(func=cmd_edit)

    p_create = sub.add_parser("create", help="create a new item")
    p_create.add_argument("title")
    p_create.add_argument("--column")
    p_create.add_argument("--actor")
    p_create.add_argument("--commit", action="store_true")
    p_create.set_defaults(func=cmd_create)

    args = parser.parse_args(argv)
    # "list" defaults --column to Todo; allow --column '' to mean all.
    if getattr(args, "column", None) == "":
        args.column = None
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
