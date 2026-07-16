"""
Parity: an MCP tool call produces an event indistinguishable from the HTTP path
(same op/field/payload/parent-shape). The MCP host forwards over HTTP, so both
paths land in `dispatch` against one `Backend`; the tests wire the host's `_send`
seam straight to `dispatch` to exercise that mapping without a socket.
"""

from __future__ import annotations

import time

from tododo import mcp
from tododo.app import Backend
from tododo.server import dispatch


def _route_through(backend: Backend):
    def _send(method, path, query, body):
        _, payload = dispatch(backend, method, path, query, body)
        return payload
    return _send


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


def test_create_item_parity(tmp_path, monkeypatch):
    http_backend = Backend(tmp_path / "http", "pw", enable_git=False)
    mcp_backend = Backend(tmp_path / "mcp", "pw", enable_git=False)
    http_backend.start()
    mcp_backend.start()
    try:
        args = {"board": "b", "column": "todo", "title": "Buy milk"}
        _, http_result = dispatch(http_backend, "POST", "/item", {}, args)

        monkeypatch.setattr(mcp, "_send", _route_through(mcp_backend))
        mcp_result = mcp.create_item(args["board"], args["column"], args["title"])

        http_event = _wait(http_backend, http_result["uuid"]).event
        mcp_event = _wait(mcp_backend, mcp_result["uuid"]).event
        assert _fingerprint(http_event) == _fingerprint(mcp_event)
    finally:
        http_backend.stop()
        mcp_backend.stop()


def test_mcp_reads(backend: Backend, monkeypatch):
    backend.start()
    try:
        monkeypatch.setattr(mcp, "_send", _route_through(backend))
        created = mcp.create_board("Work", ["a"])
        _wait(backend, created["uuid"])
        boards = mcp.list_boards()
        assert any(board["name"]["value"] == "Work" for board in boards["boards"])
    finally:
        backend.stop()
