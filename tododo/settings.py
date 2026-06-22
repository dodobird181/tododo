"""App settings.

Mirrors the keybindings pattern: a version-controlled ``default_settings.yaml``
ships with the repo, and a gitignored ``settings.yaml`` (the user's personal
copy) is generated from it on first run and is what the app actually reads.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "default_settings.yaml"
USER_PATH = ROOT / "settings.yaml"

DEFAULTS = {
    "descriptions": "selected",      # "selected" or "all"
    "merge_conflicts": "incoming",   # "incoming" (-X theirs) or "current" (-X ours)
    "push_interval": 2,              # seconds between batched pushes
    "poll_interval": 2,              # seconds between background fetches for remote changes
    "poll_backoff_max": 300,         # cap (s) for exponential fetch backoff when idle
    "git_avatars": True,             # show a git-author avatar circle on each card
    "avatar_images": True,           # try to load a real Gravatar image (else monogram)
    "timestamps": "selected",        # show last-updated time: "selected" or "all"
    "timestamp_format": "%B {th}, %Y at %-I:%M %p (%Z)",  # strftime + {th} ordinal day
}


class Settings:
    def __init__(self, values: dict):
        self.values = values

    @classmethod
    def load(cls) -> "Settings":
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
