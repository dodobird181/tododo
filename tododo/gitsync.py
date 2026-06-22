"""Git synchronization.

The board YAML lives inside a git repo and git is treated as the durable
backing store. This module runs a background worker thread that:

  * commits + pushes the board whenever the app mutates it (auto-push),
  * always fetches before pushing and merges remote changes first,
  * periodically fetches/pulls in the background so the running app picks up
    edits made elsewhere.

All git calls are best-effort: failures (offline, conflicts) are logged to an
in-memory status string shown in the app status bar rather than crashing.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path

# How often the background poller fetches + pulls remote changes.
POLL_INTERVAL_SECONDS = 120


class GitSync:
    def __init__(self, repo_root: Path, tracked_file: Path):
        self.repo_root = Path(repo_root)
        self.tracked_file = Path(tracked_file)
        self._jobs: "queue.Queue[tuple[str, str] | None]" = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.status = "git: idle"
        # Set whenever the local board file changed on disk via a pull, so the
        # UI thread knows to reload the board.
        self.board_changed = threading.Event()
        self._enabled = self._is_repo()
        self._worker = threading.Thread(target=self._run, name="gitsync", daemon=True)
        self._poller = threading.Thread(target=self._poll, name="gitpoll", daemon=True)

    def start(self) -> None:
        if not self._enabled:
            self._set_status("git: not a repo (sync off)")
            return
        self._worker.start()
        self._poller.start()

    def stop(self) -> None:
        self._stop.set()
        self._jobs.put(None)

    # --- public API ------------------------------------------------------

    def push_change(self, message: str) -> None:
        """Queue a commit + push for the latest board state."""
        if self._enabled:
            self._jobs.put(("push", message))

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

    def _pull(self) -> None:
        """Fetch and merge remote changes. Sets board_changed if file moved."""
        before = self._file_mtime()
        self._git("fetch", "--quiet")
        # Only merge if remote actually has new commits to avoid noise.
        res = self._git("rev-list", "--count", "HEAD..@{u}")
        ahead = res.stdout.strip()
        if ahead and ahead != "0":
            merge = self._git("merge", "--no-edit", "@{u}")
            if merge.returncode != 0:
                # Likely a conflict; back out so the app stays usable.
                self._git("merge", "--abort")
                self._set_status("git: merge conflict — manual fix needed")
                return
            if self._file_mtime() != before:
                self.board_changed.set()
            self._set_status("git: pulled remote changes")

    def _commit_and_push(self, message: str) -> None:
        self._git("add", str(self.tracked_file))
        status = self._git("status", "--porcelain", str(self.tracked_file))
        if not status.stdout.strip():
            # Nothing staged that differs; still try to push pending commits.
            pass
        else:
            self._git("commit", "-m", message)
        # Always fetch + merge before pushing.
        self._pull()
        push = self._git("push")
        if push.returncode == 0:
            self._set_status("git: pushed")
        else:
            self._set_status("git: push failed (offline?)")

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self._jobs.get()
            if job is None:
                break
            kind, message = job
            try:
                if kind == "push":
                    self._set_status("git: pushing…")
                    self._commit_and_push(message)
            except subprocess.TimeoutExpired:
                self._set_status("git: timeout")
            except Exception as exc:  # best-effort, never crash UI
                self._set_status(f"git: error {exc}")

    def _poll(self) -> None:
        # Stagger the first poll slightly so startup is snappy.
        time.sleep(5)
        while not self._stop.is_set():
            try:
                self._pull()
            except subprocess.TimeoutExpired:
                self._set_status("git: fetch timeout")
            except Exception as exc:
                self._set_status(f"git: poll error {exc}")
            # Sleep in small slices so stop() is responsive.
            for _ in range(POLL_INTERVAL_SECONDS):
                if self._stop.is_set():
                    return
                time.sleep(1)
