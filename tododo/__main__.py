"""
Dev launcher: start the backend + HTTP server and serve the test UI.

    python -m tododo [--root DIR] [--port N] [--no-git]

Passphrase comes from `TODODO_PASSPHRASE` (default a dev value). GitHub login,
if `gh` is installed, is used as the event author.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from tododo.app import Backend
from tododo.server import serve


def _github_login() -> str:
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(prog="tododo")
    parser.add_argument("--root", default=".", help="repo root holding events/ and events-encrypted/")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8760)
    parser.add_argument("--no-git", action="store_true", help="disable git sync")
    args = parser.parse_args()

    passphrase = os.environ.get("TODODO_PASSPHRASE", "dev-passphrase")
    backend = Backend(
        Path(args.root),
        passphrase=passphrase,
        default_by=_github_login() or "local",
        enable_git=not args.no_git,
    )
    backend.start()
    server = serve(backend, args.host, args.port)
    print(f"tododo running -> http://{args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        backend.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
