"""
Parity: an MCP tool call produces an event indistinguishable from the HTTP path
(same op/field/payload/parent-shape), since both go through the same Backend
operations.
"""

from __future__ import annotations

import time

from tododo.app import Backend
from tododo.mcp import call_tool
from tododo.server import dispatch


def _wait(backend: Backend, uuid: str, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = backend.poll(uuid)
        if job and job.status != "pending":
            return job
        time.sleep(0.01)
    raise AssertionError("job never completed")


def _fingerprint(event):
    return (event.op, event.field, event.payload, len(event.parent))


def test_create_item_parity(tmp_path):
    http_backend = Backend(tmp_path / "http", "pw", enable_git=False)
    mcp_backend = Backend(tmp_path / "mcp", "pw", enable_git=False)
    http_backend.start()
    mcp_backend.start()
    try:
        args = {"board": "b", "column": "todo", "title": "Buy milk"}
        _, http_result = dispatch(http_backend, "POST", "/item", {}, args)
        mcp_result = call_tool(mcp_backend, "create_item", args)

        http_event = _wait(http_backend, http_result["uuid"]).event
        mcp_event = _wait(mcp_backend, mcp_result["uuid"]).event
        assert _fingerprint(http_event) == _fingerprint(mcp_event)
    finally:
        http_backend.stop()
        mcp_backend.stop()


def test_mcp_reads(backend: Backend):
    backend.start()
    try:
        created = call_tool(backend, "create_board", {"name": "Work", "columns": ["a"]})
        _wait(backend, created["uuid"])
        boards = call_tool(backend, "list_boards", {})
        assert any(board["name"]["value"] == "Work" for board in boards["boards"])
    finally:
        backend.stop()
