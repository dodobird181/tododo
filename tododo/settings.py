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
