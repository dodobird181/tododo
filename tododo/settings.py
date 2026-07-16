"""
Local app settings.

A plaintext YAML file at `userdata/settings.yaml` (gitignored, per-machine),
modelled on the OLD app's `settings.py` and the sibling `keybindings.py` /
`workspace.py` modules: a `DEFAULT_SETTINGS` dict is the source of truth for the
values, `SETTINGS_HELP` is the source of truth for the descriptions, and the
whole thing is deliberately *not* event-sourced (these are machine-local
tunables, not shared domain state).

Each setting is persisted (and returned by the API) as a `{value, help}` pair,
so the YAML is self-documenting and a future settings UI can render the help
alongside each field. The `help` text is authoritative in code — it is rewritten
from `SETTINGS_HELP` on every save, so upgrades keep the descriptions current.

Today the file carries the defaults applied to a *new* item's start/end
datetimes, each expressed relative to the moment of creation: an offset from
now, then snapped onto a grid. The offset, the rounding direction, and the grid
interval are all configurable per field. The interval is clamped to a
five-minute minimum granularity.

Schema (each key maps to `{value, help}`)::

    default_start_offset_minutes:   <int>            # minutes from "now"
    default_start_rounding:         ceil|floor|none  # grid rounding direction
    default_start_interval_minutes: <int>            # grid size (min 5)
    default_end_offset_minutes:     <int>
    default_end_rounding:           ceil|floor|none
    default_end_interval_minutes:   <int>
"""

from __future__ import annotations

import math
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import yaml

MINIMUM_INTERVAL_MINUTES = 5
ROUNDING_MODES = ("ceil", "floor", "none")
DATETIME_FORMAT = "%Y-%m-%dT%H:%M"

DEFAULT_SETTINGS = {
    "default_start_offset_minutes": 30,
    "default_start_rounding": "ceil",
    "default_start_interval_minutes": 15,
    "default_end_offset_minutes": 60,
    "default_end_rounding": "ceil",
    "default_end_interval_minutes": 15,
}

SETTINGS_HELP = {
    "default_start_offset_minutes":
        "Minutes after 'now' to place a new item's start, before rounding.",
    "default_start_rounding":
        "How to snap the start onto the interval grid: 'ceil', 'floor', or 'none'.",
    "default_start_interval_minutes":
        "Grid size in minutes the start snaps to (clamped to a 5-minute minimum).",
    "default_end_offset_minutes":
        "Minutes after 'now' to place a new item's end, before rounding.",
    "default_end_rounding":
        "How to snap the end onto the interval grid: 'ceil', 'floor', or 'none'.",
    "default_end_interval_minutes":
        "Grid size in minutes the end snaps to (clamped to a 5-minute minimum).",
}


def load_settings(path: Path) -> dict:
    """
    Read the settings as `{key: {value, help}}`, backfilling any missing key from
    the defaults so callers never see a hole. A missing file yields the defaults.
    Accepts both the structured form and a legacy flat `{key: value}` file.
    """
    values = dict(DEFAULT_SETTINGS)
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        values.update(_flatten(raw))
    return _structured(_cleaned(values))


def save_settings(path: Path, data: dict) -> dict:
    """
    Persist the known settings (validated and coerced) as `{value, help}` pairs
    and return what was written. `data` may be structured or flat; the help text
    is always taken from `SETTINGS_HELP`.
    """
    values = dict(DEFAULT_SETTINGS)
    values.update(_flatten(data))
    structured = _structured(_cleaned(values))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(structured, sort_keys=False), encoding="utf-8")
    return structured


def default_datetimes(settings: dict, moment: datetime | None = None) -> tuple[str, str]:
    """
    Compute the default `(start, end)` for an item created at `moment` (now if
    omitted), each as a naive `YYYY-MM-DDTHH:MM` string matching the datetime the
    web UI's `datetime-local` inputs emit. `settings` may be structured or flat.
    """
    values = _cleaned({**DEFAULT_SETTINGS, **_flatten(settings)})
    moment = datetime.now() if moment is None else moment
    start = _round_to_grid(
        moment + timedelta(minutes=values["default_start_offset_minutes"]),
        values["default_start_interval_minutes"],
        values["default_start_rounding"],
    )
    end = _round_to_grid(
        moment + timedelta(minutes=values["default_end_offset_minutes"]),
        values["default_end_interval_minutes"],
        values["default_end_rounding"],
    )
    return start.strftime(DATETIME_FORMAT), end.strftime(DATETIME_FORMAT)


def _round_to_grid(moment: datetime, interval_minutes: int, mode: str) -> datetime:
    """
    Snap `moment` onto a grid of `interval_minutes` anchored at the top of the
    hour. `ceil`/`floor` pick the direction; `none` only drops sub-minute parts.
    """
    interval = max(MINIMUM_INTERVAL_MINUTES, int(interval_minutes))
    if mode == "none":
        return moment.replace(second=0, microsecond=0)
    anchor = moment.replace(minute=0, second=0, microsecond=0)
    elapsed_minutes = (moment - anchor).total_seconds() / 60.0
    if mode == "floor":
        steps = math.floor(elapsed_minutes / interval)
    else:
        steps = math.ceil(elapsed_minutes / interval)
    return anchor + timedelta(minutes=steps * interval)


def _structured(values: dict) -> dict:
    return {key: {"value": values[key], "help": SETTINGS_HELP[key]} for key in DEFAULT_SETTINGS}


def _flatten(data: dict | None) -> dict:
    """
    Reduce a structured-or-flat settings dict to `{key: value}` for the known
    keys, unwrapping any `{value, help}` entries.
    """
    data = data or {}
    result = {}
    for key in DEFAULT_SETTINGS:
        if key in data:
            result[key] = _value_of(data[key])
    return result


def _value_of(entry):
    if isinstance(entry, dict) and "value" in entry:
        return entry["value"]
    return entry


def _cleaned(data: dict) -> dict:
    return {
        "default_start_offset_minutes": _as_int(data.get("default_start_offset_minutes"), 30),
        "default_start_rounding": _as_mode(data.get("default_start_rounding")),
        "default_start_interval_minutes": _as_interval(data.get("default_start_interval_minutes")),
        "default_end_offset_minutes": _as_int(data.get("default_end_offset_minutes"), 60),
        "default_end_rounding": _as_mode(data.get("default_end_rounding")),
        "default_end_interval_minutes": _as_interval(data.get("default_end_interval_minutes")),
    }


def _as_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_mode(value) -> str:
    text = str(value).lower()
    return text if text in ROUNDING_MODES else "ceil"


def _as_interval(value) -> int:
    return max(MINIMUM_INTERVAL_MINUTES, _as_int(value, MINIMUM_INTERVAL_MINUTES))
