"""Install git hooks used by tododo.

The background poller in :mod:`tododo.gitsync` handles "fetch every couple of
minutes" while the app runs. This module installs a ``pre-push`` hook so that
*any* push (from the app or the command line) always fetches first, keeping the
local repo aware of remote state before publishing.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

PRE_PUSH = """#!/bin/sh
# tododo: always fetch the latest remote state before pushing.
git fetch --quiet || true
exit 0
"""


def install_hooks(repo_root: Path) -> None:
    hooks_dir = Path(repo_root) / ".git" / "hooks"
    if not hooks_dir.parent.exists():
        return  # not a git repo
    hooks_dir.mkdir(parents=True, exist_ok=True)
    _write_hook(hooks_dir / "pre-push", PRE_PUSH)


def _write_hook(path: Path, content: str) -> None:
    if path.exists() and path.read_text() == content:
        return
    path.write_text(content)
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
