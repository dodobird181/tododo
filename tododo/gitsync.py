"""
Git synchronization for the encrypted event mirror.

Only `events-encrypted/` is tracked. Every event is a uniquely-named file, so
two people editing the same data produce two different files: git only ever sees
clean file-adds and never a text merge conflict. Real (same-field) conflicts are
surfaced later by the projection, not by git.

Commits are queued and pushed, bundled; the loop always pulls before pushing and
fetches periodically when idle. All git calls are best-effort: failures (offline,
etc.) land in an in-memory status string instead of crashing. After a pull that
advances HEAD, `on_pull` fires so the filewatcher can decrypt+apply new events.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

TICK_SECONDS = 1


class GitSync:
    def __init__(
        self,
        repo_root: Path,
        tracked: tuple[str, ...] = ("events-encrypted",),
        on_pull: Callable[[], None] | None = None,
        push_interval: float = 2.0,
        poll_interval: float = 2.0,
        poll_backoff_max: float = 300.0,
    ):
        self.repo_root = Path(repo_root)
        self.tracked = list(tracked)
        self.on_pull = on_pull
        self.push_interval = max(0.5, float(push_interval))
        self.poll_interval = max(0.5, float(poll_interval))
        self.poll_backoff_max = max(self.poll_interval, float(poll_backoff_max))
        self._cur_poll = self.poll_interval
        self._jobs: "queue.Queue[str | None]" = queue.Queue()
        self._dirty = threading.Event()
        self._fetch_now = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._git_lock = threading.Lock()
        self.status = "git: idle"
        self.last_fetch: float = 0.0
        self._enabled = (self.repo_root / ".git").exists()
        self._committer = threading.Thread(target=self._commit_loop, name="gitcommit", daemon=True)
        self._syncer = threading.Thread(target=self._sync_loop, name="gitsync", daemon=True)

    # --- public API ------------------------------------------------------

    def start(self) -> None:
        if not self._enabled:
            self._set_status("git: not a repo (sync off)")
            return
        self._committer.start()
        self._syncer.start()

    def stop(self) -> None:
        self._stop.set()
        self._jobs.put(None)

    def push_change(self, message: str) -> None:
        """
        Queue a commit for the tracked mirror; the push happens bundled, soon.
        """
        if self._enabled:
            self._jobs.put(message)

    def request_sync(self) -> None:
        """
        Ask the sync loop to fetch+merge right away, bypassing the poll backoff.
        """
        if self._enabled:
            self._fetch_now.set()

    # --- internals -------------------------------------------------------

    def _set_status(self, text: str) -> None:
        with self._lock:
            self.status = text

    def _git(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _commit(self, message: str) -> bool:
        self._git("add", "--", *self.tracked)
        staged = self._git("diff", "--cached", "--quiet", "--", *self.tracked)
        if staged.returncode == 0:
            return False
        self._git("commit", "-m", message)
        return True

    def _pull(self) -> bool:
        """
        Fetch and reconcile. Returns True if the remote was ahead. Because event
        files never collide, a diverged merge unions the file-adds cleanly.
        """
        before = self._head()
        self._git("fetch", "--quiet")
        with self._lock:
            self.last_fetch = time.time()
        ahead = self._git("rev-list", "--count", "HEAD..@{u}").stdout.strip()
        if not ahead or ahead == "0":
            return False
        our_ahead = self._git("rev-list", "--count", "@{u}..HEAD").stdout.strip()
        if our_ahead in ("", "0"):
            self._git("merge", "--ff-only", "@{u}")
            self._set_status("git: pulled remote changes")
        else:
            self._git("merge", "--no-edit", "-X", "theirs", "@{u}")
            self._set_status("git: merged remote changes")
        if self._head() != before and self.on_pull:
            self.on_pull()
        return True

    def _push(self) -> None:
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
            except Exception as error:
                self._set_status(f"git: commit error {error}")

    def _sync_loop(self) -> None:
        last_poll = 0.0
        last_push = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if self._fetch_now.is_set():
                    self._fetch_now.clear()
                    with self._git_lock:
                        self._pull()
                    last_poll = now
                    self._cur_poll = self.poll_interval
                elif self._dirty.is_set() and now - last_push >= self.push_interval:
                    self._dirty.clear()
                    self._set_status("git: pushing…")
                    with self._git_lock:
                        self._push()
                    last_push = now
                    last_poll = now
                    self._cur_poll = self.poll_interval
                elif now - last_poll >= self._cur_poll:
                    with self._git_lock:
                        pulled = self._pull()
                    last_poll = now
                    if pulled or self._dirty.is_set():
                        self._cur_poll = self.poll_interval
                    else:
                        self._cur_poll = min(self._cur_poll * 2, self.poll_backoff_max)
            except subprocess.TimeoutExpired:
                self._set_status("git: timeout")
            except Exception as error:
                self._set_status(f"git: error {error}")
            self._stop.wait(min(TICK_SECONDS, self.push_interval))
