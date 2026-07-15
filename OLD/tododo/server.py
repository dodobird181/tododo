"""Local HTTP data server — the only process that touches the filesystem.

Owns the :class:`Store` (items/boards on disk, encryption), the :class:`GitSync`
loop, git hooks, keybindings/settings files, and the server-side workspace file.
The pygame GUI (``tododo.ui``) is a client that drives everything through the
JSON endpoints below. Run with ``python -m tododo.server``.

Endpoints (all JSON; see ``apispec.yaml``):

    GET    /user
    GET    /item?id=            POST /item            PUT /item            DELETE /item?id=
    POST   /item/list           (filter body)
    POST   /item/lock           DELETE /item/lock     GET /item/history?id=
    GET    /board?name=         POST /board           PUT /board           DELETE /board?name=
    GET    /board/list
    POST   /board/lock          DELETE /board/lock
    GET    /keybindings         PUT /keybindings      ({action, value})
    GET    /settings            PUT /settings         ({key, value})

The caller identity (for lock ownership + edit provenance) is the server's own
git github-username, so no per-request auth is needed on a single-user machine.
"""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import migrate
from .githooks import install_hooks
from .gitsync import GitSync
from .keybindings import Keybindings
from .settings import Settings
from .store import Store
from .workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"

# GET paths served by the JSON API; every other GET is a static web-client asset.
_API_GET_PATHS = {
    "/user", "/item", "/item/history", "/board", "/board/list",
    "/keybindings", "/settings",
}


class Server:
    """Holds the long-lived store/git/settings; the HTTP handler delegates here."""

    def __init__(self):
        self.settings = Settings.load()
        install_hooks(ROOT)
        self.git = GitSync(
            ROOT,
            merge_option=self.settings.merge_option(),
            push_interval=self.settings.push_interval(),
            poll_interval=self.settings.poll_interval(),
            poll_backoff_max=self.settings.poll_backoff_max(),
        )
        self.store = Store(self.settings, self.git)
        name, email = self.git.identity()
        self.actor = self.git.github_username() or name or "someone"
        self.user = {"user": name, "email": email, "github": self.git.github_username()}
        # Server-generated, per-machine view state (no client endpoint).
        Workspace.load().save()
        # Migrate legacy monolithic boards on first run, then start syncing.
        migrate.run(self.store, self.actor)
        self.git.start()

    # --- request routing -------------------------------------------------

    def handle(self, method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
        routes = {
            ("GET", "/user"): self._user,
            ("GET", "/item"): self._item_get,
            ("POST", "/item"): self._item_post,
            ("PUT", "/item"): self._item_put,
            ("DELETE", "/item"): self._item_delete,
            ("POST", "/item/list"): self._item_list,
            ("POST", "/item/lock"): self._item_lock,
            ("DELETE", "/item/lock"): self._item_unlock,
            ("GET", "/item/history"): self._item_history,
            ("GET", "/board"): self._board_get,
            ("POST", "/board"): self._board_post,
            ("PUT", "/board"): self._board_put,
            ("DELETE", "/board"): self._board_delete,
            ("GET", "/board/list"): self._board_list,
            ("POST", "/board/lock"): self._board_lock,
            ("DELETE", "/board/lock"): self._board_unlock,
            ("GET", "/keybindings"): self._keybindings_get,
            ("PUT", "/keybindings"): self._keybindings_put,
            ("GET", "/settings"): self._settings_get,
            ("PUT", "/settings"): self._settings_put,
        }
        fn = routes.get((method, path))
        if not fn:
            return 404, {"error": f"no route {method} {path}"}
        return fn(query, body)

    def _arg(self, query: dict, body: dict, key: str):
        if key in body:
            return body[key]
        vals = query.get(key)
        return vals[0] if vals else None

    # --- user ------------------------------------------------------------

    def _user(self, query, body):
        return 200, self.user

    # --- items -----------------------------------------------------------

    def _item_get(self, query, body):
        item = self.store.get_item(self._arg(query, body, "id"))
        return (200, item.to_dict()) if item else (404, {"error": "no such item"})

    def _item_list(self, query, body):
        items = self.store.list_items(body or {})
        return 200, {"items": [it.to_dict() for it in items]}

    def _item_post(self, query, body):
        board = body.get("board")
        column = body.get("column")
        if not board or not column:
            return 400, {"error": "board and column are required"}
        vals = {k: body.get(k) for k in
                ("title", "description", "start", "end", "assigned_to", "report_to")}
        item = self.store.create_item(self.actor, board, column, **vals)
        return 200, item.to_dict()

    def _item_put(self, query, body):
        item_id = body.get("id")
        if not item_id:
            return 400, {"error": "id required"}
        if not self.store.holds_lock(self.actor, item_id):
            return 409, {"error": "you do not hold the lock on this item"}
        values = {k: v for k, v in body.items() if k != "id"}
        item = self.store.update_item(self.actor, item_id, values)
        return (200, item.to_dict()) if item else (404, {"error": "no such item"})

    def _item_delete(self, query, body):
        item_id = self._arg(query, body, "id")
        if not self.store.holds_lock(self.actor, item_id):
            return 409, {"error": "you do not hold the lock on this item"}
        ok = self.store.delete_item(item_id)
        return (200, {"deleted": True}) if ok else (404, {"error": "no such item"})

    def _item_lock(self, query, body):
        locked, holder = self.store.acquire_lock(self.actor, body.get("id"))
        return 200, {"locked": locked, "holder": holder}

    def _item_unlock(self, query, body):
        ok = self.store.release_lock(self.actor, self._arg(query, body, "id"))
        return 200, {"released": ok}

    def _item_history(self, query, body):
        return 200, {"events": self.store.item_history(self._arg(query, body, "id"))}

    # --- boards ----------------------------------------------------------

    def _board_get(self, query, body):
        board = self.store.get_board(self._arg(query, body, "name"))
        return (200, board.to_dict()) if board else (404, {"error": "no such board"})

    def _board_list(self, query, body):
        return 200, {"boards": [b.to_dict() for b in self.store.list_boards()]}

    def _board_post(self, query, body):
        name = body.get("name")
        if not name:
            return 400, {"error": "name required"}
        board = self.store.create_board(name, list(body.get("columns") or []))
        return (200, board.to_dict()) if board else (409, {"error": "board name exists"})

    def _board_put(self, query, body):
        name = body.get("name")
        if not self.store.acquire_board_lock(self.actor, name)[0]:
            return 409, {"error": "board is being edited by another user"}
        try:
            board = self.store.update_board(name, body.get("new_name"), body.get("columns"))
        finally:
            self.store.release_board_lock(self.actor, name)
        return (200, board.to_dict()) if board else (404, {"error": "no such board / name taken"})

    def _board_delete(self, query, body):
        ok = self.store.delete_board(self._arg(query, body, "name"))
        return (200, {"deleted": True}) if ok else (404, {"error": "no such board"})

    def _board_lock(self, query, body):
        ok, holder = self.store.acquire_board_lock(self.actor, body.get("name"))
        return 200, {"locked": ok, "holder": holder}

    def _board_unlock(self, query, body):
        ok = self.store.release_board_lock(self.actor, self._arg(query, body, "name"))
        return 200, {"released": ok}

    # --- keybindings -----------------------------------------------------

    def _keybindings_get(self, query, body):
        return 200, {"bindings": Keybindings.load().mapping}

    def _keybindings_put(self, query, body):
        action, value = body.get("action"), body.get("value")
        keys = Keybindings.load()
        if action not in keys.mapping:
            return 404, {"error": f"no such action {action}"}
        keys.mapping[action] = str(value)
        keys.save()
        return 200, {"bindings": keys.mapping}

    # --- settings --------------------------------------------------------

    def _settings_get(self, query, body):
        # Values keep their native JSON types (bool/int/float/str) so the GUI can
        # pick the right editor control per primitive.
        return 200, {"settings": self.settings.values}

    def _settings_put(self, query, body):
        key, value = body.get("key"), body.get("value")
        if key not in self.settings.values:
            return 404, {"error": f"no such setting {key}"}
        self.settings.set(key, value)
        # Push live-tunable values to the running sync loop.
        self.git.merge_option = self.settings.merge_option()
        self.git.push_interval = self.settings.push_interval()
        self.git.poll_interval = self.settings.poll_interval()
        self.git.poll_backoff_max = self.settings.poll_backoff_max()
        return 200, {"settings": self.settings.values}


def _make_handler(app: Server):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # silence default stderr logging
            pass

        def _dispatch(self, method: str):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            body = {}
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    self._send(400, {"error": "invalid JSON body"})
                    return
            try:
                status, payload = app.handle(method, parsed.path, query, body)
            except Exception as exc:  # never take the server down on one request
                status, payload = 500, {"error": str(exc)}
            self._send(status, payload)

        def _send(self, status: int, payload: dict):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_static(self, path: str):
            """Serve the HTML+JS web client from tododo/web (same origin as the API)."""
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (WEB_DIR / rel).resolve()
            # Contain within WEB_DIR; fall back to the SPA entry point.
            if WEB_DIR not in target.parents and target != WEB_DIR:
                target = WEB_DIR / "index.html"
            if not target.is_file():
                target = WEB_DIR / "index.html"
            try:
                data = target.read_bytes()
            except OSError:
                self._send(404, {"error": "not found"})
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in _API_GET_PATHS:
                self._dispatch("GET")
            else:
                self._serve_static(parsed.path)

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def main() -> None:
    app = Server()
    port = app.settings.server_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(app))
    print(f"tododo server on http://127.0.0.1:{port} (actor: {app.actor})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.git.stop()


if __name__ == "__main__":
    main()
