"""
Installer for the tododo MCP server.

Registers tododo as a stdio MCP server in one or more target directories by
merging into each `<dir>/.mcp.json` and auto-allowing the read-only tools in
`<dir>/.claude/settings.json`.

Two launch modes. `console` (the default) emits the bare `tododo-mcp` command,
resolved from PATH — portable across machines and OSes once the package is
installed (`pipx install <repo>` or `pip install .`). `linked` instead pins this
interpreter and puts the repo on `PYTHONPATH`, so the server launches straight
from a checkout without installing anything.

    python -m tododo.install [DIR ...] [--mode console|linked] [--url URL] [--by NAME]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SERVER_NAME = "tododo"
CONSOLE_COMMAND = "tododo"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "http://127.0.0.1:8760"
REQUIREMENTS = ["mcp>=1.28,<2.0"]
READ_TOOLS = ["list_boards", "view_board", "list_items", "view_item", "list_conflicts"]


def server_config(url: str, by: str, mode: str) -> dict:
    """
    Build the `.mcp.json` entry that launches this server. `console` uses the
    installed `tododo-mcp` command from PATH; `linked` pins this checkout.
    """
    env = {"TODODO_URL": url}
    if by:
        env["TODODO_MCP_BY"] = by
    if mode == "console":
        return {"type": "stdio", "command": CONSOLE_COMMAND, "args": [], "env": env}
    env["PYTHONPATH"] = str(REPO_ROOT)
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "tododo.mcp"],
        "env": env,
    }


def _merge_json_file(path: Path, mutate) -> None:
    """
    Load `path` (or start empty), apply `mutate`, and write it back indented.
    A `.bak` copy of any existing file is kept before overwriting.
    """
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        path.with_suffix(path.suffix + ".bak").write_text(
            json.dumps(existing, indent=2) + "\n", encoding="utf-8",
        )
    mutate(existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


def _register_server(config: dict, url: str, by: str, mode: str) -> None:
    servers = config.setdefault("mcpServers", {})
    servers[SERVER_NAME] = server_config(url, by, mode)


def _allow_read_tools(settings: dict) -> None:
    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])
    for tool in READ_TOOLS:
        entry = f"mcp__{SERVER_NAME}__{tool}"
        if entry not in allow:
            allow.append(entry)


def install_directory(directory: Path, url: str, by: str, mode: str) -> None:
    """
    Register the server and auto-allow its read tools inside one directory.
    """
    _merge_json_file(directory / ".mcp.json", lambda config: _register_server(config, url, by, mode))
    _merge_json_file(directory / ".claude" / "settings.json", _allow_read_tools)
    print(f"registered {SERVER_NAME} ({mode}) in {directory}")


def install_dependencies() -> None:
    """
    Install the runtime requirements into the current interpreter.
    """
    subprocess.run([sys.executable, "-m", "pip", "install", *REQUIREMENTS], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="tododo.install")
    parser.add_argument("directories", nargs="*", default=["."], help="target directories (default: cwd)")
    parser.add_argument("--mode", choices=["console", "linked"], default="console",
                        help="console: PATH command (default); linked: pin this checkout")
    parser.add_argument("--deps", action="store_true", help="install runtime requirements first")
    parser.add_argument("--url", default=DEFAULT_URL, help="URL of the running tododo app")
    parser.add_argument("--by", default="", help="author tag written on MCP events")
    args = parser.parse_args()

    if args.deps:
        install_dependencies()

    if args.mode == "console" and shutil.which(CONSOLE_COMMAND) is None:
        print(f"warning: '{CONSOLE_COMMAND}' is not on PATH yet — install it with "
              f"'pipx install {REPO_ROOT}' (or 'pip install {REPO_ROOT}')")

    for directory in args.directories:
        install_directory(Path(directory).resolve(), args.url, args.by, args.mode)

    print("done — restart your agent to load the tododo MCP server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
