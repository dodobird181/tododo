"""Colour themes.

Themes live in the ``themes/`` folder and follow the same version-controlled /
user-overridable split as settings and keybindings:

  * ``themes/default_theme.yaml`` — the built-in default, Tokyo Night
    (version-controlled).
  * a handful of bundled alternates (``dracula``/``gruvbox``/``nord``), also
    version-controlled.
  * ``themes/current_theme.yaml`` — the active theme (gitignored); copied from
    the default on first run and overwritten whenever the user picks a theme.
  * any *other* file dropped in ``themes/`` is a user theme (gitignored too).

A theme is a flat map of colour name -> ``[r, g, b]`` (``overlay`` is RGBA), plus
``column_colors`` (a list of RGB). Missing keys fall back to the default.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
THEMES_DIR = ROOT / "themes"
DEFAULT_PATH = THEMES_DIR / "default_theme.yaml"
CURRENT_PATH = THEMES_DIR / "current_theme.yaml"

# The built-in default palette (mirrors the original hard-coded colours).
DEFAULTS: dict = {
    "bg": [24, 26, 32],
    "col_bg": [34, 37, 46],
    "col_header": [44, 48, 60],
    "card_bg": [52, 57, 71],
    "card_bg_sel": [70, 92, 130],
    "card_bg_drag": [90, 116, 160],
    "text": [228, 230, 236],
    "muted": [150, 156, 170],
    "accent": [110, 168, 254],
    "badge": [96, 200, 160],
    "overlay": [10, 12, 16, 210],
    "danger": [224, 108, 108],
    "code": [224, 196, 140],
    "selection": [74, 110, 165],
    # Locking: dotted outline when *you* select an item you hold the lock on;
    # a distinct highlight when the item is locked by *another* user.
    "lock_dotted": [240, 196, 110],
    "lock_other": [200, 90, 120],
    "column_colors": [
        [110, 168, 254], [240, 196, 110], [96, 200, 160],
        [200, 130, 240], [240, 140, 170],
    ],
}

KEYS = list(DEFAULTS.keys())


def _to_tuple(value):
    if value and isinstance(value[0], (list, tuple)):
        return [tuple(v) for v in value]  # column_colors
    return tuple(value)


def ensure_files() -> None:
    """Create the themes folder and the current theme on first run."""
    THEMES_DIR.mkdir(exist_ok=True)
    if not DEFAULT_PATH.exists():
        DEFAULT_PATH.write_text(yaml.safe_dump(DEFAULTS, sort_keys=False), encoding="utf-8")
    if not CURRENT_PATH.exists():
        src = DEFAULT_PATH if DEFAULT_PATH.exists() else None
        if src:
            shutil.copyfile(src, CURRENT_PATH)
        else:
            CURRENT_PATH.write_text(yaml.safe_dump(DEFAULTS, sort_keys=False), encoding="utf-8")


def load(path: Path) -> dict:
    """Load a theme file into a {key: tuple/colour} map, backfilled from defaults."""
    data = {}
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    colors = {}
    for key in KEYS:
        colors[key] = _to_tuple(data.get(key) or DEFAULTS[key])
    return colors


def load_current() -> dict:
    ensure_files()
    return load(CURRENT_PATH)


def apply_named(name: str) -> dict | None:
    """Make the named theme current (copy it onto current_theme.yaml). Returns
    the loaded colours, or None if no such theme."""
    path = THEMES_DIR / f"{name}.yaml"
    if not path.exists():
        return None
    colors = load(path)
    # Persist the raw file so edits to the source theme carry over verbatim.
    shutil.copyfile(path, CURRENT_PATH)
    return colors


def list_themes() -> list[str]:
    """Stems of selectable theme files (everything except the active copy)."""
    ensure_files()
    return sorted(p.stem for p in THEMES_DIR.glob("*.yaml") if p.name != CURRENT_PATH.name)
