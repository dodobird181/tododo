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
    "webhook_enabled": False,        # listen for push notifications -> instant fetch
    "webhook_port": 8765,            # port the webhook receiver binds to
    "webhook_secret": "",            # optional shared secret (X-Hub-Signature-256)
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

    @property
    def webhook_enabled(self) -> bool:
        return bool(self.values.get("webhook_enabled", False))

    def webhook_port(self) -> int:
        try:
            return int(self.values.get("webhook_port", 8765))
        except (TypeError, ValueError):
            return 8765

    def webhook_secret(self) -> str:
        return str(self.values.get("webhook_secret", "") or "")
