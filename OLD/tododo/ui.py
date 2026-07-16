"""Pygame UI for the kanban board."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from . import avatars, clipboard, markdown, theme, timefmt
import pygame
import yaml

from . import workspace
from .guimodel import UiBoard, UiItem as Item
from .keybindings import ACTIONS, Keybindings, code_to_key_name
from .settings import Settings

# Fixed card layouts (the old configurable item_layout system has been removed).
# Each layout is rows of (field, weight); the layout engine below renders them.
CARD_LAYOUT = {
    "collapsed": [[("title", 1)]],
    "expanded": [
        [("title", 1)],
        [("description", 1)],
        [("blame", 1), ("due", 1)],
        [("relationships", 1)],
    ],
}
PERSON_FIELDS = ["assignee", "reporter"]


def _iso_epoch(s: str) -> float:
    """Parse an ISO datetime string to unix seconds (0.0 on failure)."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0

# --- layout constants ----------------------------------------------------

WIDTH, HEIGHT = 1280, 800
TOPBAR_H = 56
STATUSBAR_H = 28
COL_GAP = 16
COL_PAD = 12
CARD_PAD = 10
CARD_GAP = 10
CARD_MIN_H = 56
HEADER_H = 44

# --- palette ("colors" — pun intended) -----------------------------------

BG = (24, 26, 32)
COL_BG = (34, 37, 46)
COL_HEADER = (44, 48, 60)
CARD_BG = (52, 57, 71)
CARD_BG_SEL = (70, 92, 130)
CARD_BG_DRAG = (90, 116, 160)
TEXT = (228, 230, 236)
MUTED = (150, 156, 170)
ACCENT = (110, 168, 254)
BADGE = (96, 200, 160)
OVERLAY = (10, 12, 16, 210)
DANGER = (224, 108, 108)
CODE = (224, 196, 140)
SELECTION = (74, 110, 165)
LOCK_DOTTED = (240, 196, 110)
LOCK_OTHER = (200, 90, 120)

COLUMN_COLORS = [(110, 168, 254), (240, 196, 110), (96, 200, 160), (200, 130, 240), (240, 140, 170)]


def apply_theme(colors: dict) -> None:
    """Swap the active palette by overwriting the module-level colour globals.

    Every render reads these by name at call time, so reassigning them here
    re-skins the whole UI without touching call sites."""
    g = globals()
    g["BG"] = colors["bg"]
    g["COL_BG"] = colors["col_bg"]
    g["COL_HEADER"] = colors["col_header"]
    g["CARD_BG"] = colors["card_bg"]
    g["CARD_BG_SEL"] = colors["card_bg_sel"]
    g["CARD_BG_DRAG"] = colors["card_bg_drag"]
    g["TEXT"] = colors["text"]
    g["MUTED"] = colors["muted"]
    g["ACCENT"] = colors["accent"]
    g["BADGE"] = colors["badge"]
    g["OVERLAY"] = colors["overlay"]
    g["DANGER"] = colors["danger"]
    g["CODE"] = colors["code"]
    g["SELECTION"] = colors["selection"]
    g["LOCK_DOTTED"] = colors["lock_dotted"]
    g["LOCK_OTHER"] = colors["lock_other"]
    g["COLUMN_COLORS"] = colors["column_colors"]


@dataclass
class CardRect:
    item: Item
    rect: pygame.Rect


def _avatar_color(seed: str) -> tuple[int, int, int]:
    """Deterministic mid-bright colour from a name/email seed."""
    h = hashlib.md5(seed.encode("utf-8")).digest()
    return (60 + h[0] % 180, 60 + h[1] % 180, 60 + h[2] % 180)


def _avatar_initials(name: str, email: str) -> str:
    source = name.strip() or email.strip()
    if not source:
        return "?"
    if not name.strip() and "@" in source:
        source = source.split("@", 1)[0]  # use the email's local part
    parts = [p for p in source.replace(".", " ").replace("_", " ").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return parts[0][:2].upper() if parts else "?"


def _layout_offsets(text: str, font, max_w: int) -> list[tuple[str, int]]:
    """Wrap text to max_w, returning (row_text, start_index) keeping exact offsets.

    Explicit newlines force a new row; long lines wrap at the last space (or hard-
    break a too-long word). Offsets map each row back to the source string so the
    caret and selection can be positioned precisely.
    """
    rows: list[tuple[str, int]] = []
    base = 0
    for line in text.split("\n"):
        if line == "":
            rows.append(("", base))
        else:
            seg_start = 0
            last_space = -1
            i = 0
            n = len(line)
            while i < n:
                if font.size(line[seg_start:i + 1])[0] > max_w and i > seg_start:
                    if last_space > seg_start:
                        rows.append((line[seg_start:last_space + 1], base + seg_start))
                        seg_start = last_space + 1
                    else:
                        rows.append((line[seg_start:i], base + seg_start))
                        seg_start = i
                    last_space = -1
                    i = seg_start
                    continue
                if line[i] == " ":
                    last_space = i
                i += 1
            rows.append((line[seg_start:], base + seg_start))
        base += len(line) + 1  # +1 for the newline separator
    return rows or [("", 0)]


def _offset_at(text: str, font, box: pygame.Rect, pos, scroll: int = 0) -> int:
    """Map a pixel position inside box to the nearest character offset in text."""
    rows = _layout_offsets(text, font, box.w - 20)
    lh = font.get_linesize()
    x0, y0 = box.x + 10, box.y + 6 - scroll
    row_idx = int((pos[1] - y0) // lh)
    row_idx = max(0, min(len(rows) - 1, row_idx))
    rowtext, start = rows[row_idx]
    relx = pos[0] - x0
    # Pick the boundary whose x is closest to the click.
    best_k, best_d = 0, float("inf")
    for k in range(len(rowtext) + 1):
        d = abs(font.size(rowtext[:k])[0] - relx)
        if d < best_d:
            best_d, best_k = d, k
        else:
            break  # widths are monotonic, so we can stop once it grows
    return start + best_k


class EditBuffer:
    """An editable string with a caret and a shift-selection.

    Holds the text, a caret offset, and an optional selection anchor. Shift+move
    extends the selection from the anchor; a plain move clears it. Insert /
    backspace / paste replace an active selection. Shared by all text widgets so
    selection behaves identically everywhere.
    """

    def __init__(self, text: str = ""):
        self.text = text
        self.cursor = len(text)
        self.anchor: int | None = None  # selection start; None = no selection

    # --- selection -------------------------------------------------------

    def has_selection(self) -> bool:
        return self.anchor is not None and self.anchor != self.cursor

    def selection_range(self) -> tuple[int, int]:
        return tuple(sorted((self.anchor, self.cursor)))  # type: ignore[arg-type]

    def selected_text(self) -> str:
        if not self.has_selection():
            return ""
        a, b = self.selection_range()
        return self.text[a:b]

    def select_all(self) -> None:
        self.anchor = 0
        self.cursor = len(self.text)

    def _delete_selection(self) -> None:
        if not self.has_selection():
            self.anchor = None
            return
        a, b = self.selection_range()
        self.text = self.text[:a] + self.text[b:]
        self.cursor = a
        self.anchor = None

    # --- editing ---------------------------------------------------------

    def insert(self, s: str) -> None:
        self._delete_selection()
        i = self.cursor
        self.text = self.text[:i] + s + self.text[i:]
        self.cursor = i + len(s)

    def backspace(self) -> None:
        if self.has_selection():
            self._delete_selection()
            return
        if self.cursor > 0:
            self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1

    # --- caret movement (select=True extends the selection) --------------

    def _pre_move(self, select: bool) -> None:
        if select:
            if self.anchor is None:
                self.anchor = self.cursor
        else:
            self.anchor = None

    def move_h(self, delta: int, select: bool) -> None:
        self._pre_move(select)
        self.cursor = max(0, min(len(self.text), self.cursor + delta))

    def move_v(self, delta: int, select: bool) -> None:
        """Move the caret up/down one line, keeping the column where possible.

        At the edges it collapses to an end: up on the first line goes to the
        start of the text, down on the last line goes to the end. (For a single-
        line field like the title, up = line start, down = line end.)
        """
        self._pre_move(select)
        text, cur = self.text, self.cursor
        line_start = text.rfind("\n", 0, cur) + 1
        col = cur - line_start
        if delta < 0:
            if line_start == 0:
                self.cursor = 0  # first line -> beginning of the line
                return
            prev_end = line_start - 1
            prev_start = text.rfind("\n", 0, prev_end) + 1
            self.cursor = prev_start + min(col, prev_end - prev_start)
        else:
            nl = text.find("\n", cur)
            if nl == -1:
                self.cursor = len(text)  # last line -> end of the line
                return
            next_start = nl + 1
            next_nl = text.find("\n", next_start)
            next_end = len(text) if next_nl == -1 else next_nl
            self.cursor = next_start + min(col, next_end - next_start)

    def clamp(self) -> None:
        self.cursor = min(self.cursor, len(self.text))
        if self.anchor is not None:
            self.anchor = min(self.anchor, len(self.text))


class TextInput:
    """Single-line text input modal state (e.g. point values)."""

    def __init__(self, prompt: str, on_submit, initial: str = "", numeric: bool = False):
        self.prompt = prompt
        self.on_submit = on_submit
        self.numeric = numeric
        self.buf = EditBuffer(initial)

    def handle(self, event) -> bool:
        """Return True when the modal should close."""
        if event.type != pygame.KEYDOWN:
            return False
        mods = event.mod
        b = self.buf
        if event.key == pygame.K_ESCAPE:
            return True
        if mods & pygame.KMOD_CTRL:
            if event.key == pygame.K_a:
                b.select_all()
                return False
            if event.key == pygame.K_c:
                clipboard.copy(b.selected_text() or b.text)
                return False
            if event.key == pygame.K_v:
                b.insert(self._filter(clipboard.paste()))
                return False
        if event.key == pygame.K_RETURN:
            self.on_submit(b.text)
            return True
        if event.key in (pygame.K_LEFT, pygame.K_RIGHT):
            b.move_h(-1 if event.key == pygame.K_LEFT else 1, bool(mods & pygame.KMOD_SHIFT))
            return False
        if event.key == pygame.K_BACKSPACE:
            b.backspace()
            return False
        ch = event.unicode
        if ch and ch.isprintable():
            ch = self._filter(ch)
            if ch:
                b.insert(ch)
        return False

    def _filter(self, s: str) -> str:
        return "".join(c for c in s if c.isdigit()) if self.numeric else s


class ItemEditor:
    """Two-field modal: title + (multi-line) description.

    TAB / Shift+TAB switch fields. ←/→ move the caret; ↑/↓ move it between lines
    within the field. Hold SHIFT while moving to highlight (select) text. ENTER in
    the title submits; in the description it inserts a newline (CTRL+ENTER
    submits). CTRL+A/C/V select-all/copy/paste. ESC cancels.
    """

    def __init__(self, prompt: str, on_submit, title: str = "", description: str = ""):
        self.prompt = prompt
        self.on_submit = on_submit
        self.labels = ["Title", "Description"]
        self.bufs = [EditBuffer(title), EditBuffer(description)]
        self.active = 0
        self.scroll = [0, 0]  # vertical scroll offset (px) per field

    @property
    def buf(self) -> EditBuffer:
        return self.bufs[self.active]

    def _switch(self, step: int) -> None:
        self.active = (self.active + step) % len(self.bufs)
        self.buf.clamp()

    def handle(self, event) -> bool:
        """Return True when the modal should close."""
        if event.type != pygame.KEYDOWN:
            return False
        mods = event.mod
        b = self.buf
        shift = bool(mods & pygame.KMOD_SHIFT)
        if event.key == pygame.K_ESCAPE:
            return True

        if mods & pygame.KMOD_CTRL:
            if event.key == pygame.K_a:
                b.select_all()
                return False
            if event.key == pygame.K_c:
                clipboard.copy(b.selected_text() or b.text)
                return False
            if event.key == pygame.K_v:
                b.insert(clipboard.paste())
                return False

        if event.key == pygame.K_TAB:
            self._switch(-1 if shift else 1)
            return False
        if event.key in (pygame.K_LEFT, pygame.K_RIGHT):
            b.move_h(-1 if event.key == pygame.K_LEFT else 1, shift)
            return False
        if event.key in (pygame.K_UP, pygame.K_DOWN):
            b.move_v(1 if event.key == pygame.K_DOWN else -1, shift)
            return False
        if event.key == pygame.K_RETURN:
            # Title field submits; description field inserts a newline unless CTRL.
            if self.active == 1 and not (mods & pygame.KMOD_CTRL):
                b.insert("\n")
                return False
            self.on_submit(self.bufs[0].text, self.bufs[1].text)
            return True
        if event.key == pygame.K_BACKSPACE:
            b.backspace()
            return False
        ch = event.unicode
        if ch and ch.isprintable():
            b.insert(ch)
        return False


class Confirm:
    def __init__(self, message: str, on_yes):
        self.message = message
        self.on_yes = on_yes


class App:
    # UI modes
    NORMAL = "normal"
    PALETTE = "palette"
    TEXT = "text"
    EDIT = "edit"
    CONFIRM = "confirm"
    KEYBINDINGS = "keybindings"
    VIEW_YAML = "view_yaml"
    RELATIONSHIPS = "relationships"
    SWITCH_BOARD = "switch_board"
    THEME = "theme"
    SEARCH = "search"
    VIEW_HISTORY = "view_history"

    def __init__(self, client, keys: Keybindings, settings: "Settings",
                 user: dict, board_name: str):
        pygame.init()
        pygame.display.set_caption("tododo — kanban on YAML")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
        # Held keys (backspace, arrows, ctrl+arrow moves) repeat continuously.
        pygame.key.set_repeat(300, 40)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, 24)
        self.font_sm = pygame.font.Font(None, 20)
        self.font_lg = pygame.font.Font(None, 34)
        apply_theme(theme.load_current())  # active palette before any colours are read
        self._build_md_fonts()
        # Background fetcher for real avatar images (GitHub, by author email).
        self.avatars = avatars.AvatarStore()
        self._authors_dirty = False  # unused (authors registry removed); kept for old paths

        self.client = client
        # Identity of the local user (from the server's git config).
        self.user = user or {}
        self.actor = self.user.get("github") or self.user.get("user") or "someone"
        self.keys = keys
        self.settings = settings
        # Client-backed board adapter (all reads/writes go through the server).
        self.board = UiBoard(client, board_name, self.actor)
        # Item id we currently hold a server lock on (released when selection moves).
        self._locked_id: str | None = None
        self._last_poll = 0

        self.mode = self.NORMAL
        self.selected_id: str | None = None
        # When no item is selected, the cursor can rest on an (often empty)
        # column — its title is highlighted, and Create targets it.
        self.focused_col: str | None = None
        # Multi-selection (shift+up/down): set of item ids for collective ops,
        # with sel_anchor_id marking where the range selection started.
        self.multi: set[str] = set()
        self.sel_anchor_id: str | None = None

        # Column display state: per-column scroll (session only) + minimized set,
        # the latter persisted per-machine in the gitignored workspace.yaml.
        self.col_scroll: dict[str, int] = {}
        self.workspace = workspace.Workspace.load()
        self.minimized = self.workspace.minimized(self._board_key())
        self.minimized &= set(self.board.columns)  # drop stale columns
        self.workspace.touch_opened(self._board_key())  # record this session's open
        self.workspace.save(self._board_key(), self.board.columns)
        self._col_toggle_rects: dict[str, pygame.Rect] = {}
        self.running = True
        # Board-switch modal state.
        self.sb_query: EditBuffer | None = None
        self.sb_index = 0
        self.sb_focus = "search"  # "search" | "list"
        self.sb_boards: list[str] = []
        # Theme-switch modal state (mirrors the board-switch view).
        self.th_query: EditBuffer | None = None
        self.th_index = 0
        self.th_focus = "search"
        self.th_themes: list[str] = []
        self._theme_cache: dict[str, dict] = {}  # name -> colours (for swatches)

        # drag state. A click sets a *candidate*; it only becomes a real drag
        # (pulled out of its column, drawn floating) once the mouse moves past a
        # small threshold, so a plain click just selects without anything moving.
        self.drag_item: Item | None = None
        self.drag_candidate: Item | None = None
        self.drag_offset = (0, 0)
        self.drag_pos = (0, 0)
        self.mouse_down_pos = None

        # modal state
        self.text_input: TextInput | None = None
        self.editor: ItemEditor | None = None
        self.confirm: Confirm | None = None
        # Field boxes from the last editor render, for click-to-focus hit-testing.
        self._editor_boxes: list[pygame.Rect] = []
        # YAML viewer state.
        self._yaml_buf: EditBuffer | None = None
        self._yaml_scroll = 0
        self._yaml_return = self.NORMAL
        # Avatar circles from the last render, for hover tooltips: (rect, label).
        self._avatar_hits: list[tuple[pygame.Rect, str]] = []

        # palette scroll state
        self._palette_scroll = 0
        # keybindings editor state
        self.kb_index = 0
        self.kb_capturing = False
        self.kb_scroll = 0  # vertical scroll offset in pixels
        # search modal state
        self.search_query: EditBuffer | None = None
        self.search_index = 0
        self.search_scroll = 0
        self.search_results: list[Item] = []
        # relationships editor state
        self.rel_index = 0

        # Per-item history is fetched on demand from the server (git-derived).
        self._history_cache: dict[str, list | None] = {}
        # History modal state.
        self._history_item_id: str | None = None
        self._history_scroll = 0

        # cached card rects from last render, for hit-testing
        self.card_rects: list[CardRect] = []
        self.column_rects: dict[str, pygame.Rect] = {}

        self.toast = ""
        self.toast_until = 0

        # Theme is still a local file; watch it for live re-skin.
        self._theme_mtime = self._mtime(theme.CURRENT_PATH)
        self._last_watch = 0

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _mtime(path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def notify(self, text: str, ms: int = 2000) -> None:
        self.toast = text
        self.toast_until = pygame.time.get_ticks() + ms

    def persist(self, message: str) -> None:
        """Flush buffered field edits to the server (which commits + pushes)."""
        self.board.flush()

    def selected(self) -> Item | None:
        return self.board.find(self.selected_id) if self.selected_id else None

    def _actor(self) -> str:
        """Github username credited with the current action."""
        return self.actor

    def _board_key(self) -> str:
        """Namespace key for the current board (its name)."""
        return self.board.name

    def _build_md_fonts(self) -> None:
        """(Re)build markdown fonts, which bake in the current theme colours."""
        self.md = markdown.MarkdownFonts(20, TEXT, MUTED, CODE, ACCENT)
        self._md_bold = pygame.font.Font(None, 24)
        self._md_bold.set_bold(True)
        self._md_italic = pygame.font.Font(None, 24)
        self._md_italic.set_italic(True)

    # --- main loop -------------------------------------------------------

    def _invalidate_history_cache(self) -> None:
        self._history_cache.clear()

    def _ensure_history_loaded(self, item_id: str) -> None:
        """Background: fetch one item's git history from the server (for the modal)."""
        def _load() -> None:
            self._history_cache[item_id] = self.client.item_history(item_id)
        threading.Thread(target=_load, name="histload", daemon=True).start()

    def run(self) -> None:
        while self.running:
            for event in pygame.event.get():
                self.handle_event(event)
            self._acquire_lock(self.selected_id)  # lock follows the selection
            self.poll_board()       # pick up others' edits from the server
            self.check_theme_reload()
            self.render()
            self.clock.tick(60)
        self._release_lock()
        pygame.quit()

    def reload_board(self) -> None:
        """Re-fetch the board + items from the server, keeping selection sane."""
        self.board.refresh()
        if self.selected_id and not self.board.find(self.selected_id):
            self.selected_id = None
        if self.focused_col not in self.board.columns:
            self.focused_col = None
        self.multi = {i for i in self.multi if self.board.find(i)}
        self.minimized &= set(self.board.columns)

    def poll_board(self) -> None:
        """Poll the server for board/item changes at ~1s cadence."""
        now = pygame.time.get_ticks()
        if now - self._last_poll < 1000:
            return
        self._last_poll = now
        self.reload_board()

    def check_theme_reload(self) -> None:
        """The theme is still a local file; hot-reload it on change."""
        now = pygame.time.get_ticks()
        if now - self._last_watch < 1000:
            return
        self._last_watch = now
        m = self._mtime(theme.CURRENT_PATH)
        if m and m != self._theme_mtime:
            self._theme_mtime = m
            apply_theme(theme.load_current())
            self._build_md_fonts()
            self._theme_cache.clear()
            self.notify("theme reloaded")

    # --- server-backed locking ------------------------------------------

    def _acquire_lock(self, item_id: str | None) -> None:
        """Acquire the server lock on the newly-selected item (releasing the old)."""
        if item_id == self._locked_id:
            return
        self._release_lock()
        if item_id and self.board.find(item_id):
            locked, holder = self.client.lock_item(item_id)
            if locked:
                self._locked_id = item_id

    def _release_lock(self) -> None:
        if self._locked_id:
            self.client.unlock_item(self._locked_id)
            self._locked_id = None

    def _can_edit(self, item: Item | None) -> bool:
        """True when the item is editable by us (not locked by another user)."""
        return bool(item) and not item.locked_by_other(self.actor)

    # --- event handling --------------------------------------------------

    def handle_event(self, event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return
        if event.type == pygame.VIDEORESIZE:
            self.screen = pygame.display.set_mode((event.w, event.h), pygame.RESIZABLE)
            return

        if self.mode == self.TEXT:
            if self.text_input and self.text_input.handle(event):
                self.text_input = None
                self.mode = self.NORMAL
            return
        if self.mode == self.EDIT:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self.editor_click(event.pos)
                return
            if event.type == pygame.MOUSEWHEEL:
                self.editor_scroll(event.y)
                return
            if (event.type == pygame.KEYDOWN and (event.mod & pygame.KMOD_CTRL)
                    and self.keys.matches("view_yaml", event.key) and self.selected()):
                self.open_yaml(self.selected(), self.EDIT)
                return
            if (event.type == pygame.KEYDOWN and (event.mod & pygame.KMOD_CTRL)
                    and self.keys.matches("view_history", event.key) and self.selected()):
                self.open_history(self.selected())
                return
            if (event.type == pygame.KEYDOWN and (event.mod & pygame.KMOD_CTRL)
                    and self.keys.matches("due_date", event.key) and self.selected()):
                self.start_due_date()
                return
            if self.editor and self.editor.handle(event):
                self.editor = None
                self.mode = self.NORMAL
            return
        if self.mode == self.VIEW_YAML:
            self.handle_yaml(event)
            return
        if self.mode == self.VIEW_HISTORY:
            self.handle_history(event)
            return
        if self.mode == self.SWITCH_BOARD:
            self.handle_switch_board(event)
            return
        if self.mode == self.THEME:
            self.handle_themes(event)
            return
        if self.mode == self.CONFIRM:
            self.handle_confirm(event)
            return
        if self.mode == self.KEYBINDINGS:
            if event.type == pygame.MOUSEWHEEL:
                self.kb_scroll = max(0, self.kb_scroll - event.y * self.font.get_linesize())
            else:
                self.handle_keybindings(event)
            return
        if self.mode == self.SEARCH:
            if event.type == pygame.MOUSEWHEEL:
                self.search_scroll = max(0, self.search_scroll - event.y * self.font.get_linesize())
            else:
                self.handle_search(event)
            return
        if self.mode == self.RELATIONSHIPS:
            self.handle_relationships(event)
            return
        if self.mode == self.PALETTE:
            if event.type == pygame.MOUSEWHEEL:
                self._palette_scroll = max(0, self._palette_scroll - event.y * self.font.get_linesize())
            else:
                self.handle_palette(event)
            return

        # NORMAL mode
        if event.type == pygame.KEYDOWN:
            ctrl = event.mod & pygame.KMOD_CTRL
            if ctrl:
                # CTRL+Enter opens the selected item, just like a plain Enter.
                if self.keys.matches("confirm", event.key) and self.selected():
                    self.start_edit()
                    return
                # CTRL+Y views the selected item's raw YAML.
                if self.keys.matches("view_yaml", event.key) and self.selected():
                    self.open_yaml(self.selected(), self.NORMAL)
                    return
                # CTRL+H views the selected item's git history.
                if self.keys.matches("view_history", event.key) and self.selected():
                    self.open_history(self.selected())
                    return
                # CTRL+A selects every item in the focused item's column.
                if self.keys.matches("select_column", event.key) and self.selected():
                    self.select_column()
                    return
                # CTRL+C / CTRL+V copy and paste items (via the system clipboard).
                if event.key == pygame.K_c:
                    self.copy_selection()
                    return
                if event.key == pygame.K_v:
                    self.paste_items()
                    return
                # CTRL+W sets a due date on the selected item.
                if self.keys.matches("due_date", event.key) and self.selected():
                    self.start_due_date()
                    return
                # CTRL+F opens fuzzy item search.
                if self.keys.matches("search", event.key):
                    self.start_search()
                    return
                # CTRL+<open_palette> opens the palette; CTRL+<action> runs it directly.
                if self.keys.matches("open_palette", event.key):
                    self.mode = self.PALETTE
                    self._palette_scroll = 0
                    return
                action = self.action_for_key(event.key)
                if action:
                    self.perform_action(action)
                    return
                return
            if event.key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
                self.navigate(event.key, bool(event.mod & pygame.KMOD_SHIFT))
                return
            # Open the selected item with the configurable "confirm" binding (Enter).
            if self.keys.matches("confirm", event.key) and self.selected():
                self.start_edit()
                return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.on_mouse_down(event.pos)
        elif event.type == pygame.MOUSEMOTION:
            self.on_mouse_motion(event.pos)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.on_mouse_up(event.pos)
        elif event.type == pygame.MOUSEWHEEL:
            self.scroll_column_at(pygame.mouse.get_pos(), event.y)

    # --- normal-mode mouse ----------------------------------------------

    def scroll_column_at(self, pos, dy: int) -> None:
        for name, rect in self.column_rects.items():
            if rect.collidepoint(pos) and name not in self.minimized:
                self.col_scroll[name] = max(0, self.col_scroll.get(name, 0) - dy * 40)
                return

    def card_at(self, pos) -> Item | None:
        for cr in self.card_rects:
            if cr.rect.collidepoint(pos):
                return cr.item
        return None

    def column_at(self, pos) -> str | None:
        for name, rect in self.column_rects.items():
            if rect.collidepoint(pos):
                return name
        return None

    DRAG_THRESHOLD = 6

    def on_mouse_down(self, pos) -> None:
        # Clicking a column header (or a minimized bar) toggles minimize.
        for name, trect in self._col_toggle_rects.items():
            if trect.collidepoint(pos):
                if name in self.minimized:
                    self.minimized.discard(name)
                else:
                    self.minimized.add(name)
                    self._focus_after_minimize(name)
                self.workspace.save(self._board_key(), self.board.columns)
                return
        item = self.card_at(pos)
        self.selected_id = item.id if item else None
        # Clicking empty space inside a column focuses that column (its title),
        # so a new item can be created there even when it has no cards yet.
        self.focused_col = None if item else self.column_at(pos)
        self._clear_multi()  # a click is a single selection
        self.mouse_down_pos = pos
        self.drag_item = None
        self.drag_candidate = None
        # An item locked by another user may be selected (to view) but not dragged.
        if item and not item.locked_by_other(self.actor):
            for cr in self.card_rects:
                if cr.item.id == item.id:
                    self.drag_candidate = item
                    self.drag_offset = (pos[0] - cr.rect.x, pos[1] - cr.rect.y)
                    self.drag_pos = pos
                    break

    def _focus_after_minimize(self, name: str) -> None:
        """After minimizing ``name``, move the cursor onto the nearest column that
        is still maximized so the collapse is felt immediately. If every column is
        now minimized, deselect everything (arrows/clicks re-enter normally)."""
        cols = self.board.columns
        candidates = [c for c in cols if c not in self.minimized]
        self._clear_multi()
        if not candidates:
            self.selected_id = None
            self.focused_col = None
            return
        idx = cols.index(name)
        # Nearest by column distance; ties resolve to the column on the right.
        nearest = min(candidates, key=lambda c: (abs(cols.index(c) - idx),
                                                 -(cols.index(c) > idx)))
        self._focus(nearest, 0)

    def on_mouse_motion(self, pos) -> None:
        if self.drag_item:
            self.drag_pos = pos
            return
        # Promote a candidate to a real drag only after moving past the threshold.
        if self.drag_candidate and self.mouse_down_pos:
            if (abs(pos[0] - self.mouse_down_pos[0]) > self.DRAG_THRESHOLD
                    or abs(pos[1] - self.mouse_down_pos[1]) > self.DRAG_THRESHOLD):
                self.drag_item = self.drag_candidate
                self.drag_pos = pos

    def on_mouse_up(self, pos) -> None:
        item = self.drag_item
        self.drag_item = None
        self.drag_candidate = None
        if not item:
            return  # plain click: selection only
        target_col = self.column_at(pos)
        if target_col is None:
            return
        position = self.drop_position(target_col, pos, dragging_id=item.id)
        self.board.reorder(item.id, target_col, position)
        self.persist(f"move '{item.title}' to {target_col}")

    def drop_position(self, column: str, pos, dragging_id: str) -> int:
        """Index within column based on the drop y-coordinate."""
        cards = [cr for cr in self.card_rects if cr.item.column == column and cr.item.id != dragging_id]
        for i, cr in enumerate(cards):
            if pos[1] < cr.rect.centery:
                return i
        return len(cards)

    # --- selection ------------------------------------------------------

    def _clear_multi(self) -> None:
        self.multi = set()
        self.sel_anchor_id = None

    def _select_range(self, column: str) -> None:
        """Set multi to the contiguous run between anchor and focus in column."""
        ids = [it.id for it in self.board.items_in(column)]
        if self.sel_anchor_id not in ids or self.selected_id not in ids:
            self.multi = {self.selected_id} if self.selected_id else set()
            return
        a, b = ids.index(self.sel_anchor_id), ids.index(self.selected_id)
        lo, hi = min(a, b), max(a, b)
        self.multi = set(ids[lo:hi + 1])

    def select_column(self) -> None:
        """Select every item in the focused item's column (CTRL+A)."""
        item = self.selected()
        if not item:
            return
        ids = [it.id for it in self.board.items_in(item.column)]
        if not ids:
            return
        self.multi = set(ids)
        self.sel_anchor_id = item.id
        self.notify(f"selected {len(ids)} in '{item.column}'")

    def selection(self) -> list[Item]:
        """Items targeted by collective actions (multi-selection, else the focus)."""
        if self.multi:
            return [it for it in self.board.items if it.id in self.multi]
        item = self.selected()
        return [item] if item else []

    def is_selected(self, item: Item) -> bool:
        return item.id == self.selected_id or item.id in self.multi

    # --- keyboard navigation --------------------------------------------

    def _focus(self, column: str, row: int) -> None:
        """Land the cursor in a column: select an item if it has any, else the
        (empty) column's title."""
        items = self.board.items_in(column)
        if items:
            self.selected_id = items[min(row, len(items) - 1)].id
            self.focused_col = None
        else:
            self.selected_id = None
            self.focused_col = column

    def navigate(self, key: int, shift: bool = False) -> None:
        cols = self.board.columns
        if not cols:
            return
        item = self.selected()
        # Resolve the current column from the selected item or the focused column.
        if item:
            cur_col = item.column
        elif self.focused_col in cols:
            cur_col = self.focused_col
        else:
            # Nothing focused yet: land on the first column with items, else col 0.
            for c in cols:
                if self.board.items_in(c):
                    self._focus(c, 0)
                    self._clear_multi()
                    return
            self._focus(cols[0], 0)
            self._clear_multi()
            return

        col_idx = cols.index(cur_col)
        col_items = self.board.items_in(cur_col)
        row = col_items.index(item) if item else 0

        if key in (pygame.K_UP, pygame.K_DOWN):
            if not col_items:
                return  # empty focused column: nothing to move between
            step = -1 if key == pygame.K_UP else 1
            if shift and item:
                # Extend a selection within the column (clamp, don't wrap).
                if self.sel_anchor_id is None:
                    self.sel_anchor_id = item.id
                new_row = max(0, min(len(col_items) - 1, row + step))
                self.selected_id = col_items[new_row].id
                self._select_range(cur_col)
            else:
                new_row = (row + step) % len(col_items)  # cycle within column
                self.selected_id = col_items[new_row].id
                self._clear_multi()
        else:  # left / right — step to the adjacent column, empty or not
            self._clear_multi()
            step = -1 if key == pygame.K_LEFT else 1
            target = cols[(col_idx + step) % len(cols)]
            self._focus(target, row)

    # --- palette / actions ----------------------------------------------

    # Keybinding groups: list of (group_label, [action, ...]) in display order.
    KB_GROUPS = [
        ("Palette", ["open_palette"]),
        ("Items", ["create", "delete", "point", "relationships", "select_column", "due_date"]),
        ("Movement", ["move_left", "move_right", "move_up", "move_down"]),
        ("Views", ["keybindings", "view_yaml", "view_history", "themes", "new_board", "switch_board", "search"]),
        ("Dialogs", ["confirm", "cancel"]),
    ]

    # Actions triggerable via the palette or CTRL+<key>.
    COMMAND_ACTIONS = [
        "create", "delete", "move_left", "move_right",
        "move_up", "move_down", "point", "relationships",
        "new_board", "switch_board", "themes", "keybindings",
        "due_date", "search", "view_history",
    ]

    def action_for_key(self, key: int) -> str | None:
        for action in self.COMMAND_ACTIONS:
            if self.keys.matches(action, key):
                return action
        return None

    def perform_action(self, action: str) -> None:
        if action == "create":
            self.start_create()
        elif action == "delete":
            self.start_delete()
        elif action == "point":
            self.start_point()
        elif action == "relationships":
            if self.selected():
                self.start_relationships()
            else:
                self.notify("no item selected")
        elif action == "new_board":
            self.start_new_board()
        elif action == "switch_board":
            self.start_switch_board()
        elif action == "themes":
            self.start_themes()
        elif action == "move_left":
            self.do_move(-1)
        elif action == "move_right":
            self.do_move(1)
        elif action == "move_up":
            self.do_move_vert(-1)
        elif action == "move_down":
            self.do_move_vert(1)
        elif action == "keybindings":
            self.mode = self.KEYBINDINGS
            self.kb_index = 0
            self.kb_capturing = False
        elif action == "due_date":
            if self.selected():
                self.start_due_date()
            else:
                self.notify("no item selected")
        elif action == "search":
            self.start_search()
        elif action == "view_history":
            if self.selected():
                self.open_history(self.selected())
            else:
                self.notify("no item selected")

    def handle_palette(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if self.keys.matches("cancel", event.key) or self.keys.matches("open_palette", event.key):
            self.mode = self.NORMAL
            return
        action = self.action_for_key(event.key)
        if action:
            self.perform_action(action)

    # --- copy / paste ----------------------------------------------------

    def _copy_payload(self, item: Item) -> dict:
        """The portable subset of an item (no ids/timestamps/blame)."""
        d: dict = {"title": item.title, "description": item.description}
        if item.due:
            d["due"] = item.due
        if item.assignee:
            d["assignee"] = item.assignee
        if item.reporter:
            d["reporter"] = item.reporter
        return d

    def copy_selection(self) -> None:
        """Copy the selected item(s) to the clipboard as YAML (CTRL+C)."""
        items = self.selection()
        if not items:
            self.notify("no item selected")
            return
        payload = [self._copy_payload(it) for it in items]
        clipboard.copy(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        self.notify(f"copied {len(items)} item(s)")

    def paste_items(self) -> None:
        """Create item(s) from clipboard YAML into the current column (CTRL+V)."""
        text = clipboard.paste()
        try:
            data = yaml.safe_load(text) if text else None
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            self.notify("clipboard isn't an item")
            return
        if self.selected():
            col = self.selected().column
        elif self.focused_col in self.board.columns:
            col = self.focused_col
        else:
            col = self.board.columns[0]
        created: list[Item] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            if not title:
                continue
            item = self.board.create(title, column=col,
                                     description=str(entry.get("description", "") or ""))
            item.due = str(entry.get("due", "") or "")
            item.assignee = str(entry.get("assignee", "") or "")
            item.reporter = str(entry.get("reporter", "") or "")
            created.append(item)
        if not created:
            self.notify("clipboard isn't an item")
            return
        self.selected_id = created[-1].id
        self.focused_col = None
        self.persist(f"paste {len(created)} item(s)")
        self.notify(f"pasted {len(created)} item(s)")

    def start_create(self) -> None:
        if self.selected():
            col = self.selected().column
        elif self.focused_col in self.board.columns:
            col = self.focused_col  # create into the focused (often empty) column
        else:
            col = self.board.columns[0]

        def submit(title: str, description: str) -> None:
            title = title.strip()
            if title:
                item = self.board.create(title, column=col, description=description.strip())
                self.selected_id = item.id
                self.focused_col = None
        self.editor = ItemEditor(f"New item in '{col}'", submit)
        self.mode = self.EDIT

    def _guard_locked(self) -> bool:
        """Refuse a mutation when any selected item is locked by another user.

        Returns True if blocked (and notifies), False if the edit may proceed.
        """
        for it in self.selection():
            if it.locked_by_other(self.actor):
                self.notify(f"locked by {it.lock_value}")
                self.mode = self.NORMAL
                return True
        return False

    def start_edit(self) -> None:
        item = self.selected()
        if not item:
            return
        if self._guard_locked():
            return

        orig_title, orig_desc = item.title, item.description

        def submit(title: str, description: str) -> None:
            title = title.strip()
            if not title:
                return
            description = description.strip()
            if title == orig_title and description == orig_desc:
                return  # no change — don't create a pointless commit
            item.title = title
            item.description = description
            self.persist(f"edit '{title}'")
        self.editor = ItemEditor("Edit item", submit, title=item.title, description=item.description)
        self.mode = self.EDIT

    def open_yaml(self, item: Item, return_mode: str) -> None:
        text = yaml.safe_dump(item.to_dict(), sort_keys=False, allow_unicode=True)
        self._yaml_buf = EditBuffer(text)
        self._yaml_scroll = 0
        self._yaml_return = return_mode
        self.mode = self.VIEW_YAML

    def handle_yaml(self, event) -> None:
        if event.type == pygame.MOUSEWHEEL:
            self._yaml_scroll = max(0, self._yaml_scroll - event.y * self.font.get_linesize())
            return
        if event.type != pygame.KEYDOWN:
            return
        if self.keys.matches("cancel", event.key) or self.keys.matches("view_yaml", event.key):
            self.mode = self._yaml_return

    def _board_files(self) -> list[str]:
        """Stems of the board YAML files that sit alongside the current board.

        Excludes the app's own config files (settings/keybindings/workspace and
        the version-controlled ``default_*`` templates).
        """
        return sorted(b.get("name", "") for b in self.client.list_boards() if b.get("name"))

    def start_new_board(self) -> None:
        """Prompt for a name and create a brand-new board file (CTRL+M)."""
        def submit(text: str) -> None:
            name = text.strip()
            if name:
                self._open_board(name, create=True)
        self.text_input = TextInput("New board file name:", submit, initial="")
        self.mode = self.TEXT

    def _switch_board_list(self) -> list[str]:
        """Available boards (current excluded) ordered most-recently-opened first.

        Boards never opened (no recorded timestamp) sort after opened ones, then
        alphabetically. The optional search query narrows by case-insensitive
        substring.
        """
        cur = self._board_key()
        boards = [b for b in self._board_files() if b != cur]
        q = (self.sb_query.text.strip().lower() if self.sb_query else "")
        if q:
            boards = [b for b in boards if q in b.lower()]
        boards.sort(key=lambda b: (-self.workspace.opened(b), b.lower()))
        return boards

    def start_switch_board(self) -> None:
        """Open the searchable board-switch view (CTRL+M)."""
        self.sb_query = EditBuffer("")
        self.sb_index = 0
        self.sb_focus = "search"
        self.sb_boards = self._switch_board_list()
        self.mode = self.SWITCH_BOARD

    def handle_switch_board(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if self.keys.matches("cancel", event.key):
            self.mode = self.NORMAL
            return
        boards = self.sb_boards
        if self.keys.matches("confirm", event.key):  # ENTER opens the selection
            if boards:
                self._open_board(boards[self.sb_index], create=False)
                self.mode = self.NORMAL
            return
        if event.key == pygame.K_DOWN:
            if self.sb_focus == "search":
                # The first board is already highlighted by default, so the first
                # DOWN should land on the *second* item (when there is one).
                self.sb_focus = "list"
                self.sb_index = 1 if len(boards) > 1 else 0
            elif boards:
                self.sb_index = min(len(boards) - 1, self.sb_index + 1)
            return
        if event.key == pygame.K_UP:
            if self.sb_focus == "list":
                if self.sb_index <= 0:
                    self.sb_focus = "search"
                else:
                    self.sb_index -= 1
            return
        # Anything else edits the search query (and refocuses the search box).
        before = self.sb_query.text
        if event.key == pygame.K_BACKSPACE:
            self.sb_query.backspace()
        elif event.unicode and event.unicode.isprintable():
            self.sb_query.insert(event.unicode)
        if self.sb_query.text != before:
            self.sb_focus = "search"
            self.sb_boards = self._switch_board_list()
            self.sb_index = 0  # auto-select first match as you type

    # --- theme switcher (mirrors the board-switch view) ------------------

    def _theme_list(self) -> list[str]:
        names = theme.list_themes()
        q = (self.th_query.text.strip().lower() if self.th_query else "")
        if q:
            names = [n for n in names if q in n.lower()]
        return names

    def start_themes(self) -> None:
        """Open the searchable colour-theme switcher (CTRL+T)."""
        self.th_query = EditBuffer("")
        self.th_index = 0
        self.th_focus = "search"
        self.th_themes = self._theme_list()
        self.mode = self.THEME

    def _apply_named_theme(self, name: str) -> None:
        colors = theme.apply_named(name)
        if colors is None:
            self.notify(f"no theme '{name}'")
            return
        apply_theme(colors)
        self._build_md_fonts()  # markdown fonts bake in colours; rebuild them
        self._theme_mtime = self._mtime(theme.CURRENT_PATH)
        self.notify(f"theme: {name}")

    def handle_themes(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if self.keys.matches("cancel", event.key):
            self.mode = self.NORMAL
            return
        names = self.th_themes
        if self.keys.matches("confirm", event.key):
            if names:
                self._apply_named_theme(names[self.th_index])
                self.mode = self.NORMAL
            return
        if event.key == pygame.K_DOWN:
            if self.th_focus == "search":
                self.th_focus = "list"
                self.th_index = 0
            elif names:
                self.th_index = min(len(names) - 1, self.th_index + 1)
            return
        if event.key == pygame.K_UP:
            if self.th_focus == "list":
                if self.th_index <= 0:
                    self.th_focus = "search"
                else:
                    self.th_index -= 1
            return
        before = self.th_query.text
        if event.key == pygame.K_BACKSPACE:
            self.th_query.backspace()
        elif event.unicode and event.unicode.isprintable():
            self.th_query.insert(event.unicode)
        if self.th_query.text != before:
            self.th_focus = "search"
            self.th_themes = self._theme_list()
            self.th_index = 0

    # --- fuzzy item search -----------------------------------------------

    def _search_score(self, item: Item, q: str) -> int:
        """Higher score = better match. 0 means no match."""
        title_l = item.title.lower()
        desc_l = item.description.lower()
        if q in title_l:
            # Exact prefix wins, then anywhere in title, then description.
            return 3 if title_l.startswith(q) else 2
        if q in desc_l:
            return 1
        # Fuzzy: all chars of q appear in title in order.
        ti = 0
        for ch in q:
            ti = title_l.find(ch, ti)
            if ti == -1:
                break
            ti += 1
        else:
            return 1
        return 0

    def _search_results(self) -> list[Item]:
        if not self.search_query:
            return []
        q = self.search_query.text.strip().lower()
        if not q:
            return list(self.board.items)
        scored = [(self._search_score(it, q), it) for it in self.board.items]
        scored = [(s, it) for s, it in scored if s > 0]
        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored]

    def start_search(self) -> None:
        self.search_query = EditBuffer("")
        self.search_index = 0
        self.search_scroll = 0
        self.search_results = self._search_results()
        self.mode = self.SEARCH

    def handle_search(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if self.keys.matches("cancel", event.key) or self.keys.matches("search", event.key):
            self.mode = self.NORMAL
            return
        results = self.search_results
        if self.keys.matches("confirm", event.key) and results:
            item = results[self.search_index]
            self.selected_id = item.id
            self.focused_col = None
            self._clear_multi()
            self.mode = self.NORMAL
            return
        if event.key == pygame.K_DOWN and results:
            self.search_index = min(len(results) - 1, self.search_index + 1)
            return
        if event.key == pygame.K_UP and results:
            self.search_index = max(0, self.search_index - 1)
            return
        # Everything else edits the query.
        if not self.search_query:
            return
        if event.key == pygame.K_BACKSPACE:
            self.search_query.backspace()
        elif event.unicode and event.unicode.isprintable():
            self.search_query.insert(event.unicode)
        self.search_results = self._search_results()
        self.search_index = 0
        self.search_scroll = 0

    def draw_search(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 680, 520)
        title_surf = self.font_lg.render("Search items", True, TEXT)
        self.screen.blit(title_surf, (rect.x + 24, rect.y + 18))
        sub = self.font_sm.render("type · UP/DOWN select · ENTER jump · ESC close", True, MUTED)
        self.screen.blit(sub, (rect.x + 24, rect.y + 54))

        # Search box.
        box = pygame.Rect(rect.x + 24, rect.y + 82, rect.w - 48, 38)
        pygame.draw.rect(self.screen, BG, box, border_radius=6)
        pygame.draw.rect(self.screen, ACCENT, box, width=2, border_radius=6)
        blink = (pygame.time.get_ticks() // 500) % 2 == 0
        if self.search_query is not None:
            self.draw_editable(self.font, box, self.search_query, blink)
        if self.search_query is None or not self.search_query.text:
            ph = self.font.render("Search…", True, MUTED)
            self.screen.blit(ph, (box.x + 12, box.y + box.h // 2 - ph.get_height() // 2))

        # Results — unified card rendering with variable heights.
        results = self.search_results
        card_w = rect.w - 24
        body = pygame.Rect(rect.x + 12, box.bottom + 8, card_w, rect.h - 112 - 38)

        # Precompute item heights so we can scroll correctly.
        heights = [
            self._card_height(item, card_w, force_expanded=(i == self.search_index))
            for i, item in enumerate(results)
        ]
        content_h = sum(ih + CARD_GAP for ih in heights) if heights else 0
        self.search_scroll = max(0, min(self.search_scroll, max(0, content_h - body.h)))

        # Keep selected card in view.
        if heights:
            sel_top = sum(heights[j] + CARD_GAP for j in range(self.search_index))
            sel_bot = sel_top + heights[self.search_index]
            if sel_top < self.search_scroll:
                self.search_scroll = sel_top
            elif sel_bot > self.search_scroll + body.h:
                self.search_scroll = sel_bot - body.h

        prev_clip = self.screen.get_clip()
        self.screen.set_clip(body.clip(prev_clip) if prev_clip else body)
        if not results:
            msg = "no results" if (self.search_query and self.search_query.text) else "no items"
            self.screen.blit(self.font.render(msg, True, MUTED), (rect.x + 24, body.y + 10))
        y = body.y - self.search_scroll
        for i, item in enumerate(results):
            ih = heights[i]
            if body.y <= y + ih and y < body.bottom:
                selected = i == self.search_index
                self.draw_card(item, body.x, y, card_w,
                               force_expanded=selected, force_selected=selected)
            y += ih + CARD_GAP
        self.screen.set_clip(prev_clip)

        # Scrollbar.
        if content_h > body.h:
            track_h = body.h - 4
            thumb_h = max(20, int(track_h * body.h / content_h))
            thumb_y = body.y + 2 + int((track_h - thumb_h) * self.search_scroll
                                        / max(1, content_h - body.h))
            pygame.draw.rect(self.screen, MUTED, (rect.right - 10, thumb_y, 4, thumb_h),
                             border_radius=2)

    def _open_board(self, name: str, create: bool) -> None:
        name = name.strip()
        if not name:
            return
        exists = self.client.get_board(name) is not None
        if not exists:
            if not create:
                self.notify(f"no board '{name}' — use CTRL+M to create it")
                return
            # Default columns mirror the built-in board layout.
            self.client.create_board(name, ["Todo", "Doing", "Done"])
        self._release_lock()
        self.board = UiBoard(self.client, name, self.actor)
        self.selected_id = None
        self._clear_multi()
        self._invalidate_history_cache()
        # Minimized columns are namespaced per board: load this board's own set.
        self.minimized = self.workspace.minimized(self._board_key())
        self.minimized &= set(self.board.columns)
        self.workspace.touch_opened(self._board_key())  # bump recency for ordering
        self.workspace.save(self._board_key(), self.board.columns)
        self.notify(f"{'created' if create and not exists else 'switched to'} board '{name}'")

    def start_delete(self) -> None:
        sel = self.selection()
        if not sel:
            self.notify("no item selected")
            self.mode = self.NORMAL
            return
        if self._guard_locked():
            return

        ids = [it.id for it in sel]

        def yes() -> None:
            for item_id in ids:
                self.board.delete(item_id)
            self.selected_id = None
            self._clear_multi()
            self.persist(f"delete {len(ids)} item(s)")
        message = f"Delete '{sel[0].title}'?" if len(sel) == 1 else f"Delete {len(sel)} items?"
        self.confirm = Confirm(message, yes)
        self.mode = self.CONFIRM

    def start_point(self) -> None:
        # Points were removed from the item schema.
        self.notify("points are no longer supported")
        self.mode = self.NORMAL

    def start_due_date(self) -> None:
        item = self.selected()
        if not item:
            return
        if self._guard_locked():
            return

        def submit(text: str) -> None:
            text = text.strip()
            # Accept empty string to clear the due date.
            item.due = text
            label = text or "cleared"
            self.persist(f"due date '{item.title}' = {label}")
        self.text_input = TextInput(
            f"Due date for '{item.title}' (YYYY-MM-DD, blank to clear):",
            submit, initial=item.due)
        self.mode = self.TEXT

    def do_move(self, delta: int) -> None:
        sel = self.selection()
        if not sel:
            self.notify("no item selected")
            self.mode = self.NORMAL
            return
        if self._guard_locked():
            return
        for item in sel:
            self.board.move_relative(item.id, delta)
        if len(sel) == 1:
            self.persist(f"move '{sel[0].title}' to {sel[0].column}")
        else:
            self.persist(f"move {len(sel)} items")
        self.mode = self.NORMAL

    def do_move_vert(self, delta: int) -> None:
        item = self.selected()
        if not item:
            self.notify("no item selected")
            self.mode = self.NORMAL
            return
        if self.board.move_within_column(item.id, delta):
            self.persist(f"reorder '{item.title}'")
        self.mode = self.NORMAL

    # --- confirm ---------------------------------------------------------

    def handle_confirm(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if self.keys.matches("confirm", event.key) or event.key == pygame.K_y:
            if self.confirm:
                self.confirm.on_yes()
            self.confirm = None
            self.mode = self.NORMAL
        elif self.keys.matches("cancel", event.key) or event.key == pygame.K_n:
            self.confirm = None
            self.mode = self.NORMAL

    # --- keybindings editor ---------------------------------------------

    def _kb_actions_flat(self) -> list[str]:
        """Actions in the same order as KB_GROUPS (used by keybindings editor)."""
        return [a for _, acts in self.KB_GROUPS for a in acts]

    def handle_keybindings(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        flat = self._kb_actions_flat()
        if self.kb_capturing:
            # Capture the next key as the new binding for the selected action; the
            # server owns the keybindings file, so persist through the client.
            action = flat[self.kb_index]
            value = code_to_key_name(event.key)
            self.keys.mapping[action] = value
            self.kb_capturing = False
            self.client.update_keybinding(action, value)
            self.notify(f"{action} -> {value}")
            return
        if self.keys.matches("cancel", event.key):
            self.mode = self.PALETTE
        elif event.key == pygame.K_UP:
            self.kb_index = (self.kb_index - 1) % len(flat)
        elif event.key == pygame.K_DOWN:
            self.kb_index = (self.kb_index + 1) % len(flat)
        elif event.key == pygame.K_RETURN:
            self.kb_capturing = True

    # --- rendering -------------------------------------------------------

    def render(self) -> None:
        w, h = self.screen.get_size()
        self.screen.fill(BG)
        self.draw_topbar(w)
        self.draw_columns(w, h)
        self.draw_dragged()
        self.draw_statusbar(w, h)
        if self.mode == self.NORMAL:
            self.draw_avatar_tooltip(w, h)

        if self.mode == self.PALETTE:
            self.draw_palette(w, h)
        elif self.mode == self.TEXT:
            self.draw_text_input(w, h)
        elif self.mode == self.EDIT:
            self.draw_editor(w, h)
        elif self.mode == self.CONFIRM:
            self.draw_confirm(w, h)
        elif self.mode == self.KEYBINDINGS:
            self.draw_keybindings(w, h)
        elif self.mode == self.VIEW_YAML:
            self.draw_yaml(w, h)
        elif self.mode == self.RELATIONSHIPS:
            self.draw_relationships(w, h)
        elif self.mode == self.SWITCH_BOARD:
            self.draw_switch_board(w, h)
        elif self.mode == self.THEME:
            self.draw_themes(w, h)
        elif self.mode == self.SEARCH:
            self.draw_search(w, h)
        elif self.mode == self.VIEW_HISTORY:
            self.draw_history(w, h)

        self.draw_toast(w, h)
        pygame.display.flip()

    def draw_topbar(self, w: int) -> None:
        pygame.draw.rect(self.screen, COL_HEADER, (0, 0, w, TOPBAR_H))
        title = self.font_lg.render("tododo", True, TEXT)
        self.screen.blit(title, (16, TOPBAR_H // 2 - title.get_height() // 2))
        board_name = self.font_lg.render(self._board_key(), True, ACCENT)
        self.screen.blit(board_name, (16 + title.get_width() + 14,
                                      TOPBAR_H // 2 - board_name.get_height() // 2))
        palette_key = self.keys.label("open_palette") or "SPACE"
        left_edge = 16 + title.get_width() + 14 + board_name.get_width() + 20
        hint = self.font_sm.render(
            f"{len(self.board.items)} items   ·   "
            f"CTRL+{palette_key}: commands   ·   arrows: navigate   ·   drag to move",
            True, MUTED)
        self.screen.blit(hint, (left_edge, TOPBAR_H // 2 - hint.get_height() // 2))

    MIN_COL_W = 46  # width of a minimized column

    def draw_columns(self, w: int, h: int) -> None:
        self.card_rects = []
        self.column_rects = {}
        self._avatar_hits = []
        self._col_toggle_rects = {}
        cols = self.board.columns
        n = max(1, len(cols))
        area_top = TOPBAR_H + COL_GAP
        area_bottom = h - STATUSBAR_H - COL_GAP
        col_h = area_bottom - area_top

        # A minimized column holding the focused item (or column) is temporarily maximized.
        sel = self.selected()
        sel_col = sel.column if sel else self.focused_col
        is_min = {c: (c in self.minimized and c != sel_col) for c in cols}

        n_min = sum(1 for c in cols if is_min[c])
        n_norm = max(1, len(cols) - n_min)
        total_gap = COL_GAP * (len(cols) + 1)
        norm_w = (w - total_gap - n_min * self.MIN_COL_W) / n_norm

        x = COL_GAP
        for i, col in enumerate(cols):
            minimized = is_min[col]
            cw = self.MIN_COL_W if minimized else norm_w
            rect = pygame.Rect(int(x), area_top, int(cw), col_h)
            self.column_rects[col] = rect
            accent = COLUMN_COLORS[i % len(COLUMN_COLORS)]
            if minimized:
                self.draw_minimized_column(col, rect, accent)
            else:
                self.draw_column(col, rect, accent)
            x += cw + COL_GAP

    def draw_minimized_column(self, name: str, rect: pygame.Rect, accent) -> None:
        pygame.draw.rect(self.screen, COL_BG, rect, border_radius=10)
        pygame.draw.rect(self.screen, accent, (rect.x, rect.y, rect.w, 6),
                         border_top_left_radius=10, border_top_right_radius=10)
        self._col_toggle_rects[name] = rect  # click anywhere to expand
        count = len(self.board.items_in(name))
        cnt = self.font_sm.render(str(count), True, MUTED)
        self.screen.blit(cnt, (rect.centerx - cnt.get_width() // 2, rect.y + 12))
        # Column name drawn vertically down the bar.
        vsurf = self.font_sm.render(name, True, TEXT)
        vsurf = pygame.transform.rotate(vsurf, 90)
        self.screen.blit(vsurf, (rect.centerx - vsurf.get_width() // 2, rect.y + 40))

    def draw_column(self, name: str, rect: pygame.Rect, accent) -> None:
        pygame.draw.rect(self.screen, COL_BG, rect, border_radius=10)
        header = pygame.Rect(rect.x, rect.y, rect.w, HEADER_H)
        pygame.draw.rect(self.screen, COL_HEADER, header, border_radius=10)
        pygame.draw.rect(self.screen, accent, (rect.x, rect.y, 6, HEADER_H), border_top_left_radius=10)

        # The cursor can rest on a column itself (no item selected) — highlight
        # the header so it's clear where a new item would be created.
        focused = (self.focused_col == name and not self.selected())
        if focused:
            pygame.draw.rect(self.screen, ACCENT, header, width=2, border_radius=10)

        # Minimize button: only this minus sign toggles minimize, not the whole
        # header (clicking the header elsewhere just focuses the column).
        btn = pygame.Rect(rect.right - HEADER_H, rect.y, HEADER_H, HEADER_H)
        self._col_toggle_rects[name] = btn
        hover = btn.collidepoint(pygame.mouse.get_pos())
        if hover:
            tint = tuple(min(255, c + 16) for c in COL_HEADER)
            pygame.draw.rect(self.screen, tint, btn, border_top_right_radius=10)
        bx, by = btn.center
        pygame.draw.line(self.screen, TEXT if hover else MUTED, (bx - 7, by), (bx + 7, by), 2)

        items = self.board.items_in(name)
        label = self.font.render(name, True, ACCENT if focused else TEXT)
        self.screen.blit(label, (rect.x + 16, rect.y + HEADER_H // 2 - label.get_height() // 2))
        meta = self.font_sm.render(f"{len(items)}", True, MUTED)
        self.screen.blit(meta, (btn.x - meta.get_width() - 8,
                                rect.y + HEADER_H // 2 - meta.get_height() // 2))

        # Scrollable body, clipped to the column.
        body = pygame.Rect(rect.x + 2, rect.y + HEADER_H, rect.w - 4, rect.h - HEADER_H)
        view_h = body.h - CARD_GAP
        scroll = self.col_scroll.get(name, 0)
        prev_clip = self.screen.get_clip()
        self.screen.set_clip(body.clip(prev_clip) if prev_clip else body)
        y = body.y + CARD_GAP - scroll
        content_h = 0
        for item in items:
            if self.drag_item and item.id == self.drag_item.id:
                continue  # drawn floating
            ch = self.draw_card(item, rect.x + COL_PAD, y, rect.w - 2 * COL_PAD)
            if ch.bottom > body.y and ch.y < body.bottom:
                self.card_rects.append(CardRect(item, ch))  # only hit-test visible cards
            # Keep the focused card in view (auto-scroll, applied next frame).
            if item.id == self.selected_id:
                if ch.y < body.y:
                    self.col_scroll[name] = scroll - (body.y - ch.y)
                elif ch.bottom > body.bottom:
                    self.col_scroll[name] = scroll + (ch.bottom - body.bottom)
            y += ch.height + CARD_GAP
            content_h += ch.height + CARD_GAP
        self.screen.set_clip(prev_clip)

        max_scroll = max(0, content_h - view_h)
        self.col_scroll[name] = max(0, min(self.col_scroll.get(name, 0), max_scroll))
        if content_h > view_h:
            track_h = body.h - 6
            thumb_h = max(24, int(track_h * view_h / content_h))
            thumb_y = body.y + 3 + int((track_h - thumb_h) * scroll / max(1, max_scroll))
            pygame.draw.rect(self.screen, MUTED, (rect.right - 6, thumb_y, 4, thumb_h), border_radius=2)

    def wrap_text(self, text: str, font, max_w: int) -> list[str]:
        words = text.split()
        lines, cur = [], ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if font.size(trial)[0] <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines or [""]

    def show_description(self, item: Item, floating: bool) -> bool:
        if not item.description:
            return False
        if self.settings.descriptions_always:
            return True
        return item.id == self.selected_id

    def show_timestamp(self, item: Item, floating: bool) -> bool:
        if not item.last_edited_at():
            return False
        return self.settings.timestamps_always or item.id == self.selected_id

    def blame_line(self, item: Item) -> str:
        """Blame sentence from the item's own per-field provenance (no git needed)."""
        when = item.last_edited_at()
        if not when:
            return ""
        ts = timefmt.ago(_iso_epoch(when), self.settings.timestamp_format())
        by = f" by {item.author}" if item.author else ""
        return f"Last edited {ts}{by}"

    # --- unified item display ---------------------------------------------

    def _compute_field(self, item: Item, field: str, w: int) -> tuple[int, object]:
        """Compute (pixel_height, cached_data) for a layout field without drawing."""
        if field == "title":
            lines = self.wrap_text(item.title, self.font, max(1, w - 40))
            return len(lines) * self.font.get_linesize(), lines
        if field == "description":
            if not item.description:
                return 0, None
            try:
                md_rows, h = markdown.flow(item.description, self.md, w)
            except Exception:
                return 0, None
            if not md_rows or h == 0:
                return 0, None
            max_h = self.settings.max_description_height
            if max_h and h > max_h:
                h = max_h
            return h + 6, md_rows
        if field == "blame":
            text = self.blame_line(item)
            return (self.font_sm.get_linesize() + 6, text) if text else (0, None)
        if field == "due":
            if not item.due:
                return 0, None
            return self.font_sm.get_linesize() + 4, f"Due: {item.due}"
        if field == "relationships":
            from OLD.tododo.board import PERSON_FIELDS
            parts = [f"{f}: @{self._handle_for(getattr(item, f))}"
                     for f in PERSON_FIELDS if getattr(item, f, "")]
            text = " · ".join(parts)
            return (self.font_sm.get_linesize() + 4, text) if text else (0, None)
        return 0, None

    def _draw_cached_field(self, field: str, x: int, y: int, w: int, cached, *,
                           item: Item | None = None) -> None:
        """Draw a pre-computed field at (x, y) within cell width w."""
        if field == "title":
            lines = cached or []
            if item and item.points:
                badge = self.font_sm.render(str(item.points), True, (20, 24, 30))
                bw = badge.get_width() + 14
                brect = pygame.Rect(x + w - bw - 4, y, bw, 22)
                pygame.draw.rect(self.screen, BADGE, brect, border_radius=11)
                self.screen.blit(badge, (brect.x + 7, brect.y + 11 - badge.get_height() // 2))
            ty = y
            for line in lines:
                self.screen.blit(self.font.render(line, True, TEXT), (x, ty))
                ty += self.font.get_linesize()
        elif field == "description":
            if cached:
                markdown.draw(self.screen, x, y + 6, cached)
        elif field == "blame":
            if cached:
                self.screen.blit(self.font_sm.render(cached, True, MUTED), (x, y + 6))
        elif field == "due":
            if cached:
                self.screen.blit(self.font_sm.render(cached, True, DANGER), (x, y + 4))
        elif field == "relationships":
            if cached:
                self.screen.blit(self.font_sm.render(cached, True, ACCENT), (x, y + 4))

    def _layout_card(self, item: Item, content_w: int,
                     layout: list[list[tuple[str, int]]]) -> tuple[int, list]:
        """Compute (total_content_h, rows_data) without drawing.

        rows_data is a list of (row_h, [(x_offset, cell_w, field, cached), ...]).
        Only rows with at least one non-zero-height cell are included.
        """
        rows_data = []
        total_h = 0
        for row_cells in layout:
            total_weight = sum(wt for _, wt in row_cells)
            if total_weight == 0:
                continue
            cell_list = []
            row_h = 0
            x_off = 0
            for field, wt in row_cells:
                cell_w = max(1, int(content_w * wt / total_weight))
                h, cached = self._compute_field(item, field, cell_w)
                row_h = max(row_h, h)
                cell_list.append((x_off, cell_w, field, cached))
                x_off += cell_w
            if row_h > 0:
                rows_data.append((row_h, cell_list))
                total_h += row_h
        return total_h, rows_data

    def _card_height(self, item: Item, w: int, *, force_expanded: bool | None = None) -> int:
        """Compute card height without drawing (for scroll pre-computation)."""
        show_av = self.settings.git_avatars
        av_d, av_gap = 24, 8
        left = CARD_PAD + (av_d + av_gap if show_av else 0)
        content_w = w - left - CARD_PAD
        if force_expanded is not None:
            expanded = force_expanded
        else:
            expanded = self.show_description(item, False) or self.show_timestamp(item, False)
        layout = CARD_LAYOUT["expanded" if expanded else "collapsed"]
        total_h, rows_data = self._layout_card(item, content_w, layout)
        n_gaps = max(0, len(rows_data) - 1)
        return max(CARD_MIN_H, total_h + n_gaps * 4 + 2 * CARD_PAD)

    def draw_card(self, item: Item, x: int, y: int, w: int, floating: bool = False, *,
                  force_expanded: bool | None = None,
                  force_selected: bool = False) -> pygame.Rect:
        show_av = self.settings.git_avatars
        av_d, av_gap = 24, 8
        left = CARD_PAD + (av_d + av_gap if show_av else 0)
        content_w = w - left - CARD_PAD
        content_x = x + left

        if force_expanded is not None:
            expanded = force_expanded
        else:
            expanded = self.show_description(item, floating) or self.show_timestamp(item, floating)
        layout = CARD_LAYOUT["expanded" if expanded else "collapsed"]

        total_content_h, rows_data = self._layout_card(item, content_w, layout)
        n_gaps = max(0, len(rows_data) - 1)
        card_h = max(CARD_MIN_H, total_content_h + n_gaps * 4 + 2 * CARD_PAD)
        rect = pygame.Rect(x, y, w, card_h)

        if floating:
            color = CARD_BG_DRAG
        elif force_selected or self.is_selected(item):
            color = CARD_BG_SEL
        else:
            color = CARD_BG
        pygame.draw.rect(self.screen, color, rect, border_radius=8)
        locked_other = item.locked_by_other(self.actor)
        if not floating and locked_other:
            # Held by another user: distinct lockee highlight (thick tint fill edge).
            pygame.draw.rect(self.screen, LOCK_OTHER, rect, width=3, border_radius=8)
        if not floating and item.id == self.selected_id:
            # We hold the lock on our selection: dotted outline in the lock colour,
            # layered over the lockee highlight during the brief hand-over window.
            if self._locked_id == item.id:
                self._draw_dotted_rect(rect, LOCK_DOTTED)
            else:
                pygame.draw.rect(self.screen, ACCENT, rect, width=2, border_radius=8)
        elif not floating and (item.id in self.multi or force_selected):
            pygame.draw.rect(self.screen, ACCENT, rect, width=1, border_radius=8)

        if show_av:
            self._draw_avatar(item, x, y, av_d, floating)

        ty = y + CARD_PAD
        for i, (row_h, cell_list) in enumerate(rows_data):
            if i > 0:
                ty += 4
            for x_off, cell_w, field, cached in cell_list:
                old_clip = self.screen.get_clip()
                self.screen.set_clip(pygame.Rect(content_x + x_off, ty, cell_w, row_h))
                self._draw_cached_field(field, content_x + x_off, ty, cell_w, cached, item=item)
                self.screen.set_clip(old_clip)
            ty += row_h

        return rect

    def _draw_dotted_rect(self, rect: pygame.Rect, color, dash: int = 5, gap: int = 4) -> None:
        """Draw a dotted/dashed rectangle border (used for a held-lock selection)."""
        step = dash + gap
        for x in range(rect.left, rect.right, step):
            pygame.draw.line(self.screen, color, (x, rect.top), (min(x + dash, rect.right), rect.top), 2)
            pygame.draw.line(self.screen, color, (x, rect.bottom - 1), (min(x + dash, rect.right), rect.bottom - 1), 2)
        for y in range(rect.top, rect.bottom, step):
            pygame.draw.line(self.screen, color, (rect.left, y), (rect.left, min(y + dash, rect.bottom)), 2)
            pygame.draw.line(self.screen, color, (rect.right - 1, y), (rect.right - 1, min(y + dash, rect.bottom)), 2)

    def _avatar_image(self, email: str, name: str, size: int):
        """A circular GitHub avatar Surface for the email, or None (use monogram).

        Registers the author and caches any resolved github login / avatar_url into
        the version-controlled ``authors:`` registry so it is only looked up once.
        """
        if not (self.settings.avatar_images and email):
            return None
        if self.board.register_author(email, name):
            self._authors_dirty = True
        rec = self.board.authors.get(email, {})
        url = rec.get("avatar_url")
        if not url and rec.get("github"):
            url = avatars.github_png_url(rec["github"], size * 2)
        if not url:
            resolved = self.avatars.resolve(email)
            if resolved:
                if self.board.set_author_meta(email, github=resolved.get("login"),
                                              avatar_url=resolved.get("avatar_url")):
                    self._authors_dirty = True
                url = resolved.get("avatar_url")
        return self.avatars.image(url, size) if url else None

    def _draw_avatar(self, item: Item, x: int, y: int, av_d: int, floating: bool) -> None:
        email = item.assignee
        if not email:
            return
        rec = self.board.authors.get(email, {})
        name = rec.get("name") or ""
        r = av_d // 2
        cx, cy = x + CARD_PAD + r, y + CARD_PAD + r
        img = self._avatar_image(email, name, av_d)
        if img:
            self.screen.blit(img, (cx - r, cy - r))
        else:
            seed = email or name or item.id
            pygame.draw.circle(self.screen, _avatar_color(seed), (cx, cy), r)
            initials = self.font_sm.render(_avatar_initials(name, email), True, (18, 22, 28))
            self.screen.blit(initials, (cx - initials.get_width() // 2, cy - initials.get_height() // 2))
        if not floating:
            gh = self.board.authors.get(email, {}).get("github")
            if gh:
                who = f"{name} (@{gh})" if name else f"@{gh}"
            else:
                who = name or "unknown"
            label = f"Assignee: {who} <{email}>" if email else who
            self._avatar_hits.append((pygame.Rect(cx - r, cy - r, av_d, av_d), label))

    def draw_avatar_tooltip(self, w: int, h: int) -> None:
        mx, my = pygame.mouse.get_pos()
        for rect, label in self._avatar_hits:
            if rect.collidepoint(mx, my):
                surf = self.font_sm.render(label, True, TEXT)
                pad = 8
                tip = pygame.Rect(0, 0, surf.get_width() + 2 * pad, surf.get_height() + 2 * pad)
                tip.topleft = (min(mx + 14, w - tip.w - 4), my + 14)
                pygame.draw.rect(self.screen, COL_HEADER, tip, border_radius=6)
                pygame.draw.rect(self.screen, ACCENT, tip, width=1, border_radius=6)
                self.screen.blit(surf, (tip.x + pad, tip.y + pad))
                return

    def draw_dragged(self) -> None:
        if not self.drag_item:
            return
        x = self.drag_pos[0] - self.drag_offset[0]
        y = self.drag_pos[1] - self.drag_offset[1]
        # width matches a column's card width if known
        col_rect = self.column_rects.get(self.drag_item.column)
        w = (col_rect.w - 2 * COL_PAD) if col_rect else 240
        self.draw_card(self.drag_item, x, y, w, floating=True)

    def draw_statusbar(self, w: int, h: int) -> None:
        rect = pygame.Rect(0, h - STATUSBAR_H, w, STATUSBAR_H)
        pygame.draw.rect(self.screen, COL_HEADER, rect)
        if self.client.last_error:
            status = f"server: {self.client.last_error}"
        else:
            status = f"server: connected ({self.actor})"
        line = f"{status}"
        surf = self.font_sm.render(line, True, MUTED)
        self.screen.blit(surf, (12, h - STATUSBAR_H + 5))
        # Footnote: how to reach the command palette.
        palette_key = self.keys.label("open_palette") or "SPACE"
        note = self.font_sm.render(f"CTRL+{palette_key} lets you see available commands", True, MUTED)
        self.screen.blit(note, (w - note.get_width() - 12, h - STATUSBAR_H + 5))

    # --- overlays --------------------------------------------------------

    def dim(self, w: int, h: int) -> None:
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill(OVERLAY)
        self.screen.blit(overlay, (0, 0))

    def panel(self, w: int, h: int, pw: int, ph: int) -> pygame.Rect:
        rect = pygame.Rect((w - pw) // 2, (h - ph) // 2, pw, ph)
        pygame.draw.rect(self.screen, COL_BG, rect, border_radius=12)
        pygame.draw.rect(self.screen, ACCENT, rect, width=2, border_radius=12)
        return rect

    def draw_palette(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 480, 560)
        title = self.font_lg.render("Commands", True, TEXT)
        self.screen.blit(title, (rect.x + 24, rect.y + 18))
        sel = self.selected()
        subtitle = f"selected: {sel.title}" if sel else "no item selected"
        sub = self.font_sm.render(subtitle, True, MUTED)
        self.screen.blit(sub, (rect.x + 24, rect.y + 52))

        rows = [
            ("create", "Create new item"),
            ("delete", "Delete selected item"),
            ("move_left", "Move selected left"),
            ("move_right", "Move selected right"),
            ("move_up", "Move selected up"),
            ("move_down", "Move selected down"),
            ("point", "Assign points"),
            ("relationships", "Edit people (creator/assignee/reporter)"),
            ("new_board", "Create new board"),
            ("switch_board", "Switch board"),
            ("themes", "Switch colour theme"),
            ("keybindings", "Edit keybindings"),
            ("due_date", "Set due date (CTRL+D)"),
            ("view_history", "View item history"),
        ]

        ROW_H = 34
        body = pygame.Rect(rect.x + 12, rect.y + 78, rect.w - 24, rect.h - 108)
        content_h = len(rows) * ROW_H
        view_h = body.h
        self._palette_scroll = max(0, min(self._palette_scroll, max(0, content_h - view_h)))

        prev_clip = self.screen.get_clip()
        self.screen.set_clip(body.clip(prev_clip) if prev_clip else body)

        y = body.y - self._palette_scroll
        for action, desc in rows:
            if body.y <= y + ROW_H and y < body.bottom:
                keylabel = self.keys.label(action)
                key_surf = self.font.render(f"[{keylabel}]", True, ACCENT)
                self.screen.blit(key_surf, (rect.x + 24, y + (ROW_H - self.font.get_linesize()) // 2))
                desc_surf = self.font.render(desc, True, TEXT)
                self.screen.blit(desc_surf, (rect.x + 130, y + (ROW_H - self.font.get_linesize()) // 2))
            y += ROW_H

        self.screen.set_clip(prev_clip)

        if content_h > view_h:
            track_h = view_h - 4
            thumb_h = max(20, int(track_h * view_h / content_h))
            scroll_ratio = self._palette_scroll / max(1, content_h - view_h)
            thumb_y = body.y + 2 + int((track_h - thumb_h) * scroll_ratio)
            pygame.draw.rect(self.screen, MUTED,
                             (rect.right - 10, thumb_y, 4, thumb_h), border_radius=2)

        foot = self.font_sm.render(
            "press a key, or use CTRL+<key> any time   ·   [ESC] close", True, MUTED)
        self.screen.blit(foot, (rect.x + 24, rect.bottom - 28))

    def editor_click(self, pos) -> None:
        """Click in the editor: focus the clicked field and place the caret there."""
        if not self.editor:
            return
        for i, box in enumerate(self._editor_boxes):
            if box.collidepoint(pos):
                self.editor.active = i
                buf = self.editor.bufs[i]
                if i == self._EDITOR_MD_FIELD:  # description renders as soft markdown
                    buf.cursor = self._md_offset_at(buf.text, box, pos, self.editor.scroll[i])
                else:
                    buf.cursor = _offset_at(buf.text, self.font, box, pos, self.editor.scroll[i])
                buf.anchor = None
                return

    def editor_scroll(self, dy: int) -> None:
        """Mouse-wheel scroll the editor field under the cursor (else the active one)."""
        if not self.editor:
            return
        mx, my = pygame.mouse.get_pos()
        idx = self.editor.active
        for i, box in enumerate(self._editor_boxes):
            if box.collidepoint(mx, my):
                idx = i
                break
        step = self.font.get_linesize()
        self.editor.scroll[idx] = max(0, self.editor.scroll[idx] - dy * step)

    def draw_editable(self, font, box: pygame.Rect, buf: "EditBuffer", show_caret: bool,
                      scroll: int = 0) -> int:
        """Render an EditBuffer inside box (text, selection, caret), clipped and
        vertically scrolled. Content taller than the box scrolls; the caret is kept
        in view and a scrollbar is drawn. Returns the (clamped) scroll offset."""
        sel = buf.selection_range() if buf.has_selection() else None
        rows = _layout_offsets(buf.text, font, box.w - 20)
        lh = font.get_linesize()
        pad = 6
        view_h = box.h - 2 * pad
        content_h = len(rows) * lh

        # Index of the row holding the caret, to keep it visible.
        caret_row = 0
        for idx, (rt, st) in enumerate(rows):
            if st <= buf.cursor <= st + len(rt):
                caret_row = idx
                break
        if show_caret:
            if caret_row * lh < scroll:
                scroll = caret_row * lh
            elif (caret_row + 1) * lh > scroll + view_h:
                scroll = (caret_row + 1) * lh - view_h
        scroll = max(0, min(scroll, max(0, content_h - view_h)))

        prev_clip = self.screen.get_clip()
        self.screen.set_clip(box.clip(prev_clip) if prev_clip else box)
        x = box.x + 10
        y0 = box.y + pad - scroll
        caret_drawn = False
        for idx, (rowtext, start) in enumerate(rows):
            y = y0 + idx * lh
            if y + lh < box.y or y > box.bottom:
                continue  # offscreen
            end = start + len(rowtext)
            if sel:
                s, e = sel
                rs, re = max(s, start), min(e, end)
                if re > rs:
                    pre = font.size(rowtext[:rs - start])[0]
                    wsel = font.size(rowtext[rs - start:re - start])[0]
                    extra = 6 if e > end else 0
                    pygame.draw.rect(self.screen, SELECTION, (x + pre, y, max(wsel, 2) + extra, lh))
            self.screen.blit(font.render(rowtext, True, TEXT), (x, y))
            if show_caret and not caret_drawn and start <= buf.cursor <= end:
                cx = x + font.size(rowtext[:buf.cursor - start])[0]
                pygame.draw.line(self.screen, ACCENT, (cx, y + 2), (cx, y + lh - 2), 2)
                caret_drawn = True
        self.screen.set_clip(prev_clip)

        # Scrollbar when content overflows.
        if content_h > view_h:
            track_h = box.h - 4
            thumb_h = max(20, int(track_h * view_h / content_h))
            denom = max(1, content_h - view_h)
            thumb_y = box.y + 2 + int((track_h - thumb_h) * scroll / denom)
            pygame.draw.rect(self.screen, COL_HEADER, (box.right - 6, box.y + 2, 4, track_h),
                             border_radius=2)
            pygame.draw.rect(self.screen, MUTED, (box.right - 6, thumb_y, 4, thumb_h),
                             border_radius=2)
        return scroll

    # --- in-place "soft" markdown rendering for the description editor -------
    # Every character is kept (markers shown dimmed), and all fonts share one
    # size, so the caret/selection offset math is identical to the plain path.

    def _md_style_font(self, style: str):
        if style in ("b", "h"):
            return self._md_bold
        if style == "i":
            return self._md_italic
        return self.font

    def _md_style_color(self, style: str):
        return {"marker": MUTED, "quote": MUTED, "code": CODE, "h": ACCENT}.get(style, TEXT)

    def _md_prefix_widths(self, text: str, styles: list) -> list:
        """Cumulative pixel widths so any slice width is pref[j] - pref[i]."""
        pref = [0] * (len(text) + 1)
        for k, ch in enumerate(text):
            w = 0 if ch == "\n" else self._md_style_font(styles[k]).size(ch)[0]
            pref[k + 1] = pref[k] + w
        return pref

    def _md_layout(self, text: str, pref: list, max_w: int) -> list:
        """Word-wrap like _layout_offsets, but measuring with styled widths."""
        rows: list = []
        base = 0
        for line in text.split("\n"):
            if line == "":
                rows.append(("", base))
            else:
                seg_start = 0
                last_space = -1
                i = 0
                n = len(line)
                while i < n:
                    if (pref[base + i + 1] - pref[base + seg_start]) > max_w and i > seg_start:
                        if last_space > seg_start:
                            rows.append((line[seg_start:last_space + 1], base + seg_start))
                            seg_start = last_space + 1
                        else:
                            rows.append((line[seg_start:i], base + seg_start))
                            seg_start = i
                        last_space = -1
                        i = seg_start
                        continue
                    if line[i] == " ":
                        last_space = i
                    i += 1
                rows.append((line[seg_start:], base + seg_start))
            base += len(line) + 1
        return rows or [("", 0)]

    def _md_offset_at(self, text: str, box: pygame.Rect, pos, scroll: int) -> int:
        styles = markdown.char_styles(text)
        pref = self._md_prefix_widths(text, styles)
        rows = self._md_layout(text, pref, box.w - 20)
        lh = self.font.get_linesize()
        x0, y0 = box.x + 10, box.y + 6 - scroll
        row_idx = max(0, min(len(rows) - 1, int((pos[1] - y0) // lh)))
        rowtext, start = rows[row_idx]
        relx = pos[0] - x0
        best_k, best_d = 0, float("inf")
        for k in range(len(rowtext) + 1):
            d = abs((pref[start + k] - pref[start]) - relx)
            if d < best_d:
                best_d, best_k = d, k
            else:
                break
        return start + best_k

    def draw_editable_md(self, box: pygame.Rect, buf: "EditBuffer", show_caret: bool,
                         scroll: int = 0) -> int:
        """Like draw_editable, but soft-renders the text as markdown in place."""
        text = buf.text
        styles = markdown.char_styles(text)
        pref = self._md_prefix_widths(text, styles)
        sel = buf.selection_range() if buf.has_selection() else None
        rows = self._md_layout(text, pref, box.w - 20)
        lh = self.font.get_linesize()
        pad = 6
        view_h = box.h - 2 * pad
        content_h = len(rows) * lh

        caret_row = 0
        for idx, (rt, st) in enumerate(rows):
            if st <= buf.cursor <= st + len(rt):
                caret_row = idx
                break
        if show_caret:
            if caret_row * lh < scroll:
                scroll = caret_row * lh
            elif (caret_row + 1) * lh > scroll + view_h:
                scroll = (caret_row + 1) * lh - view_h
        scroll = max(0, min(scroll, max(0, content_h - view_h)))

        prev_clip = self.screen.get_clip()
        self.screen.set_clip(box.clip(prev_clip) if prev_clip else box)
        x = box.x + 10
        y0 = box.y + pad - scroll
        caret_drawn = False
        for idx, (rowtext, start) in enumerate(rows):
            y = y0 + idx * lh
            if y + lh < box.y or y > box.bottom:
                continue
            end = start + len(rowtext)
            if sel:
                s, e = sel
                rs, re = max(s, start), min(e, end)
                if re > rs:
                    pre = pref[rs] - pref[start]
                    wsel = pref[re] - pref[rs]
                    extra = 6 if e > end else 0
                    pygame.draw.rect(self.screen, SELECTION, (x + pre, y, max(wsel, 2) + extra, lh))
            # Blit each character with its own style font/colour.
            for k in range(start, end):
                ch = text[k]
                if ch == " ":
                    continue
                font = self._md_style_font(styles[k])
                color = self._md_style_color(styles[k])
                self.screen.blit(font.render(ch, True, color), (x + (pref[k] - pref[start]), y))
            if show_caret and not caret_drawn and start <= buf.cursor <= end:
                cx = x + (pref[buf.cursor] - pref[start])
                pygame.draw.line(self.screen, ACCENT, (cx, y + 2), (cx, y + lh - 2), 2)
                caret_drawn = True
        self.screen.set_clip(prev_clip)

        if content_h > view_h:
            track_h = box.h - 4
            thumb_h = max(20, int(track_h * view_h / content_h))
            denom = max(1, content_h - view_h)
            thumb_y = box.y + 2 + int((track_h - thumb_h) * scroll / denom)
            pygame.draw.rect(self.screen, COL_HEADER, (box.right - 6, box.y + 2, 4, track_h),
                             border_radius=2)
            pygame.draw.rect(self.screen, MUTED, (box.right - 6, thumb_y, 4, thumb_h),
                             border_radius=2)
        return scroll

    def draw_text_input(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 560, 180)
        ti = self.text_input
        prompt = self.font.render(ti.prompt, True, TEXT)
        self.screen.blit(prompt, (rect.x + 24, rect.y + 28))
        box = pygame.Rect(rect.x + 24, rect.y + 72, rect.w - 48, 40)
        pygame.draw.rect(self.screen, BG, box, border_radius=6)
        pygame.draw.rect(self.screen, ACCENT, box, width=1, border_radius=6)
        blink = (pygame.time.get_ticks() // 500) % 2 == 0
        self.draw_editable(self.font, box, ti.buf, blink)
        foot = self.font_sm.render("[ENTER] confirm   ·   SHIFT+LEFT/RIGHT select   ·   [ESC] cancel", True, MUTED)
        self.screen.blit(foot, (rect.x + 24, rect.bottom - 28))

    # Editor field box heights: a single-line title and a tall (scrollable) body.
    EDITOR_FIELD_HEIGHTS = [40, 300]
    _EDITOR_MD_FIELD = 1  # the description field is soft-rendered as markdown

    def draw_editor(self, w: int, h: int) -> None:
        self.dim(w, h)
        ed = self.editor
        rect = self.panel(w, h, 620, 540)
        prompt = self.font_lg.render(ed.prompt, True, TEXT)
        self.screen.blit(prompt, (rect.x + 24, rect.y + 18))

        blink = (pygame.time.get_ticks() // 500) % 2 == 0
        y = rect.y + 70
        self._editor_boxes = []
        for i, label in enumerate(ed.labels):
            lab = self.font_sm.render(label, True, MUTED)
            self.screen.blit(lab, (rect.x + 24, y))
            box = pygame.Rect(rect.x + 24, y + 22, rect.w - 48, self.EDITOR_FIELD_HEIGHTS[i])
            self._editor_boxes.append(box)
            pygame.draw.rect(self.screen, BG, box, border_radius=6)
            border = ACCENT if i == ed.active else MUTED
            pygame.draw.rect(self.screen, border, box, width=2 if i == ed.active else 1, border_radius=6)
            caret = blink and i == ed.active
            if i == self._EDITOR_MD_FIELD:
                ed.scroll[i] = self.draw_editable_md(box, ed.bufs[i], caret, ed.scroll[i])
            else:
                ed.scroll[i] = self.draw_editable(self.font, box, ed.bufs[i], caret, ed.scroll[i])
            y += 22 + self.EDITOR_FIELD_HEIGHTS[i] + 16

        foot = self.font_sm.render("TAB switch · CTRL+Enter save · CTRL+Y view YAML · ESC", True, MUTED)
        self.screen.blit(foot, (rect.x + 24, rect.bottom - 28))

    def draw_yaml(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 640, 480)
        title = self.font_lg.render("Item YAML", True, TEXT)
        self.screen.blit(title, (rect.x + 24, rect.y + 18))
        box = pygame.Rect(rect.x + 20, rect.y + 64, rect.w - 40, rect.h - 110)
        pygame.draw.rect(self.screen, BG, box, border_radius=6)
        pygame.draw.rect(self.screen, MUTED, box, width=1, border_radius=6)
        if self._yaml_buf is not None:
            self._yaml_scroll = self.draw_editable(self.font_sm, box, self._yaml_buf,
                                                   show_caret=False, scroll=self._yaml_scroll)
        foot = self.font_sm.render("scroll to read   ·   [CTRL+Y / ESC] close", True, MUTED)
        self.screen.blit(foot, (rect.x + 24, rect.bottom - 28))

    def draw_confirm(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 480, 160)
        msg = self.font.render(self.confirm.message, True, TEXT)
        self.screen.blit(msg, (rect.x + 24, rect.y + 36))
        warn = self.font_sm.render("This cannot be undone.", True, DANGER)
        self.screen.blit(warn, (rect.x + 24, rect.y + 72))
        foot = self.font.render("[Y / ENTER] yes      [N / ESC] no", True, MUTED)
        self.screen.blit(foot, (rect.x + 24, rect.bottom - 40))

    def draw_keybindings(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 520, 520)
        title = self.font_lg.render("Edit Keybindings", True, TEXT)
        self.screen.blit(title, (rect.x + 24, rect.y + 18))
        sub = self.font_sm.render("UP/DOWN select · ENTER rebind · scroll · ESC save+close", True, MUTED)
        self.screen.blit(sub, (rect.x + 24, rect.y + 54))

        GROUP_H = 28   # height of a group-header row
        ROW_H   = 36   # height of an action row

        # Compute total content height so we can clamp scroll.
        total_h = sum(GROUP_H + len(acts) * ROW_H for _, acts in self.KB_GROUPS)
        body = pygame.Rect(rect.x + 12, rect.y + 82, rect.w - 24, rect.h - 112)
        view_h = body.h
        self.kb_scroll = max(0, min(self.kb_scroll, max(0, total_h - view_h)))

        # Keep the selected action in view.
        sel_y = 0
        action_pos = 0
        for grp_label, acts in self.KB_GROUPS:
            sel_y_g = sel_y + GROUP_H
            for a in acts:
                if action_pos == self.kb_index:
                    if sel_y_g < self.kb_scroll:
                        self.kb_scroll = sel_y_g
                    elif sel_y_g + ROW_H > self.kb_scroll + view_h:
                        self.kb_scroll = sel_y_g + ROW_H - view_h
                sel_y_g += ROW_H
                action_pos += 1
            sel_y += GROUP_H + len(acts) * ROW_H

        prev_clip = self.screen.get_clip()
        self.screen.set_clip(body.clip(prev_clip) if prev_clip else body)

        y = body.y - self.kb_scroll
        action_pos = 0
        for grp_label, acts in self.KB_GROUPS:
            # Group header.
            if body.y <= y + GROUP_H and y < body.bottom:
                grp_surf = self.font_sm.render(grp_label.upper(), True, MUTED)
                self.screen.blit(grp_surf, (rect.x + 24, y + 8))
                pygame.draw.line(self.screen, MUTED,
                                 (rect.x + 24 + grp_surf.get_width() + 8, y + GROUP_H // 2),
                                 (rect.right - 24, y + GROUP_H // 2))
            y += GROUP_H
            for action in acts:
                if body.y <= y + ROW_H and y < body.bottom:
                    selected = action_pos == self.kb_index
                    if selected:
                        hl = pygame.Rect(rect.x + 12, y - 2, rect.w - 24, ROW_H - 4)
                        pygame.draw.rect(self.screen, CARD_BG_SEL, hl, border_radius=6)
                    name_surf = self.font.render(action, True, TEXT)
                    self.screen.blit(name_surf, (rect.x + 24, y + (ROW_H - self.font.get_linesize()) // 2))
                    if selected and self.kb_capturing:
                        val = self.font.render("press a key…", True, ACCENT)
                    else:
                        val = self.font.render(self.keys.mapping.get(action, ""), True, BADGE)
                    self.screen.blit(val, (rect.right - val.get_width() - 24,
                                          y + (ROW_H - self.font.get_linesize()) // 2))
                y += ROW_H
                action_pos += 1

        self.screen.set_clip(prev_clip)

        # Scrollbar.
        if total_h > view_h:
            track_h = body.h - 4
            thumb_h = max(20, int(track_h * view_h / total_h))
            thumb_y = body.y + 2 + int((track_h - thumb_h) * self.kb_scroll / max(1, total_h - view_h))
            pygame.draw.rect(self.screen, MUTED, (rect.right - 10, thumb_y, 4, thumb_h), border_radius=2)

    # --- person-fields editor (creator / assignee / reporter) ---------------

    def _author_emails(self) -> list[str]:
        """Sorted list of email keys from board.authors."""
        return sorted(self.board.authors.keys())

    def _handle_for(self, email: str) -> str:
        """Display handle for an author email (github handle > name > email)."""
        rec = self.board.authors.get(email, {})
        return rec.get("github") or rec.get("name") or email

    def start_relationships(self) -> None:
        if not self.selected():
            return
        if self._guard_locked():
            return
        self.rel_index = 0
        self.mode = self.RELATIONSHIPS

    def handle_relationships(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if self.keys.matches("cancel", event.key) or self.keys.matches("relationships", event.key):
            self.mode = self.NORMAL
            return
        from OLD.tododo.board import PERSON_FIELDS
        item = self.selected()
        if not item:
            return
        if event.key == pygame.K_UP:
            self.rel_index = (self.rel_index - 1) % len(PERSON_FIELDS)
        elif event.key == pygame.K_DOWN:
            self.rel_index = (self.rel_index + 1) % len(PERSON_FIELDS)
        elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
            self._cycle_person_field(item, PERSON_FIELDS[self.rel_index],
                                     1 if event.key == pygame.K_RIGHT else -1)

    def _cycle_person_field(self, item: Item, field: str, delta: int) -> None:
        options = [""] + self._author_emails()
        cur = getattr(item, field, "")
        idx = options.index(cur) if cur in options else 0
        nxt = options[(idx + delta) % len(options)]
        setattr(item, field, nxt)
        self.persist(f"set {field} on '{item.title}'")

    def draw_relationships(self, w: int, h: int) -> None:
        from OLD.tododo.board import PERSON_FIELDS
        self.dim(w, h)
        rect = self.panel(w, h, 520, 300)
        item = self.selected()
        title = self.font_lg.render("People", True, TEXT)
        self.screen.blit(title, (rect.x + 24, rect.y + 18))
        sub = self.font_sm.render("UP/DOWN select · LEFT/RIGHT assign · ESC close", True, MUTED)
        self.screen.blit(sub, (rect.x + 24, rect.y + 54))
        y = rect.y + 90
        if not self.board.authors:
            note = self.font_sm.render("no authors yet — create an item first", True, MUTED)
            self.screen.blit(note, (rect.x + 24, y))
            y += 30
        for i, field in enumerate(PERSON_FIELDS):
            selected = i == self.rel_index
            if selected:
                hl = pygame.Rect(rect.x + 12, y - 4, rect.w - 24, 32)
                pygame.draw.rect(self.screen, CARD_BG_SEL, hl, border_radius=6)
            label = self.font.render(field, True, TEXT)
            self.screen.blit(label, (rect.x + 24, y))
            email = getattr(item, field, "") if item else ""
            display = f"@{self._handle_for(email)}" if email else "—"
            val = self.font.render(display, True, ACCENT if email else BADGE)
            self.screen.blit(val, (rect.right - val.get_width() - 24, y))
            y += 36

    # --- history modal ------------------------------------------------------

    def open_history(self, item: Item) -> None:
        self._history_item_id = item.id
        self._history_scroll = 0
        cached = self._history_cache.get(item.id)
        if not cached:
            self._history_cache[item.id] = None  # mark as loading
            self._ensure_history_loaded(item.id)
        self.mode = self.VIEW_HISTORY

    def handle_history(self, event) -> None:
        if event.type == pygame.MOUSEWHEEL:
            ROW_H = self.font_sm.get_linesize() + 8
            self._history_scroll = max(0, self._history_scroll - event.y * ROW_H)
            return
        if event.type != pygame.KEYDOWN:
            return
        if (self.keys.matches("cancel", event.key)
                or self.keys.matches("view_history", event.key)):
            self.mode = self.NORMAL
            return
        ROW_H = self.font_sm.get_linesize() + 8
        if event.key == pygame.K_UP:
            self._history_scroll = max(0, self._history_scroll - ROW_H)
        elif event.key == pygame.K_DOWN:
            self._history_scroll += ROW_H

    def draw_history(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 680, 520)
        item = self.board.find(self._history_item_id) if self._history_item_id else None
        title_text = f"History: {item.title[:40]}" if item else "History"
        self.screen.blit(self.font_lg.render(title_text, True, TEXT),
                         (rect.x + 24, rect.y + 18))
        self.screen.blit(
            self.font_sm.render("UP/DOWN · scroll · ESC close", True, MUTED),
            (rect.x + 24, rect.y + 54))

        body = pygame.Rect(rect.x + 20, rect.y + 82, rect.w - 40, rect.h - 120)
        events = self._history_cache.get(self._history_item_id) if self._history_item_id else []

        if events is None:
            self.screen.blit(self.font.render("Loading history…", True, MUTED),
                             (body.x + 12, body.y + 12))
        elif not events:
            self.screen.blit(self.font.render("No git history found.", True, MUTED),
                             (body.x + 12, body.y + 12))
        else:
            ROW_H = self.font_sm.get_linesize() + 8
            content_h = len(events) * ROW_H
            max_scroll = max(0, content_h - body.h)
            self._history_scroll = min(self._history_scroll, max_scroll)

            prev_clip = self.screen.get_clip()
            self.screen.set_clip(body)
            fmt = self.settings.timestamp_format()
            y = body.y - self._history_scroll
            for evt in events:
                if y + ROW_H >= body.y and y < body.bottom:
                    if evt.action == "created":
                        color, label = BADGE, "Created"
                    elif evt.action == "deleted":
                        color, label = DANGER, "Deleted"
                    elif evt.action == "column":
                        color, label = ACCENT, f"Moved → '{evt.new_value}'"
                    else:
                        old = str(evt.old_value or "")[:20]
                        new = str(evt.new_value or "")[:20]
                        color, label = TEXT, f"{evt.action}: '{old}' → '{new}'"
                    ts_s = self.font_sm.render(timefmt.ago(evt.timestamp, fmt), True, MUTED)
                    act_s = self.font_sm.render(label, True, color)
                    by_s = self.font_sm.render(
                        f"@{evt.author_name or evt.author_email}", True, MUTED)
                    self.screen.blit(ts_s, (body.x, y + 4))
                    self.screen.blit(act_s, (body.x + 210, y + 4))
                    self.screen.blit(by_s, (body.right - by_s.get_width(), y + 4))
                y += ROW_H
            self.screen.set_clip(prev_clip)

            if content_h > body.h:
                track_h = body.h - 4
                thumb_h = max(20, int(track_h * body.h / content_h))
                scroll_ratio = self._history_scroll / max(1, max_scroll)
                thumb_y = body.y + 2 + int((track_h - thumb_h) * scroll_ratio)
                pygame.draw.rect(self.screen, MUTED,
                                 (rect.right - 10, thumb_y, 4, thumb_h), border_radius=2)

        count = len(events) if events else 0
        foot = self.font_sm.render(
            f"{count} events   ·   [ESC] close", True, MUTED)
        self.screen.blit(foot, (rect.x + 24, rect.bottom - 28))

    def draw_switch_board(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 560, 480)
        title = self.font_lg.render("Switch board", True, TEXT)
        self.screen.blit(title, (rect.x + 24, rect.y + 18))
        sub = self.font_sm.render("type to search · UP/DOWN select · ENTER open · ESC close", True, MUTED)
        self.screen.blit(sub, (rect.x + 24, rect.y + 54))

        # Search box (caret blinks only while the search box has focus).
        box = pygame.Rect(rect.x + 24, rect.y + 84, rect.w - 48, 38)
        pygame.draw.rect(self.screen, BG, box, border_radius=6)
        focus_search = self.sb_focus == "search"
        pygame.draw.rect(self.screen, ACCENT if focus_search else MUTED, box,
                         width=2 if focus_search else 1, border_radius=6)
        blink = focus_search and (pygame.time.get_ticks() // 500) % 2 == 0
        if self.sb_query is not None:
            self.draw_editable(self.font, box, self.sb_query, blink)
        if self.sb_query is None or not self.sb_query.text:
            ph = self.font.render("Search boards…", True, MUTED)
            self.screen.blit(ph, (box.x + 12, box.y + box.h // 2 - ph.get_height() // 2))

        # Board list.
        list_top = box.bottom + 14
        boards = self.sb_boards
        if not boards:
            msg = "no other boards — CTRL+B creates one"
            self.screen.blit(self.font.render(msg, True, MUTED), (rect.x + 24, list_top + 6))
        y = list_top
        row_h = 38
        for i, name in enumerate(boards):
            if y + row_h > rect.bottom - 40:
                break  # don't overflow the panel
            selected = i == self.sb_index
            if selected:
                hl = pygame.Rect(rect.x + 12, y, rect.w - 24, row_h - 4)
                pygame.draw.rect(self.screen, CARD_BG_SEL, hl, border_radius=6)
                pygame.draw.rect(self.screen, ACCENT, hl, width=1, border_radius=6)
            label = self.font.render(name, True, ACCENT if selected else TEXT)
            self.screen.blit(label, (rect.x + 24, y + (row_h - 4) // 2 - label.get_height() // 2))
            opened = self.workspace.opened(name)
            when = ("opened " + timefmt.ago(opened, self.settings.timestamp_format())) if opened else "never opened"
            ws = self.font_sm.render(when, True, MUTED)
            self.screen.blit(ws, (rect.right - ws.get_width() - 24,
                                  y + (row_h - 4) // 2 - ws.get_height() // 2))
            y += row_h

    def _theme_colors(self, name: str) -> dict:
        if name not in self._theme_cache:
            self._theme_cache[name] = theme.load(theme.THEMES_DIR / f"{name}.yaml")
        return self._theme_cache[name]

    def draw_themes(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 560, 480)
        title = self.font_lg.render("Colour theme", True, TEXT)
        self.screen.blit(title, (rect.x + 24, rect.y + 18))
        sub = self.font_sm.render("type to search · UP/DOWN select · ENTER apply · ESC close", True, MUTED)
        self.screen.blit(sub, (rect.x + 24, rect.y + 54))

        box = pygame.Rect(rect.x + 24, rect.y + 84, rect.w - 48, 38)
        pygame.draw.rect(self.screen, BG, box, border_radius=6)
        focus_search = self.th_focus == "search"
        pygame.draw.rect(self.screen, ACCENT if focus_search else MUTED, box,
                         width=2 if focus_search else 1, border_radius=6)
        blink = focus_search and (pygame.time.get_ticks() // 500) % 2 == 0
        if self.th_query is not None:
            self.draw_editable(self.font, box, self.th_query, blink)
        if self.th_query is None or not self.th_query.text:
            ph = self.font.render("Search themes…", True, MUTED)
            self.screen.blit(ph, (box.x + 12, box.y + box.h // 2 - ph.get_height() // 2))

        names = self.th_themes
        if not names:
            self.screen.blit(self.font.render("no themes found", True, MUTED),
                             (rect.x + 24, box.bottom + 20))
        y = box.bottom + 14
        row_h = 40
        for i, name in enumerate(names):
            if y + row_h > rect.bottom - 40:
                break
            selected = i == self.th_index
            if selected:
                hl = pygame.Rect(rect.x + 12, y, rect.w - 24, row_h - 4)
                pygame.draw.rect(self.screen, CARD_BG_SEL, hl, border_radius=6)
                pygame.draw.rect(self.screen, ACCENT, hl, width=1, border_radius=6)
            label = self.font.render(name, True, ACCENT if selected else TEXT)
            self.screen.blit(label, (rect.x + 24, y + (row_h - 4) // 2 - label.get_height() // 2))
            # Colour swatches previewing the theme's palette.
            colors = self._theme_colors(name)
            swatches = [colors["accent"], colors["badge"], colors["card_bg"],
                        colors["bg"], colors["danger"]]
            sx = rect.right - 24 - len(swatches) * 22
            for c in swatches:
                pygame.draw.rect(self.screen, c[:3], (sx, y + (row_h - 4) // 2 - 8, 16, 16),
                                 border_radius=3)
                pygame.draw.rect(self.screen, MUTED, (sx, y + (row_h - 4) // 2 - 8, 16, 16),
                                 width=1, border_radius=3)
                sx += 22
            y += row_h

    def draw_toast(self, w: int, h: int) -> None:
        if not self.toast or pygame.time.get_ticks() > self.toast_until:
            return
        surf = self.font.render(self.toast, True, TEXT)
        pad = 12
        rect = pygame.Rect(0, 0, surf.get_width() + 2 * pad, surf.get_height() + 2 * pad)
        rect.centerx = w // 2
        rect.y = h - STATUSBAR_H - rect.h - 16
        pygame.draw.rect(self.screen, ACCENT, rect, border_radius=8)
        self.screen.blit(surf, (rect.x + pad, rect.y + pad))
