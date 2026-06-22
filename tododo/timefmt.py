"""Human-friendly formatting of item "last updated" timestamps.

Within the last 24 hours a relative phrase is used ("X minutes ago",
"2 hours and 15 minutes ago", with 15-minute granularity on the minutes part).
Older than that, the configured strftime format is used. The format may include
the custom token ``{th}`` which is replaced with the ordinal day of month
(e.g. ``1st``), since strftime has no ordinal directive.
"""

from __future__ import annotations

import time
from datetime import datetime

# Produces e.g. "July 1st, 2026 at 8:00 AM (EST)" (%-I is GNU/Linux).
DEFAULT_FORMAT = "%B {th}, %Y at %-I:%M %p (%Z)"


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _relative(delta: float) -> str:
    if delta < 60:
        return "less than a minute ago"
    if delta < 3600:
        mins = int(delta // 60)
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hours = int(delta // 3600)
    quarter = int((delta % 3600) // 60 // 15) * 15  # round minutes down to 15s
    hour_part = f"{hours} hour{'s' if hours != 1 else ''}"
    if quarter == 0:
        return f"{hour_part} ago"
    return f"{hour_part} and {quarter} minutes ago"


def format_timestamp(ts: float, fmt: str = DEFAULT_FORMAT, now: float | None = None) -> str:
    if not ts:
        return ""
    now = time.time() if now is None else now
    delta = max(0.0, now - ts)
    if delta < 24 * 3600:
        return _relative(delta)
    dt = datetime.fromtimestamp(ts)
    try:
        out = dt.strftime(fmt or DEFAULT_FORMAT)
    except ValueError:
        out = dt.strftime(DEFAULT_FORMAT.replace("%-I", "%I"))
    return out.replace("{th}", ordinal(dt.day))
