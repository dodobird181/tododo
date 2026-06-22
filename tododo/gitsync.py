"""Git synchronization.

The board YAML lives inside a git repo and git is treated as the durable
backing store:

  * Every mutation commits the board *immediately* (locally).
  * Pushes are bundled/throttled: a sync loop ticks once a second and, if there
    are new local commits, fetches+merges (always pull before push) and pushes.
  * When idle it still fetches periodically so the running app picks up edits
    made elsewhere.
  * Concurrent edits are reconciled with a **semantic 3-way merge** of the board
    by item id (see :mod:`tododo.merge`). Disjoint edits merge automatically;
    true same-item collisions are handed to the UI as conflicts to resolve.

All git calls are best-effort: failures (offline, etc.) are reflected in an
in-memory status string shown in the app status bar rather than crashing.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path

import yaml

from . import merge as merge_mod

# Sync loop tick. Pushes happen at most this often (bundling commits).
PUSH_INTERVAL_SECONDS = 1
# When there are no local changes, fetch remote at least this often.
POLL_INTERVAL_SECONDS = 120


class GitSync:
    def __init__(self, repo_root: Path, tracked_file: Path):
        self.repo_root = Path(repo_root)
        self.tracked_file = Path(tracked_file)
        try:
            self._rel = str(self.tracked_file.relative_to(self.repo_root))
        except ValueError:
            self._rel = self.tracked_file.name
        self._jobs: "queue.Queue[str | None]" = queue.Queue()
        self._dirty = threading.Event()      # unpushed local commits exist
        self._stop = threading.Event()
        self._lock = threading.Lock()        # guards self.status / self.last_fetch
        self._git_lock = threading.Lock()    # serializes git subprocess calls
        self.status = "git: idle"
        self.last_fetch: float = 0.0
        # Set when a pull changed the board file on disk; the UI reloads on it.
        self.board_changed = threading.Event()
        # Conflict handoff: a merge paused awaiting the user's choices.
        self.conflicts_ready = threading.Event()
        self._merge_pending = False
        self._pending: tuple[dict, list] | None = None
        self._enabled = self._is_repo()
        self._committer = threading.Thread(target=self._commit_loop, name="gitcommit", daemon=True)
        self._syncer = threading.Thread(target=self._sync_loop, name="gitsync", daemon=True)

    def start(self) -> None:
        if not self._enabled:
            self._set_status("git: not a repo (sync off)")
            return
        self._committer.start()
        self._syncer.start()

    def stop(self) -> None:
        self._stop.set()
        self._jobs.put(None)

    # --- public API ------------------------------------------------------

    def push_change(self, message: str) -> None:
        """Queue a commit for the latest board state (push happens, bundled, soon)."""
        if self._enabled:
            self._jobs.put(message)

    def last_fetch_label(self) -> str:
        with self._lock:
            ts = self.last_fetch
        if not ts:
            return "never"
        return time.strftime("%H:%M:%S", time.localtime(ts))

    def take_conflicts(self):
        """Return ``(merged_board, conflicts)`` for a paused merge, or None."""
        return self._pending

    def resolve_conflicts(self, choices: dict[str, str]) -> None:
        """Apply the user's per-conflict choices, finish the merge commit, push."""
        with self._git_lock:
            try:
                if not self._pending:
                    return
                merged, conflicts = self._pending
                final = merge_mod.apply_resolutions(merged, conflicts, choices,
                                                    lambda: uuid.uuid4().hex)
                self._write_board(final)
                self._git("add", self._rel)
                self._git("commit", "--no-edit")
                push = self._git("push")
                self._set_status("git: merged + pushed" if push.returncode == 0
                                 else "git: merged (push failed)")
                self.board_changed.set()
            except Exception as exc:
                self._set_status(f"git: resolve error {exc}")
            finally:
                self._pending = None
                self._merge_pending = False
                self.conflicts_ready.clear()

    def cancel_conflicts(self) -> None:
        """Abort the paused merge, restoring the pre-merge board."""
        with self._git_lock:
            self._git("merge", "--abort")
            self._pending = None
            self._merge_pending = False
            self.conflicts_ready.clear()
            self.board_changed.set()
            self._set_status("git: merge canceled")

    # --- internals -------------------------------------------------------

    def _set_status(self, text: str) -> None:
        with self._lock:
            self.status = text

    def _is_repo(self) -> bool:
        return (self.repo_root / ".git").exists()

    def _git(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _file_mtime(self) -> float:
        try:
            return self.tracked_file.stat().st_mtime
        except OSError:
            return 0.0

    def _board_at(self, ref: str) -> dict | None:
        res = self._git("show", f"{ref}:{self._rel}")
        if res.returncode != 0:
            return None
        try:
            return yaml.safe_load(res.stdout) or {}
        except yaml.YAMLError:
            return None

    def _write_board(self, board: dict) -> None:
        tmp = self.tracked_file.with_suffix(self.tracked_file.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(board, fh, sort_keys=False, allow_unicode=True)
        tmp.replace(self.tracked_file)

    def _commit(self, message: str) -> bool:
        """Commit the tracked file locally. Returns True if a commit was made."""
        self._git("add", self._rel)
        staged = self._git("diff", "--cached", "--quiet", "--", self._rel)
        if staged.returncode == 0:
            return False  # nothing staged
        self._git("commit", "-m", message)
        return True

    def _pull(self) -> None:
        """Fetch and reconcile remote changes (semantic merge on divergence)."""
        before = self._file_mtime()
        self._git("fetch", "--quiet")
        with self._lock:
            self.last_fetch = time.time()
        ahead = self._git("rev-list", "--count", "HEAD..@{u}").stdout.strip()
        if not ahead or ahead == "0":
            return  # already up to date
        our_ahead = self._git("rev-list", "--count", "@{u}..HEAD").stdout.strip()
        if our_ahead in ("", "0"):
            # No local commits: a plain fast-forward, no conflicts possible.
            ff = self._git("merge", "--ff-only", "@{u}")
            if ff.returncode == 0:
                if self._file_mtime() != before:
                    self.board_changed.set()
                self._set_status("git: pulled remote changes")
            else:
                self._set_status("git: needs manual merge")
            return
        self._semantic_merge(before)

    def _semantic_merge(self, before: float) -> None:
        base_ref = self._git("merge-base", "HEAD", "@{u}").stdout.strip()
        self._git("merge", "--no-commit", "--no-ff", "@{u}")
        # We only auto-resolve the board file; bail to manual for anything else.
        unmerged = self._git("diff", "--name-only", "--diff-filter=U").stdout.split()
        if any(Path(u).name != self.tracked_file.name for u in unmerged):
            self._git("merge", "--abort")
            self._set_status("git: needs manual merge")
            return

        base = self._board_at(base_ref)
        ours = self._board_at("HEAD")
        theirs = self._board_at("MERGE_HEAD")
        merged, conflicts = merge_mod.merge_boards(base, ours or {}, theirs or {})
        self._write_board(merged)

        if not conflicts:
            self._git("add", self._rel)
            self._git("commit", "--no-edit")
            if self._file_mtime() != before:
                self.board_changed.set()
            self._set_status("git: merged remote changes")
        else:
            # Pause the merge in progress and hand conflicts to the UI.
            self._pending = (merged, conflicts)
            self._merge_pending = True
            self.conflicts_ready.set()
            self._set_status(f"git: {len(conflicts)} conflict(s) — resolve in app")

    def _push(self) -> None:
        # Always fetch + merge before pushing.
        self._pull()
        if self._merge_pending:
            return  # cannot push until the user resolves conflicts
        push = self._git("push")
        if push.returncode == 0:
            self._set_status("git: pushed")
        else:
            self._set_status("git: push failed (offline?)")

    def _commit_loop(self) -> None:
        while not self._stop.is_set():
            message = self._jobs.get()
            if message is None:
                break
            # Don't commit on top of a paused (conflicted) merge.
            while self._merge_pending and not self._stop.is_set():
                self._stop.wait(0.2)
            try:
                with self._git_lock:
                    made = self._commit(message)
                if made:
                    self._dirty.set()
            except subprocess.TimeoutExpired:
                self._set_status("git: commit timeout")
            except Exception as exc:  # best-effort, never crash the UI
                self._set_status(f"git: commit error {exc}")

    def _sync_loop(self) -> None:
        last_poll = 0.0
        while not self._stop.is_set():
            if self._merge_pending:
                self._stop.wait(PUSH_INTERVAL_SECONDS)
                continue
            now = time.monotonic()
            try:
                if self._dirty.is_set():
                    self._dirty.clear()
                    self._set_status("git: pushing…")
                    with self._git_lock:
                        self._push()
                    last_poll = now
                elif now - last_poll >= POLL_INTERVAL_SECONDS:
                    with self._git_lock:
                        self._pull()
                    last_poll = now
            except subprocess.TimeoutExpired:
                self._set_status("git: timeout")
            except Exception as exc:
                self._set_status(f"git: error {exc}")
            self._stop.wait(PUSH_INTERVAL_SECONDS)
