"""Minimal Obsidian-style "soft" markdown renderer for pygame.

Supports a useful subset, laid out into rows that wrap to a width:

  * headings: ``#``, ``##``, ``###``
  * bullets: ``- `` / ``* `` (rendered with a • marker)
  * block quotes: ``> `` (muted, indented)
  * inline: ``**bold**``, ``*italic*`` / ``_italic_``, `` `code` ``

Rendering is "soft": the markup characters are dropped and the text is shown
styled, the way Obsidian's live preview does it.
"""

from __future__ import annotations

import re

import pygame

_INLINE_RE = re.compile(r"(\*\*.+?\*\*|__.+?__|\*.+?\*|_.+?_|`.+?`)")


class MarkdownFonts:
    """Bundle of fonts/colours used to render markdown."""

    def __init__(self, base_size: int, text_color, muted_color, code_color, accent_color):
        self.base = pygame.font.Font(None, base_size)
        self.bold = pygame.font.Font(None, base_size)
        self.bold.set_bold(True)
        self.italic = pygame.font.Font(None, base_size)
        self.italic.set_italic(True)
        self.code = pygame.font.Font(None, base_size)
        self.h1 = pygame.font.Font(None, base_size + 12)
        self.h1.set_bold(True)
        self.h2 = pygame.font.Font(None, base_size + 7)
        self.h2.set_bold(True)
        self.h3 = pygame.font.Font(None, base_size + 3)
        self.h3.set_bold(True)
        self.text_color = text_color
        self.muted_color = muted_color
        self.code_color = code_color
        self.accent_color = accent_color


def _inline_segments(text: str):
    """Split a line into (text, style) segments. style: '', 'b', 'i', 'code'."""
    segments = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            segments.append((text[pos:m.start()], ""))
        tok = m.group()
        if tok.startswith("**") or tok.startswith("__"):
            segments.append((tok[2:-2], "b"))
        elif tok.startswith("`"):
            segments.append((tok[1:-1], "code"))
        else:
            segments.append((tok[1:-1], "i"))
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], ""))
    return segments or [("", "")]


def _font_for(fonts: MarkdownFonts, style: str):
    return {"b": fonts.bold, "i": fonts.italic, "code": fonts.code}.get(style, fonts.base)


_HEADING_RE = re.compile(r"(#{1,3})\s")
_BULLET_RE = re.compile(r"[-*]\s")


def _apply_inline(line: str, lo: int, hi: int, styles: list, off: int, base: str) -> None:
    """Style the inline span line[lo:hi]: markers -> 'marker', inner -> b/i/code."""
    for k in range(lo, hi):
        styles[off + k] = base
    for m in _INLINE_RE.finditer(line[lo:hi]):
        s, e = lo + m.start(), lo + m.end()
        tok = m.group()
        if tok.startswith("**") or tok.startswith("__"):
            inner, mlen = "b", 2
        elif tok.startswith("`"):
            inner, mlen = "code", 1
        else:
            inner, mlen = "i", 1
        for k in range(s, e):
            styles[off + k] = "marker"
        for k in range(s + mlen, e - mlen):
            styles[off + k] = inner


def char_styles(text: str) -> list:
    """A per-character style tag for soft-rendering text *in place* (markers kept).

    Unlike :func:`flow`, no characters are dropped, so offsets map 1:1 onto the
    source string — letting an editable caret/selection stay exact while the text
    is shown styled. Styles: '' normal, 'b' bold, 'i' italic, 'code', 'h' heading,
    'quote', 'marker' (the de-emphasised markup characters).
    """
    styles = [""] * len(text)
    pos = 0
    for line in text.split("\n"):
        n = len(line)
        mh = _HEADING_RE.match(line)
        if mh:
            for k in range(mh.end()):
                styles[pos + k] = "marker"
            for k in range(mh.end(), n):
                styles[pos + k] = "h"
        elif _BULLET_RE.match(line):
            styles[pos] = styles[pos + 1] = "marker"
            _apply_inline(line, 2, n, styles, pos, base="")
        elif line[:2] == "> ":
            styles[pos] = styles[pos + 1] = "marker"
            for k in range(2, n):
                styles[pos + k] = "quote"
        else:
            _apply_inline(line, 0, n, styles, pos, base="")
        pos += n + 1  # +1 for the newline
    return styles


def flow(text: str, fonts: MarkdownFonts, max_w: int):
    """Lay out markdown into rows.

    Returns ``(rows, total_height)`` where each row is
    ``(row_height, indent, [(surface, x_within_row), ...])``.
    """
    rows = []
    space_w = fonts.base.size(" ")[0]

    for block in text.split("\n"):
        indent = 0
        color = fonts.text_color
        marker = ""
        heading_font = None

        if block.startswith("### "):
            heading_font, body = fonts.h3, block[4:]
        elif block.startswith("## "):
            heading_font, body = fonts.h2, block[3:]
        elif block.startswith("# "):
            heading_font, body = fonts.h1, block[2:]
        elif block.startswith(("- ", "* ")):
            marker, body, indent = "• ", block[2:], 0
        elif block.startswith("> "):
            color, indent, body = fonts.muted_color, 14, block[2:]
        else:
            body = block

        if heading_font is not None:
            tokens = [(w, heading_font, fonts.text_color) for w in body.split(" ")]
        else:
            tokens = []
            for seg_text, style in _inline_segments(body):
                font = _font_for(fonts, style)
                tok_color = fonts.code_color if style == "code" else color
                for word in seg_text.split(" "):
                    if word:
                        tokens.append((word, font, tok_color))

        # Greedy word-wrap, preserving the marker at the start of the first row.
        x = indent
        row_items = []
        row_h = 0
        first_row = True

        def line_marker():
            return marker if first_row and marker else ""

        prefix = line_marker()
        if prefix:
            surf = fonts.base.render(prefix, True, fonts.muted_color)
            row_items.append((surf, x))
            x += surf.get_width()
            row_h = max(row_h, surf.get_height())

        if not tokens:  # blank line -> keep vertical space
            rows.append((fonts.base.get_height(), indent, row_items))
            continue

        for word, font, tok_color in tokens:
            surf = font.render(word, True, tok_color)
            w = surf.get_width()
            gap = space_w if row_items and not (len(row_items) == 1 and prefix) else 0
            if row_items and x + gap + w > max_w:
                rows.append((row_h or font.get_height(), indent, row_items))
                first_row = False
                row_items = []
                x = indent + (font.size("  ")[0] if marker else 0)  # hang-indent wrapped bullets
                row_h = 0
                gap = 0
            x += gap
            row_items.append((surf, x))
            x += w
            row_h = max(row_h, surf.get_height())
        rows.append((row_h, indent, row_items))

    total = sum(h for h, _, _ in rows)
    return rows, total


def draw(surface, x: int, y: int, rows) -> int:
    """Blit laid-out rows at (x, y). Returns the bottom y."""
    cy = y
    for row_h, _indent, items in rows:
        for surf, ix in items:
            surface.blit(surf, (x + ix, cy))
        cy += row_h
    return cy
