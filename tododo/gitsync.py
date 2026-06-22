"""Git synchronization.

The board YAML lives inside a git repo and git is treated as the durable
backing store:

  * Every mutation commits the board *immediately* (locally).
  * Pushes are bundled/throttled: a sync loop ticks once a second and, if there
    are new local commits, fetches+merges (always pull before push) and pushes.
  * When idle it still fetches periodically so the running app picks up edits
    made elsewhere.
  * Concurrent edits are reconciled by git with a configurable strategy option:
    ``-X theirs`` (incoming wins) or ``-X ours`` (current wins). Non-conflicting
    changes always merge normally; a merge git still can't resolve falls back to
    "needs manual merge".

All git calls are best-effort: failures (offline, etc.) are reflected in an
in-memory status string shown in the app status bar rather than crashing.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path

from .board import Board

# How often the sync loop wakes to check for work.
TICK_SECONDS = 1


class GitSync:
    def __init__(self, repo_root: Path, tracked_file: Path, merge_option: str = "theirs",
                 push_interval: float = 2.0, poll_interval: float = 2.0,
                 poll_backoff_max: float = 300.0):
        self.repo_root = Path(repo_root)
        self.tracked_file = Path(tracked_file)
        self.merge_option = merge_option if merge_option in ("ours", "theirs") else "theirs"
        self.push_interval = max(0.5, float(push_interval))
        self.poll_interval = max(0.5, float(poll_interval))
        self.poll_backoff_max = max(self.poll_interval, float(poll_backoff_max))
        # Current (possibly backed-off) gap between idle fetches.
        self._cur_poll = self.poll_interval
        try:
            self._rel = str(self.tracked_file.relative_to(self.repo_root))
        except ValueError:
            self._rel = self.tracked_file.name
        self._jobs: "queue.Queue[str | None]" = queue.Queue()
        self._dirty = threading.Event()      # unpushed local commits exist
        self._fetch_now = threading.Event()  # external request for an immediate fetch
        self._stop = threading.Event()
        self._lock = threading.Lock()        # guards self.status / self.last_fetch
        self._git_lock = threading.Lock()    # serializes git subprocess calls
        self.status = "git: idle"
        self.last_fetch: float = 0.0
        # Set when a pull changed the board file on disk; the UI reloads on it.
        self.board_changed = threading.Event()
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

    def request_sync(self) -> None:
        """Ask the sync loop to fetch+merge right away (e.g. from a webhook)."""
        if self._enabled:
            self._fetch_now.set()

    def identity(self) -> tuple[str, str]:
        """The configured git user (name, email); cached, best-effort."""
        cached = getattr(self, "_identity", None)
        if cached is None:
            try:
                name = self._git("config", "user.name").stdout.strip()
                email = self._git("config", "user.email").stdout.strip()
            except Exception:
                name, email = "", ""
            cached = (name, email)
            self._identity = cached
        return cached

    def last_fetch_label(self) -> str:
        with self._lock:
            ts = self.last_fetch
        if not ts:
            return "never"
        return time.strftime("%H:%M:%S", time.localtime(ts))

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

    def _commit(self, message: str) -> bool:
        """Commit the tracked file locally. Returns True if a commit was made."""
        self._git("add", self._rel)
        staged = self._git("diff", "--cached", "--quiet", "--", self._rel)
        if staged.returncode == 0:
            return False  # nothing staged
        self._git("commit", "-m", message)
        return True

    def _pull(self) -> bool:
        """Fetch and reconcile remote changes. Returns True if the remote was ahead."""
        before = self._file_mtime()
        self._git("fetch", "--quiet")
        with self._lock:
            self.last_fetch = time.time()
        ahead = self._git("rev-list", "--count", "HEAD..@{u}").stdout.strip()
        if not ahead or ahead == "0":
            return False  # already up to date
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
            return True
        self._strategy_merge(before)
        return True

    def _strategy_merge(self, before: float) -> None:
        """Diverged history: let git merge with the configured -X option."""
        self._git("merge", "--no-commit", "--no-edit", "-X", self.merge_option, "@{u}")
        unmerged = self._git("diff", "--name-only", "--diff-filter=U").stdout.split()
        if unmerged:
            self._git("merge", "--abort")
            self._set_status("git: needs manual merge")
            return
        # Safety net: a line-based merge can occasionally produce a corrupt or
        # duplicated board. Parse-validate + normalize; bail to manual if broken.
        try:
            board = Board.load(self.tracked_file)
            board.save()
        except Exception:
            self._git("merge", "--abort")
            self._set_status("git: needs manual merge")
            return
        self._git("add", self._rel)
        self._git("commit", "--no-edit")
        if self._file_mtime() != before:
            self.board_changed.set()
        self._set_status(f"git: merged remote changes (-X {self.merge_option})")

    def _push(self) -> None:
        # Always fetch + merge before pushing.
        self._pull()
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
        last_push = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if self._fetch_now.is_set():
                    # Immediate fetch requested (webhook): pull right away.
                    self._fetch_now.clear()
                    with self._git_lock:
                        self._pull()
                    last_poll = now
                    self._cur_poll = self.poll_interval  # activity -> reset backoff
                elif self._dirty.is_set() and now - last_push >= self.push_interval:
                    # Batched push: send all commits accumulated since last push.
                    self._dirty.clear()
                    self._set_status("git: pushing…")
                    with self._git_lock:
                        self._push()
                    last_push = now
                    last_poll = now
                    self._cur_poll = self.poll_interval  # activity -> reset backoff
                elif now - last_poll >= self._cur_poll:
                    with self._git_lock:
                        pulled = self._pull()
                    last_poll = now
                    # Exponential backoff while idle: nothing remote AND nothing to
                    # push. Reset to the base interval the moment there's activity.
                    if pulled or self._dirty.is_set():
                        self._cur_poll = self.poll_interval
                    else:
                        self._cur_poll = min(self._cur_poll * 2, self.poll_backoff_max)
            except subprocess.TimeoutExpired:
                self._set_status("git: timeout")
            except Exception as exc:
                self._set_status(f"git: error {exc}")
            self._stop.wait(min(TICK_SECONDS, self.push_interval))
