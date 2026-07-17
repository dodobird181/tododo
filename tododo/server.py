"""
HTTP/JSON API (API Process).

Writes enqueue a command and return `{"uuid": ...}` with 200 immediately; the
caller polls `GET /job?uuid=` for the applied event. Reads fold the in-memory
projection synchronously (no replay, no queue).

`dispatch` is the pure routing core — `(method, path, query, body) -> (status,
payload)` — so it is unit-testable without a socket. `serve` wraps it in a
stdlib `HTTPServer`.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse

from tododo.app import Backend

WEB_ROOT = Path(__file__).parent / "web"


def dispatch(backend: Backend, method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    """
    Route one request. Writes return `{"uuid"}`; reads return projected state.
    """
    route = (method, path)

    if route == ("GET", "/job"):
        uuid = query.get("uuid")
        if not uuid:
            return 400, {"error": "uuid required"}
        job = backend.poll(uuid)
        if job is None:
            return 404, {"error": "unknown uuid"}
        return 200, job.model_dump(mode="json")

    if route == ("GET", "/boards"):
        return 200, {"boards": [board.model_dump(mode="json") for board in backend.boards()]}

    if route == ("GET", "/board"):
        board_id = query.get("id")
        if not board_id:
            return 400, {"error": "id required"}
        return 200, backend.board(board_id).model_dump(mode="json")

    if route == ("GET", "/items"):
        return 200, {"items": [item.model_dump(mode="json") for item in backend.items()]}

    if route == ("GET", "/item"):
        item_id = query.get("id")
        if not item_id:
            return 400, {"error": "id required"}
        return 200, backend.item(item_id).model_dump(mode="json")

    if route == ("GET", "/conflicts"):
        conflicts = backend.conflicts(query.get("board"))
        return 200, {"conflicts": [conflict.model_dump(mode="json") for conflict in conflicts]}

    if route == ("GET", "/keybindings"):
        return 200, backend.keybindings()

    if route == ("POST", "/keybindings"):
        return 200, backend.set_keybindings(body)

    if route == ("GET", "/keybinding-contexts"):
        return 200, backend.keybinding_contexts()

    if route == ("GET", "/workspace"):
        return 200, backend.workspace()

    if route == ("POST", "/workspace"):
        return 200, backend.set_workspace(body)

    if route == ("GET", "/settings"):
        return 200, backend.settings()

    if route == ("POST", "/settings"):
        return 200, backend.set_settings(body)

    if route == ("GET", "/themes"):
        return 200, {"themes": backend.themes()}

    if method == "POST":
        return _dispatch_write(backend, path, body)

    return 404, {"error": "not found"}


def _dispatch_write(backend: Backend, path: str, body: dict) -> tuple[int, dict]:
    by = body.get("by", "")
    try:
        if path == "/board":
            uuid = backend.create_board(body["name"], body.get("columns", []), by=by)
        elif path == "/board/rename":
            uuid = backend.rename_board(body["target"], body["name"], by=by)
        elif path == "/board/delete":
            uuid = backend.delete_board(body["target"], by=by)
        elif path == "/column/create":
            uuid = backend.create_column(body["board"], body["name"], by=by)
        elif path == "/column/rename":
            uuid = backend.rename_column(body["board"], body["col"], body["name"], by=by)
        elif path == "/column/swap":
            uuid = backend.swap_column(body["board"], body["col"], body["with"], by=by)
        elif path == "/column/delete":
            uuid = backend.delete_column(body["board"], body["col"], by=by)
        elif path == "/item":
            uuid = backend.create_item(
                body["board"], body["column"], body["title"], by=by,
                start=body.get("start", ""), end=body.get("end", ""),
            )
        elif path == "/item/edit":
            uuid = backend.edit_item(body["target"], body["field"], body["value"], by=by)
        elif path == "/item/delete":
            uuid = backend.delete_item(body["target"], by=by)
        elif path == "/resolve":
            uuid = backend.resolve_conflict(
                body["target"], body["field"], body["parents"], body["value"], by=by,
            )
        else:
            return 404, {"error": "not found"}
    except KeyError as missing:
        return 400, {"error": f"missing field {missing}"}
    return 200, {"uuid": uuid}


def make_handler(backend: Backend):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status: int, payload: dict) -> None:
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def _serve_static(self, path: str) -> bool:
            relative = "index.html" if path == "/" else path.lstrip("/")
            target = (WEB_ROOT / relative).resolve()
            if WEB_ROOT.resolve() not in target.parents or not target.is_file():
                return False
            content_type = "text/html" if target.suffix == ".html" else "application/octet-stream"
            blob = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return True

        def _serve_theme(self, name: str) -> None:
            css = backend.theme_css(name)
            if css is None:
                self._respond(404, {"error": "unknown theme"})
                return
            blob = css.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/css")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def _handle(self, method: str) -> None:
            parsed = urlparse(self.path)
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            if method == "GET" and parsed.path == "/theme":
                self._serve_theme(query.get("name", ""))
                return
            api_prefixes = ("/job", "/board", "/item", "/items", "/boards", "/conflicts",
                            "/column", "/resolve", "/keybindings", "/keybinding-contexts",
                            "/workspace", "/settings", "/themes")
            if method == "GET" and (parsed.path == "/" or not parsed.path.startswith(api_prefixes)):
                if self._serve_static(parsed.path):
                    return
            body = {}
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except json.JSONDecodeError:
                    self._respond(400, {"error": "invalid json"})
                    return
            status, payload = dispatch(backend, method, parsed.path, query, body)
            self._respond(status, payload)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def log_message(self, *args):
            pass

    return Handler


def serve(backend: Backend, host: str = "127.0.0.1", port: int = 8760) -> ThreadingHTTPServer:
    """
    Build (but do not block on) a threaded HTTP server bound to `host:port`.
    Call `.serve_forever()` on the result to run it.
    """
    return ThreadingHTTPServer((host, port), make_handler(backend))
