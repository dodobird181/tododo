"""
Key correctness gate: two clones of a bare remote each edit the same field
concurrently; after both push/pull the histories merge cleanly at the git level
(no merge error) and the projection surfaces exactly one `Conflict`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tododo.app import Backend
from tododo.gitsync import GitSync

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_clone(path: Path, remote: Path):
    _git(path.parent, "clone", str(remote), path.name)
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Tester")
    (path / "events-encrypted").mkdir(exist_ok=True)


def _make_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.name)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "Tester")
    (seed / "events-encrypted").mkdir()
    (seed / "events-encrypted" / ".gitkeep").write_text("")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "origin", "main")
    return remote


def _commit_push(gitsync: GitSync, message: str):
    with gitsync._git_lock:
        gitsync._commit(message)
        gitsync._push()


def _pull(gitsync: GitSync):
    with gitsync._git_lock:
        gitsync._pull()


def test_concurrent_edits_converge_with_conflict(tmp_path):
    remote = _make_remote(tmp_path)
    path_a, path_b = tmp_path / "a", tmp_path / "b"
    _init_clone(path_a, remote)
    _init_clone(path_b, remote)

    backend_a = Backend(path_a, "pw", default_by="alice", enable_git=False)
    backend_b = Backend(path_b, "pw", default_by="bob", enable_git=False)
    gs_a = GitSync(path_a, on_pull=backend_a._on_pull)
    gs_b = GitSync(path_b, on_pull=backend_b._on_pull)

    # A creates an item, sets a root title, and publishes both.
    create = backend_a.execute(_command("CreateItem", args={"board": "b", "column": "c", "title": "x"}))
    item_id = create.event.target
    backend_a.execute(_command("EditItem", target=item_id, field="title", value="milk"))
    _commit_push(gs_a, "a: create + root")

    # B pulls the shared history so both share the same title head.
    _pull(gs_b)
    assert backend_b.item(item_id).title.value == "milk"

    # Concurrent same-field edits, each based on the shared head.
    backend_a.execute(_command("EditItem", target=item_id, field="title", value="oat"))
    backend_b.execute(_command("EditItem", target=item_id, field="title", value="almond"))
    _commit_push(gs_a, "a: oat")
    _commit_push(gs_b, "b: almond")  # pulls a's oat first (clean union), then pushes
    _pull(gs_a)  # a picks up b's almond

    conflicts_a = backend_a.conflicts()
    conflicts_b = backend_b.conflicts()
    assert len(conflicts_a) == 1
    assert len(conflicts_b) == 1
    assert conflicts_a[0].field == "title"
    # Deterministic interim winner is identical on both machines.
    assert backend_a.item(item_id).title.value == backend_b.item(item_id).title.value


def _command(op, **kwargs):
    from tododo.actor import Command
    return Command(op=op, **kwargs)
