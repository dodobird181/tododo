"""HTTP client the pygame GUI uses to talk to the local data server.

The GUI never touches the filesystem, git, or encryption directly — every read
and mutation goes through here. A thin ``requests``-free wrapper over
``http.client``; all calls are best-effort and never raise (network/refused
errors return ``None`` / empty so the UI degrades instead of crashing).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from http.client import HTTPConnection
from urllib.parse import urlencode


class Client:
    def __init__(self, host: str = "127.0.0.1", port: int = 8770, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.last_error: str | None = None

    # --- transport -------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None,
                 query: dict | None = None) -> dict | list | None:
        if query:
            path = f"{path}?{urlencode({k: v for k, v in query.items() if v is not None})}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        conn = HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            headers = {"Content-Type": "application/json"} if payload else {}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            self.last_error = None if resp.status < 400 else f"{resp.status}"
            return json.loads(data) if data else None
        except Exception as exc:
            self.last_error = str(exc)
            return None
        finally:
            conn.close()

    def ping(self) -> bool:
        return self.user() is not None

    # --- user ------------------------------------------------------------

    def user(self) -> dict | None:
        return self._request("GET", "/user")

    # --- items -----------------------------------------------------------

    def get_item(self, item_id: str) -> dict | None:
        r = self._request("GET", "/item", query={"id": item_id})
        return r if isinstance(r, dict) and "error" not in r else None

    def list_items(self, filters: dict | None = None) -> list[dict]:
        r = self._request("POST", "/item/list", body=filters or {})
        return r.get("items", []) if isinstance(r, dict) else []

    def create_item(self, **fields) -> dict | None:
        return self._request("POST", "/item", body=fields)

    def update_item(self, item_id: str, **fields) -> dict | None:
        return self._request("PUT", "/item", body={"id": item_id, **fields})

    def delete_item(self, item_id: str) -> bool:
        r = self._request("DELETE", "/item", query={"id": item_id})
        return bool(r and r.get("deleted"))

    def lock_item(self, item_id: str) -> tuple[bool, str | None]:
        r = self._request("POST", "/item/lock", body={"id": item_id})
        if not isinstance(r, dict):
            return False, None
        return bool(r.get("locked")), r.get("holder")

    def unlock_item(self, item_id: str) -> bool:
        r = self._request("DELETE", "/item/lock", query={"id": item_id})
        return bool(r and r.get("released"))

    def item_history(self, item_id: str) -> list[dict]:
        r = self._request("GET", "/item/history", query={"id": item_id})
        return r.get("events", []) if isinstance(r, dict) else []

    # --- boards ----------------------------------------------------------

    def get_board(self, name: str) -> dict | None:
        r = self._request("GET", "/board", query={"name": name})
        return r if isinstance(r, dict) and "error" not in r else None

    def list_boards(self) -> list[dict]:
        r = self._request("GET", "/board/list")
        return r.get("boards", []) if isinstance(r, dict) else []

    def create_board(self, name: str, columns: list[str]) -> dict | None:
        return self._request("POST", "/board", body={"name": name, "columns": columns})

    def update_board(self, name: str, new_name: str | None = None,
                     columns: list[str] | None = None) -> dict | None:
        body = {"name": name}
        if new_name is not None:
            body["new_name"] = new_name
        if columns is not None:
            body["columns"] = columns
        return self._request("PUT", "/board", body=body)

    def delete_board(self, name: str) -> bool:
        r = self._request("DELETE", "/board", query={"name": name})
        return bool(r and r.get("deleted"))

    def lock_board(self, name: str) -> tuple[bool, str | None]:
        r = self._request("POST", "/board/lock", body={"name": name})
        if not isinstance(r, dict):
            return False, None
        return bool(r.get("locked")), r.get("holder")

    def unlock_board(self, name: str) -> bool:
        r = self._request("DELETE", "/board/lock", query={"name": name})
        return bool(r and r.get("released"))

    # --- keybindings / settings -----------------------------------------

    def get_keybindings(self) -> dict:
        r = self._request("GET", "/keybindings")
        return r.get("bindings", {}) if isinstance(r, dict) else {}

    def update_keybinding(self, action: str, value: str) -> dict:
        r = self._request("PUT", "/keybindings", body={"action": action, "value": value})
        return r.get("bindings", {}) if isinstance(r, dict) else {}

    def get_settings(self) -> dict:
        r = self._request("GET", "/settings")
        return r.get("settings", {}) if isinstance(r, dict) else {}

    def update_setting(self, key: str, value) -> dict:
        r = self._request("PUT", "/settings", body={"key": key, "value": value})
        return r.get("settings", {}) if isinstance(r, dict) else {}


def ensure_server(client: Client) -> subprocess.Popen | None:
    """Start ``tododo.server`` as a child if none is reachable.

    Returns the child process if we had to start one (so a caller that owns the
    process can ``terminate()`` it), else ``None``. Best-effort: waits ~5s for
    the server to answer.
    """
    if client.ping():
        return None
    proc = subprocess.Popen([sys.executable, "-m", "tododo.server"])
    for _ in range(50):  # wait up to ~5s for it to come up
        if client.ping():
            return proc
        time.sleep(0.1)
    return proc
