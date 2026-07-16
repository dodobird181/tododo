"""
MCP server (stdio host).

A second front door beside the HTTP API. Every tool forwards to the already
running app over HTTP, so the app's single `Backend` stays the only writer to
the event log and an MCP tool call produces an event indistinguishable from the
HTTP one. `_send` is the sole network seam (tests inject `dispatch` through it);
the `@server.tool` functions are the schema-bearing catalog the host advertises.
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("TODODO_URL", "http://127.0.0.1:8760")
DEFAULT_BY = os.environ.get("TODODO_MCP_BY", "mcp")

server = FastMCP("tododo")


def _send(method: str, path: str, query: dict, body: dict) -> dict:
    """
    Perform one API call against the running app and return the JSON payload.
    The single network seam: tests replace this to route through `dispatch`.
    """
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    data = json.dumps(body).encode("utf-8") if method == "POST" else None
    request = Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return json.loads(error.read().decode("utf-8"))


def _write(path: str, body: dict) -> dict:
    return _send("POST", path, {}, {**body, "by": DEFAULT_BY})


def _read(path: str, query: dict | None = None) -> dict:
    return _send("GET", path, query or {}, {})


@server.tool()
def create_board(name: str, columns: list[str]) -> dict:
    """Create a board with the given column names."""
    return _write("/board", {"name": name, "columns": columns})


@server.tool()
def rename_board(target: str, name: str) -> dict:
    """Rename the board identified by `target` to `name`."""
    return _write("/board/rename", {"target": target, "name": name})


@server.tool()
def delete_board(target: str) -> dict:
    """Delete the board identified by `target`."""
    return _write("/board/delete", {"target": target})


@server.tool()
def create_column(board: str, name: str) -> dict:
    """Append a column named `name` to `board`."""
    return _write("/column/create", {"board": board, "name": name})


@server.tool()
def rename_column(board: str, col: str, name: str) -> dict:
    """Rename column `col` on `board` to `name`."""
    return _write("/column/rename", {"board": board, "col": col, "name": name})


@server.tool()
def swap_column(board: str, col: str, other: str) -> dict:
    """Swap the positions of columns `col` and `other` on `board`."""
    return _write("/column/swap", {"board": board, "col": col, "with": other})


@server.tool()
def delete_column(board: str, col: str) -> dict:
    """Delete column `col` from `board`."""
    return _write("/column/delete", {"board": board, "col": col})


@server.tool()
def create_item(board: str, column: str, title: str) -> dict:
    """Create an item titled `title` in `column` on `board`."""
    return _write("/item", {"board": board, "column": column, "title": title})


@server.tool()
def edit_item(target: str, field: str, value: str) -> dict:
    """Set `field` to `value` on the item identified by `target`."""
    return _write("/item/edit", {"target": target, "field": field, "value": value})


@server.tool()
def delete_item(target: str) -> dict:
    """Delete the item identified by `target`."""
    return _write("/item/delete", {"target": target})


@server.tool()
def resolve_conflict(target: str, field: str, parents: list[str], value: str) -> dict:
    """Resolve a conflict on `field` of `target` by choosing `value` over `parents`."""
    return _write("/resolve", {"target": target, "field": field, "parents": parents, "value": value})


@server.tool()
def list_boards() -> dict:
    """List all boards."""
    return _read("/boards")


@server.tool()
def view_board(board: str) -> dict:
    """View one board by id."""
    return _read("/board", {"id": board})


@server.tool()
def list_items() -> dict:
    """List all items across every board."""
    return _read("/items")


@server.tool()
def view_item(item: str) -> dict:
    """View one item by id."""
    return _read("/item", {"id": item})


@server.tool()
def list_conflicts(board: str | None = None) -> dict:
    """List unresolved conflicts, optionally scoped to one board."""
    return _read("/conflicts", {"board": board} if board else {})


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
