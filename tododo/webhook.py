"""Webhook receiver: fetch the instant a new commit is pushed.

A "fetch on every new commit" cannot be done with a local git hook — a commit
landing on the *remote* is not a local event. The standard mechanism is a
**webhook**: the host (e.g. GitHub) POSTs to a URL when someone pushes. This
module runs a tiny HTTP server that, on any POST, triggers an immediate
fetch+merge (via the supplied callback).

Because the sender must reach this server, expose the port with a tunnel
(``cloudflared``/``ngrok``) or run on a public host, then point a GitHub webhook
(Settings -> Webhooks, content type ``application/json``, event: *push*) at it.
Set a shared secret to require GitHub's ``X-Hub-Signature-256``.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class WebhookServer:
    def __init__(self, port: int, secret: str, on_event, host: str = "0.0.0.0"):
        self.port = port
        self.secret = secret or ""
        self.on_event = on_event
        self.host = host
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        except OSError:
            return False  # port in use / not bindable
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="webhook", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    def _valid_signature(self, body: bytes, header: str | None) -> bool:
        if not self.secret:
            return True  # no secret configured -> accept all
        if not header or not header.startswith("sha256="):
            return False
        digest = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest("sha256=" + digest, header)

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence default stderr logging
                pass

            def do_GET(self):
                # Simple liveness probe.
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"tododo webhook ok")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else b""
                sig = self.headers.get("X-Hub-Signature-256")
                if not server._valid_signature(body, sig):
                    self.send_response(401)
                    self.end_headers()
                    return
                try:
                    server.on_event()
                except Exception:
                    pass
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"fetching")

        return Handler
