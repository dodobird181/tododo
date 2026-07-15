"""
MCP adapter.

A thin layer exposing the same operations as the HTTP API as MCP tools. Every
write tool enqueues a command through the identical `Backend` methods the HTTP
path uses, so an MCP tool call produces an event indistinguishable from the HTTP
one. `call_tool` is the pure dispatcher (host-agnostic, unit-testable); `TOOLS`
is the schema list a real MCP server would advertise.
"""

from __future__ import annotations

from tododo.app import Backend

TOOLS = [
    {"name": "create_board", "args": ["name", "columns"]},
    {"name": "rename_board", "args": ["target", "name"]},
    {"name": "delete_board", "args": ["target"]},
    {"name": "create_column", "args": ["board", "name"]},
    {"name": "rename_column", "args": ["board", "col", "name"]},
    {"name": "swap_column", "args": ["board", "col", "with"]},
    {"name": "delete_column", "args": ["board", "col"]},
    {"name": "create_item", "args": ["board", "column", "title"]},
    {"name": "edit_item", "args": ["target", "field", "value"]},
    {"name": "delete_item", "args": ["target"]},
    {"name": "resolve_conflict", "args": ["target", "field", "parents", "value"]},
    {"name": "list_boards", "args": []},
    {"name": "view_board", "args": ["board"]},
    {"name": "list_items", "args": []},
    {"name": "view_item", "args": ["item"]},
    {"name": "list_conflicts", "args": ["board"]},
]


def call_tool(backend: Backend, name: str, arguments: dict) -> dict:
    """
    Invoke one MCP tool against the backend. Writes return `{"uuid"}`; reads
    return projected state.
    """
    by = arguments.get("by", "")

    if name == "create_board":
        return {"uuid": backend.create_board(arguments["name"], arguments.get("columns", []), by=by)}
    if name == "rename_board":
        return {"uuid": backend.rename_board(arguments["target"], arguments["name"], by=by)}
    if name == "delete_board":
        return {"uuid": backend.delete_board(arguments["target"], by=by)}
    if name == "create_column":
        return {"uuid": backend.create_column(arguments["board"], arguments["name"], by=by)}
    if name == "rename_column":
        return {"uuid": backend.rename_column(arguments["board"], arguments["col"], arguments["name"], by=by)}
    if name == "swap_column":
        return {"uuid": backend.swap_column(arguments["board"], arguments["col"], arguments["with"], by=by)}
    if name == "delete_column":
        return {"uuid": backend.delete_column(arguments["board"], arguments["col"], by=by)}
    if name == "create_item":
        return {"uuid": backend.create_item(
            arguments["board"], arguments["column"], arguments["title"], by=by,
        )}
    if name == "edit_item":
        return {"uuid": backend.edit_item(arguments["target"], arguments["field"], arguments["value"], by=by)}
    if name == "delete_item":
        return {"uuid": backend.delete_item(arguments["target"], by=by)}
    if name == "resolve_conflict":
        return {"uuid": backend.resolve_conflict(
            arguments["target"], arguments["field"], arguments["parents"], arguments["value"], by=by,
        )}

    if name == "list_boards":
        return {"boards": [board.model_dump(mode="json") for board in backend.boards()]}
    if name == "view_board":
        return backend.board(arguments["board"]).model_dump(mode="json")
    if name == "list_items":
        return {"items": [item.model_dump(mode="json") for item in backend.items()]}
    if name == "view_item":
        return backend.item(arguments["item"]).model_dump(mode="json")
    if name == "list_conflicts":
        conflicts = backend.conflicts(arguments.get("board"))
        return {"conflicts": [conflict.model_dump(mode="json") for conflict in conflicts]}

    raise ValueError(f"unknown tool {name}")
