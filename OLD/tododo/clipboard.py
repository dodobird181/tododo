"""Tiny clipboard helper.

Tries the common Linux clipboard CLIs (Wayland's wl-clipboard, then X11's
xclip/xsel) and falls back to an in-process buffer so copy/paste always works
inside the app even with no system clipboard available.
"""

from __future__ import annotations

import shutil
import subprocess

_buffer = ""

_COPY_TOOLS = [
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
]
_PASTE_TOOLS = [
    ["wl-paste", "--no-newline"],
    ["xclip", "-selection", "clipboard", "-o"],
    ["xsel", "--clipboard", "--output"],
]


def copy(text: str) -> None:
    global _buffer
    _buffer = text
    for tool in _COPY_TOOLS:
        if shutil.which(tool[0]):
            try:
                subprocess.run(tool, input=text.encode(), timeout=2)
                return
            except Exception:
                continue


def paste() -> str:
    for tool in _PASTE_TOOLS:
        if shutil.which(tool[0]):
            try:
                res = subprocess.run(tool, capture_output=True, timeout=2)
                if res.returncode == 0:
                    return res.stdout.decode("utf-8", "replace")
            except Exception:
                continue
    return _buffer
