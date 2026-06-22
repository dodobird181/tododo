"""Pygame UI for the kanban board."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pygame

from . import avatars, clipboard, markdown, timefmt
from .board import Board, Item
from .keybindings import ACTIONS, Keybindings, code_to_key_name
from .keybindings import USER_PATH as KB_USER_PATH
from .gitsync import GitSync
from .settings import Settings
from .settings import USER_PATH as SETTINGS_USER_PATH

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

COLUMN_COLORS = [(110, 168, 254), (240, 196, 110), (96, 200, 160), (200, 130, 240), (240, 140, 170)]


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

    def __init__(self, board: Board, keys: Keybindings, git: GitSync, settings: "Settings"):
        pygame.init()
        pygame.display.set_caption("tododo — kanban on YAML")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
        # Held keys (backspace, arrows, ctrl+arrow moves) repeat continuously.
        pygame.key.set_repeat(300, 40)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, 24)
        self.font_sm = pygame.font.Font(None, 20)
        self.font_lg = pygame.font.Font(None, 34)
        # Markdown fonts for rendering item descriptions (Obsidian-style).
        self.md = markdown.MarkdownFonts(20, TEXT, MUTED, CODE, ACCENT)
        # Background fetcher for real avatar images (Gravatar by email).
        self.avatars = avatars.AvatarStore()

        self.board = board
        self.keys = keys
        self.git = git
        self.settings = settings

        self.mode = self.NORMAL
        self.selected_id: str | None = None
        # Multi-selection (shift+up/down): set of item ids for collective ops,
        # with sel_anchor_id marking where the range selection started.
        self.multi: set[str] = set()
        self.sel_anchor_id: str | None = None

        # Column display state (session only): per-column scroll + minimized set.
        self.col_scroll: dict[str, int] = {}
        self.minimized: set[str] = set()
        self._col_toggle_rects: dict[str, pygame.Rect] = {}
        self.running = True

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
        # Avatar circles from the last render, for hover tooltips: (rect, label).
        self._avatar_hits: list[tuple[pygame.Rect, str]] = []

        # keybindings editor state
        self.kb_index = 0
        self.kb_capturing = False

        # cached card rects from last render, for hit-testing
        self.card_rects: list[CardRect] = []
        self.column_rects: dict[str, pygame.Rect] = {}

        self.toast = ""
        self.toast_until = 0

        # Hot-reload: watch the source files for external edits (mtime polling).
        self._watch = {
            "board": self.board.path,
            "settings": SETTINGS_USER_PATH,
            "keybindings": KB_USER_PATH,
        }
        self._watch_mtime = {k: self._mtime(p) for k, p in self._watch.items()}
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
        """Save board to disk and queue a git push."""
        self.board.save()
        # Record our own write so the file watcher doesn't treat it as external.
        self._watch_mtime["board"] = self._mtime(self.board.path)
        self.git.push_change(message)

    def selected(self) -> Item | None:
        return self.board.find(self.selected_id) if self.selected_id else None

    # --- main loop -------------------------------------------------------

    def run(self) -> None:
        while self.running:
            for event in pygame.event.get():
                self.handle_event(event)
            # External pulls may have changed the file on disk.
            if self.git.board_changed.is_set():
                self.git.board_changed.clear()
                self.reload_board()
            self.check_file_reloads()
            self.render()
            self.clock.tick(60)
        self.git.stop()
        pygame.quit()

    def reload_board(self) -> None:
        self.board = Board.load(self.board.path)
        if self.selected_id and not self.board.find(self.selected_id):
            self.selected_id = None
        # Drop any multi-selected ids that no longer exist.
        self.multi = {i for i in self.multi if self.board.find(i)}
        self._watch_mtime["board"] = self._mtime(self.board.path)
        self.notify("board reloaded")

    def check_file_reloads(self) -> None:
        """Hot-reload board / settings / keybindings when edited on disk."""
        now = pygame.time.get_ticks()
        if now - self._last_watch < 1000:  # poll at most once a second
            return
        self._last_watch = now
        for key, path in self._watch.items():
            m = self._mtime(path)
            if m and m != self._watch_mtime[key]:
                self._watch_mtime[key] = m
                self._reload_file(key)

    def _reload_file(self, key: str) -> None:
        if key == "board":
            self.reload_board()
        elif key == "settings":
            self.settings = Settings.load()
            # Push live-tunable values to the running git sync.
            self.git.merge_option = self.settings.merge_option()
            self.git.push_interval = self.settings.push_interval()
            self.git.poll_interval = self.settings.poll_interval()
            self.git.poll_backoff_max = self.settings.poll_backoff_max()
            self.notify("settings reloaded")
        elif key == "keybindings":
            self.keys = Keybindings.load()
            self.notify("keybindings reloaded")

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
            if self.editor and self.editor.handle(event):
                self.editor = None
                self.mode = self.NORMAL
            return
        if self.mode == self.CONFIRM:
            self.handle_confirm(event)
            return
        if self.mode == self.KEYBINDINGS:
            self.handle_keybindings(event)
            return
        if self.mode == self.PALETTE:
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
                # CTRL+<open_palette> opens the palette; CTRL+<action> runs it directly.
                if self.keys.matches("open_palette", event.key):
                    self.mode = self.PALETTE
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
                self.minimized.discard(name) if name in self.minimized else self.minimized.add(name)
                return
        item = self.card_at(pos)
        self.selected_id = item.id if item else None
        self._clear_multi()  # a click is a single selection
        self.mouse_down_pos = pos
        self.drag_item = None
        self.drag_candidate = None
        if item:
            for cr in self.card_rects:
                if cr.item.id == item.id:
                    self.drag_candidate = item
                    self.drag_offset = (pos[0] - cr.rect.x, pos[1] - cr.rect.y)
                    self.drag_pos = pos
                    break

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

    def selection(self) -> list[Item]:
        """Items targeted by collective actions (multi-selection, else the focus)."""
        if self.multi:
            return [it for it in self.board.items if it.id in self.multi]
        item = self.selected()
        return [item] if item else []

    def is_selected(self, item: Item) -> bool:
        return item.id == self.selected_id or item.id in self.multi

    # --- keyboard navigation --------------------------------------------

    def navigate(self, key: int, shift: bool = False) -> None:
        cols = self.board.columns
        if not cols:
            return
        item = self.selected()
        # No selection yet: land on the first item we can find.
        if not item:
            for c in cols:
                col_items = self.board.items_in(c)
                if col_items:
                    self.selected_id = col_items[0].id
                    self._clear_multi()
                    return
            return

        col_idx = cols.index(item.column)
        col_items = self.board.items_in(item.column)
        row = col_items.index(item)

        if key in (pygame.K_UP, pygame.K_DOWN):
            step = -1 if key == pygame.K_UP else 1
            if shift:
                # Extend a selection within the column (clamp, don't wrap).
                if self.sel_anchor_id is None:
                    self.sel_anchor_id = item.id
                new_row = max(0, min(len(col_items) - 1, row + step))
                self.selected_id = col_items[new_row].id
                self._select_range(item.column)
            else:
                new_row = (row + step) % len(col_items)  # cycle within column
                self.selected_id = col_items[new_row].id
                self._clear_multi()
        else:  # left / right
            self._clear_multi()
            step = -1 if key == pygame.K_LEFT else 1
            # Cycle across columns, skipping empty ones; keep a similar row.
            for offset in range(1, len(cols) + 1):
                target = cols[(col_idx + step * offset) % len(cols)]
                target_items = self.board.items_in(target)
                if target_items:
                    new_row = min(row, len(target_items) - 1)
                    self.selected_id = target_items[new_row].id
                    return

    # --- palette / actions ----------------------------------------------

    # Actions triggerable via the palette or CTRL+<key>.
    COMMAND_ACTIONS = [
        "create", "delete", "move_left", "move_right",
        "move_up", "move_down", "point", "keybindings",
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

    def handle_palette(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if self.keys.matches("cancel", event.key) or self.keys.matches("open_palette", event.key):
            self.mode = self.NORMAL
            return
        action = self.action_for_key(event.key)
        if action:
            self.perform_action(action)

    def start_create(self) -> None:
        col = self.selected().column if self.selected() else self.board.columns[0]

        name, email = self.git.identity()

        def submit(title: str, description: str) -> None:
            title = title.strip()
            if title:
                item = self.board.create(title, column=col, description=description.strip(),
                                         author=name, author_email=email)
                self.selected_id = item.id
                self.persist(f"create '{title}'")
        self.editor = ItemEditor(f"New item in '{col}'", submit)
        self.mode = self.EDIT

    def start_edit(self) -> None:
        item = self.selected()
        if not item:
            return

        def submit(title: str, description: str) -> None:
            title = title.strip()
            if title:
                item.title = title
                item.description = description.strip()
                item.touch()
                self.persist(f"edit '{title}'")
        self.editor = ItemEditor("Edit item", submit, title=item.title, description=item.description)
        self.mode = self.EDIT

    def start_delete(self) -> None:
        sel = self.selection()
        if not sel:
            self.notify("no item selected")
            self.mode = self.NORMAL
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
        item = self.selected()
        if not item:
            self.notify("no item selected")
            self.mode = self.NORMAL
            return

        def submit(text: str) -> None:
            try:
                item.points = int(text or 0)
            except ValueError:
                item.points = 0
            item.touch()
            self.persist(f"point '{item.title}' = {item.points}")
        self.text_input = TextInput(f"Points for '{item.title}':", submit,
                                    initial=str(item.points), numeric=True)
        self.mode = self.TEXT

    def do_move(self, delta: int) -> None:
        sel = self.selection()
        if not sel:
            self.notify("no item selected")
            self.mode = self.NORMAL
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

    def handle_keybindings(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if self.kb_capturing:
            # Capture the next key as the new binding for the selected action.
            action = ACTIONS[self.kb_index]
            self.keys.mapping[action] = code_to_key_name(event.key)
            self.kb_capturing = False
            self.keys.save()
            self._watch_mtime["keybindings"] = self._mtime(KB_USER_PATH)
            self.notify(f"{action} -> {code_to_key_name(event.key)}")
            return
        if self.keys.matches("cancel", event.key):
            self.keys.save()
            self._watch_mtime["keybindings"] = self._mtime(KB_USER_PATH)
            self.mode = self.PALETTE
        elif event.key == pygame.K_UP:
            self.kb_index = (self.kb_index - 1) % len(ACTIONS)
        elif event.key == pygame.K_DOWN:
            self.kb_index = (self.kb_index + 1) % len(ACTIONS)
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

        self.draw_toast(w, h)
        pygame.display.flip()

    def draw_topbar(self, w: int) -> None:
        pygame.draw.rect(self.screen, COL_HEADER, (0, 0, w, TOPBAR_H))
        title = self.font_lg.render("tododo", True, TEXT)
        self.screen.blit(title, (16, TOPBAR_H // 2 - title.get_height() // 2))
        total = sum(it.points for it in self.board.items)
        palette_key = self.keys.label("open_palette") or "SPACE"
        hint = self.font_sm.render(
            f"{len(self.board.items)} items · {total} pts   ·   "
            f"CTRL+{palette_key}: commands   ·   arrows: navigate   ·   drag to move",
            True, MUTED)
        self.screen.blit(hint, (160, TOPBAR_H // 2 - hint.get_height() // 2))

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

        n_min = sum(1 for c in cols if c in self.minimized)
        n_norm = max(1, len(cols) - n_min)
        total_gap = COL_GAP * (len(cols) + 1)
        norm_w = (w - total_gap - n_min * self.MIN_COL_W) / n_norm

        x = COL_GAP
        for i, col in enumerate(cols):
            minimized = col in self.minimized
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
        self._col_toggle_rects[name] = header  # click header to minimize

        items = self.board.items_in(name)
        pts = sum(it.points for it in items)
        label = self.font.render(name, True, TEXT)
        self.screen.blit(label, (rect.x + 16, rect.y + HEADER_H // 2 - label.get_height() // 2))
        meta = self.font_sm.render(f"{pts} pts  —", True, MUTED)
        self.screen.blit(meta, (rect.right - meta.get_width() - 12,
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
        if not item.updated:
            return False
        if self.settings.timestamps_always:
            return True
        return item.id == self.selected_id

    def draw_card(self, item: Item, x: int, y: int, w: int, floating: bool = False) -> pygame.Rect:
        show_av = self.settings.git_avatars
        av_d, av_gap = 24, 8
        left = CARD_PAD + (av_d + av_gap if show_av else 0)
        content_w = w - left - CARD_PAD
        text_x = x + left
        lines = self.wrap_text(item.title, self.font, content_w - 40)
        text_h = len(lines) * self.font.get_linesize()

        md_rows = None
        desc_h = 0
        if self.show_description(item, floating):
            try:
                md_rows, desc_h = markdown.flow(item.description, self.md, content_w)
            except Exception:
                md_rows, desc_h = None, 0
            if md_rows:
                desc_h += 6  # gap between title and description

        ts_text = ""
        ts_h = 0
        if self.show_timestamp(item, floating):
            ts_text = timefmt.format_timestamp(item.updated, self.settings.timestamp_format())
            if ts_text:
                ts_h = self.font_sm.get_linesize() + 6

        card_h = max(CARD_MIN_H, text_h + desc_h + ts_h + 2 * CARD_PAD)
        rect = pygame.Rect(x, y, w, card_h)
        if floating:
            color = CARD_BG_DRAG
        elif self.is_selected(item):
            color = CARD_BG_SEL
        else:
            color = CARD_BG
        pygame.draw.rect(self.screen, color, rect, border_radius=8)
        if not floating and item.id == self.selected_id:
            pygame.draw.rect(self.screen, ACCENT, rect, width=2, border_radius=8)
        elif not floating and item.id in self.multi:
            pygame.draw.rect(self.screen, ACCENT, rect, width=1, border_radius=8)

        if show_av:
            self._draw_avatar(item, x, y, av_d, floating)

        ty = y + CARD_PAD
        for line in lines:
            surf = self.font.render(line, True, TEXT)
            self.screen.blit(surf, (text_x, ty))
            ty += self.font.get_linesize()

        if md_rows:
            ty += 6
            ty = markdown.draw(self.screen, text_x, ty, md_rows)

        if ts_text:
            ty += 6
            self.screen.blit(self.font_sm.render(ts_text, True, MUTED), (text_x, ty))

        # points badge
        if item.points:
            badge = self.font_sm.render(str(item.points), True, (20, 24, 30))
            bw = badge.get_width() + 14
            brect = pygame.Rect(rect.right - bw - CARD_PAD, y + CARD_PAD, bw, 22)
            pygame.draw.rect(self.screen, BADGE, brect, border_radius=11)
            self.screen.blit(badge, (brect.x + 7, brect.y + 11 - badge.get_height() // 2))
        return rect

    def _draw_avatar(self, item: Item, x: int, y: int, av_d: int, floating: bool) -> None:
        local_name, local_email = self.git.identity()
        name = item.author or local_name
        email = item.author_email or local_email
        r = av_d // 2
        cx, cy = x + CARD_PAD + r, y + CARD_PAD + r
        img = self.avatars.get(email, av_d) if (self.settings.avatar_images and email) else None
        if img:
            self.screen.blit(img, (cx - r, cy - r))
        else:
            seed = email or name or item.id
            pygame.draw.circle(self.screen, _avatar_color(seed), (cx, cy), r)
            initials = self.font_sm.render(_avatar_initials(name, email), True, (18, 22, 28))
            self.screen.blit(initials, (cx - initials.get_width() // 2, cy - initials.get_height() // 2))
        if not floating:
            label = f"{name} <{email}>" if email else (name or "unknown")
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
        with self.git._lock:
            status = self.git.status
        line = f"{status}   ·   last fetch: {self.git.last_fetch_label()}"
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
        rect = self.panel(w, h, 480, 420)
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
            ("keybindings", "Edit keybindings"),
        ]
        y = rect.y + 84
        for action, desc in rows:
            keylabel = self.keys.label(action)
            key_surf = self.font.render(f"[{keylabel}]", True, ACCENT)
            self.screen.blit(key_surf, (rect.x + 24, y))
            desc_surf = self.font.render(desc, True, TEXT)
            self.screen.blit(desc_surf, (rect.x + 130, y))
            y += 34
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
        foot = self.font_sm.render("[ENTER] confirm   ·   SHIFT+←→ select   ·   [ESC] cancel", True, MUTED)
        self.screen.blit(foot, (rect.x + 24, rect.bottom - 28))

    # Editor field box heights: a single-line title and a tall (scrollable) body.
    EDITOR_FIELD_HEIGHTS = [40, 300]

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
            ed.scroll[i] = self.draw_editable(self.font, box, ed.bufs[i],
                                              blink and i == ed.active, ed.scroll[i])
            y += 22 + self.EDITOR_FIELD_HEIGHTS[i] + 16

        foot = self.font_sm.render("TAB switch · CTRL+Enter save · ESC", True, MUTED)
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
        title = self.font_lg.render("Keybindings", True, TEXT)
        self.screen.blit(title, (rect.x + 24, rect.y + 18))
        sub = self.font_sm.render("↑/↓ select · ENTER rebind · ESC save+close", True, MUTED)
        self.screen.blit(sub, (rect.x + 24, rect.y + 54))
        y = rect.y + 90
        for i, action in enumerate(ACTIONS):
            selected = i == self.kb_index
            if selected:
                hl = pygame.Rect(rect.x + 12, y - 4, rect.w - 24, 32)
                pygame.draw.rect(self.screen, CARD_BG_SEL, hl, border_radius=6)
            name = self.font.render(action, True, TEXT)
            self.screen.blit(name, (rect.x + 24, y))
            if selected and self.kb_capturing:
                val = self.font.render("press a key…", True, ACCENT)
            else:
                val = self.font.render(self.keys.mapping.get(action, ""), True, BADGE)
            self.screen.blit(val, (rect.right - val.get_width() - 24, y))
            y += 36

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
