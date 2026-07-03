"""App settings.

Mirrors the keybindings pattern: a version-controlled ``default_settings.yaml``
ships with the repo, and a gitignored ``settings.yaml`` (the user's personal
copy) is generated from it on first run and is what the app actually reads.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from tododo.ascii_layout import parse_ascii_grid

ROOT = Path(__file__).resolve().parent.parent
USERDATA = ROOT / "userdata"
DEFAULT_PATH = ROOT / "default_settings.yaml"
USER_PATH = USERDATA / "settings.yaml"
_LEGACY_USER_PATH = ROOT / "settings.yaml"

DEFAULTS = {
    "descriptions": "selected",      # "selected" or "all"
    "max_description_height": 300,   # max pixel height for description on a card; 0 = no limit
    "merge_conflicts": "incoming",   # "incoming" (-X theirs) or "current" (-X ours)
    "push_interval": 2,              # seconds between batched pushes
    "poll_interval": 2,              # seconds between background fetches for remote changes
    "poll_backoff_max": 300,         # cap (s) for exponential fetch backoff when idle
    "git_avatars": True,             # show a git-author avatar circle on each card
    "avatar_images": True,           # try to load a real Gravatar image (else monogram)
    "timestamps": "selected",        # show last-updated time: "selected" or "all"
    "timestamp_format": "%B {th}, %Y at %-I:%M %p (%Z)",  # strftime + {th} ordinal day
    "item_layout": None,             # overridden by default_settings.yaml
}

# Built-in layout defaults (used when item_layout is absent from settings).
DEFAULT_ITEM_LAYOUT = {
    "collapsed": (
        "+---1---+\n"
        "| title |\n"
        "+-------+\n"
    ),
    "expanded": (
        "+-------2-------+\n"
        "| title         |\n"
        "+---------------+\n"
        "| description   |\n"
        "+---1---+---1---+\n"
        "| blame | due   |\n"
        "+---------------+\n"
        "| relationships |\n"
        "+---------------+\n"
    ),
}


def _parse_layout_row(row_str: str) -> list[tuple[str, int]]:
    """Parse "field1:w1 field2:w2" or "field" into [(field, weight), ...]."""
    cells = []
    for token in str(row_str).strip().split():
        if ":" in token:
            field, _, wt = token.partition(":")
            try:
                w = int(wt)
            except ValueError:
                w = 1
        else:
            field, w = token, 1
        field = field.strip()
        if field:
            cells.append((field, max(1, w)))
    return cells


def _parse_item_layout(rows) -> list[list[tuple[str, int]]]:
    result = []
    for row in (rows or []):
        cells = _parse_layout_row(str(row))
        if cells:
            result.append(cells)
    return result


def _ascii_to_layout(ascii_str: str) -> list[list[tuple[str, int]]]:
    cells = parse_ascii_grid(ascii_str)
    rows: dict[int, list[tuple[str, int]]] = {}
    for c in sorted(cells, key=lambda c: (c.row, c.col)):
        if c.field.strip():
            rows.setdefault(c.row, []).append((c.field, c.width_ratio))
    return [rows[r] for r in sorted(rows)]


class Settings:
    def __init__(self, values: dict):
        self.values = values

    @classmethod
    def load(cls) -> "Settings":
        USERDATA.mkdir(exist_ok=True)
        # Migrate legacy settings.yaml from repo root to userdata/ on first load.
        if _LEGACY_USER_PATH.exists() and not USER_PATH.exists():
            _LEGACY_USER_PATH.rename(USER_PATH)
        if not USER_PATH.exists():
            if DEFAULT_PATH.exists():
                shutil.copyfile(DEFAULT_PATH, USER_PATH)
            else:
                USER_PATH.write_text("")
        with USER_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        defaults = dict(DEFAULTS)
        if DEFAULT_PATH.exists():
            with DEFAULT_PATH.open("r", encoding="utf-8") as fh:
                defaults.update(yaml.safe_load(fh) or {})
        values = {k: data.get(k, defaults.get(k)) for k in defaults}
        return cls(values)

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    @property
    def descriptions_always(self) -> bool:
        return str(self.values.get("descriptions", "selected")).lower() == "all"

    @property
    def max_description_height(self) -> int:
        try:
            return max(0, int(self.values.get("max_description_height", 300)))
        except (TypeError, ValueError):
            return 300

    @property
    def git_avatars(self) -> bool:
        return bool(self.values.get("git_avatars", True))

    @property
    def avatar_images(self) -> bool:
        return bool(self.values.get("avatar_images", True))

    @property
    def timestamps_always(self) -> bool:
        return str(self.values.get("timestamps", "selected")).lower() == "all"

    def timestamp_format(self) -> str:
        return str(self.values.get("timestamp_format") or "%B {th}, %Y at %-I:%M %p (%Z)")

    def merge_option(self) -> str:
        """git merge -X option: 'theirs' for incoming-wins, 'ours' for current-wins."""
        return "ours" if str(self.values.get("merge_conflicts", "incoming")).lower() == "current" \
            else "theirs"

    def push_interval(self) -> float:
        try:
            return max(0.5, float(self.values.get("push_interval", 2)))
        except (TypeError, ValueError):
            return 2.0

    def poll_interval(self) -> float:
        try:
            return max(0.5, float(self.values.get("poll_interval", 2)))
        except (TypeError, ValueError):
            return 2.0

    def poll_backoff_max(self) -> float:
        try:
            return max(self.poll_interval(), float(self.values.get("poll_backoff_max", 300)))
        except (TypeError, ValueError):
            return 300.0

    def item_layout(self, mode: str) -> list[list[tuple[str, int]]]:
        """Parsed grid layout for 'collapsed' or 'expanded' mode."""
        spec = self.values.get("item_layout") or {}
        if not isinstance(spec, dict):
            spec = {}
        val = spec.get(mode) or DEFAULT_ITEM_LAYOUT.get(mode)
        if val is None:
            return []
        if isinstance(val, str):
            try:
                return _ascii_to_layout(val)
            except Exception:
                return []
        return _parse_item_layout(val)

