"""Git-derived item history.

Reads git commit history to reconstruct per-item events without storing
timestamps or actor info in the YAML file itself.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

TRACKED_FIELDS = ("title", "column", "points", "description", "due", "assignee", "reporter")


@dataclass(frozen=True)
class HistoryEvent:
    commit_hash: str
    author_email: str
    author_name: str
    timestamp: float        # unix seconds
    action: str             # "created" | "deleted" | field name from TRACKED_FIELDS
    field: str | None       # None for created/deleted; field name otherwise
    old_value: object
    new_value: object


def _parse_iso(s: str) -> float:
    """Parse ISO 8601 author date (e.g. '2026-06-22T14:27:10-04:00') → unix float."""
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _git_run(repo_root: Path, *args: str, timeout: int = 30) -> str:
    """Run a git command in repo_root without holding any lock. Returns stdout or ''."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _load_board_at(repo_root: Path, commit_ref: str, board_rel_path: str) -> dict[str, dict]:
    """Load board YAML at commit_ref → {item_id: item_dict}. Returns {} on any error."""
    raw = _git_run(repo_root, "show", f"{commit_ref}:{board_rel_path}", timeout=10)
    if not raw:
        return {}
    try:
        data = yaml.safe_load(raw) or {}
        return {str(item["id"]): item for item in data.get("items", []) if "id" in item}
    except Exception:
        return {}


def _diff_item(
    commit_hash: str,
    author_email: str,
    author_name: str,
    timestamp: float,
    before: dict | None,
    after: dict | None,
) -> list[HistoryEvent]:
    """Produce HistoryEvents for a single commit's change to one item."""
    events: list[HistoryEvent] = []
    if before is None and after is not None:
        events.append(HistoryEvent(
            commit_hash=commit_hash,
            author_email=author_email,
            author_name=author_name,
            timestamp=timestamp,
            action="created",
            field=None,
            old_value=None,
            new_value=after.get("column"),
        ))
    elif before is not None and after is None:
        events.append(HistoryEvent(
            commit_hash=commit_hash,
            author_email=author_email,
            author_name=author_name,
            timestamp=timestamp,
            action="deleted",
            field=None,
            old_value=before.get("column"),
            new_value=None,
        ))
    elif before is not None and after is not None:
        for field in TRACKED_FIELDS:
            bv = before.get(field)
            av = after.get(field)
            if bv != av:
                events.append(HistoryEvent(
                    commit_hash=commit_hash,
                    author_email=author_email,
                    author_name=author_name,
                    timestamp=timestamp,
                    action=field,
                    field=field,
                    old_value=bv,
                    new_value=av,
                ))
    return events


def _fetch_commits(
    repo_root: Path,
    board_rel_path: str,
    item_id: str,
    max_count: int,
) -> list[tuple[str, str, str, float]]:
    """Return [(hash, email, name, ts), ...] newest-first for commits touching item_id."""
    raw = _git_run(
        repo_root,
        "log",
        f"--max-count={max_count}",
        "--format=%H|%ae|%an|%aI",
        "--",
        board_rel_path,
        timeout=15,
    )
    if not raw.strip():
        return []
    rows = []
    for line in raw.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        h, ae, an, aI = parts
        rows.append((h.strip(), ae.strip(), an.strip(), _parse_iso(aI.strip())))
    return rows


def load_item_history(
    repo_root: str | Path,
    board_rel_path: str,
    item_id: str,
    max_commits: int = 50,
) -> list[HistoryEvent]:
    """Return per-field history events for one item, newest-first.

    Never raises — returns [] on any git/parse error.
    """
    repo_root = Path(repo_root)
    commits = _fetch_commits(repo_root, board_rel_path, item_id, max_commits + 1)
    if not commits:
        return []

    # Process oldest-first so we can diff consecutive states.
    events: list[HistoryEvent] = []
    for hash_, email, name, ts in reversed(commits):
        after = _load_board_at(repo_root, hash_, board_rel_path)
        before = _load_board_at(repo_root, f"{hash_}^", board_rel_path)
        events.extend(_diff_item(
            hash_, email, name, ts,
            before=before.get(item_id),
            after=after.get(item_id),
        ))

    # Return newest-first.
    events.reverse()
    return events


def latest_event(
    repo_root: str | Path,
    board_rel_path: str,
    item_id: str,
) -> HistoryEvent | None:
    """Fast path: return only the most recent event. Used for blame_line warm-up."""
    repo_root = Path(repo_root)
    commits = _fetch_commits(repo_root, board_rel_path, item_id, max_count=2)
    if not commits:
        return None
    hash_, email, name, ts = commits[0]
    after = _load_board_at(repo_root, hash_, board_rel_path)
    before = _load_board_at(repo_root, f"{hash_}^", board_rel_path)
    events = _diff_item(hash_, email, name, ts,
                        before=before.get(item_id),
                        after=after.get(item_id))
    return events[0] if events else None
