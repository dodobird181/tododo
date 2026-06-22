"""Pygame UI for the kanban board."""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from .board import Board, Item
from .keybindings import ACTIONS, Keybindings, code_to_key_name
from .gitsync import GitSync
from .settings import Settings

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

COLUMN_COLORS = [(110, 168, 254), (240, 196, 110), (96, 200, 160), (200, 130, 240), (240, 140, 170)]


@dataclass
class CardRect:
    item: Item
    rect: pygame.Rect


class TextInput:
    """Minimal single-line text input modal state."""

    def __init__(self, prompt: str, on_submit, initial: str = "", numeric: bool = False):
        self.prompt = prompt
        self.text = initial
        self.on_submit = on_submit
        self.numeric = numeric

    def handle(self, event) -> bool:
        """Return True when the modal should close."""
        if event.type != pygame.KEYDOWN:
            return False
        if event.key == pygame.K_ESCAPE:
            return True
        if event.key == pygame.K_RETURN:
            self.on_submit(self.text)
            return True
        if event.key == pygame.K_BACKSPACE:
            self.text = self.text[:-1]
            return False
        ch = event.unicode
        if ch and ch.isprintable():
            if self.numeric and not ch.isdigit():
                return False
            self.text += ch
        return False


class ItemEditor:
    """Two-field modal: title + (multi-line) description.

    TAB / Shift+TAB or ↑/↓ switch fields. ENTER in the title field submits;
    in the description field ENTER inserts a newline (CTRL+ENTER submits).
    ESC cancels.
    """

    def __init__(self, prompt: str, on_submit, title: str = "", description: str = ""):
        self.prompt = prompt
        self.on_submit = on_submit
        self.fields = [title, description]
        self.labels = ["Title", "Description"]
        self.active = 0

    def handle(self, event) -> bool:
        """Return True when the modal should close."""
        if event.type != pygame.KEYDOWN:
            return False
        mods = event.mod
        if event.key == pygame.K_ESCAPE:
            return True
        if event.key == pygame.K_TAB:
            step = -1 if (mods & pygame.KMOD_SHIFT) else 1
            self.active = (self.active + step) % len(self.fields)
            return False
        if event.key in (pygame.K_UP, pygame.K_DOWN):
            self.active = (self.active + (1 if event.key == pygame.K_DOWN else -1)) % len(self.fields)
            return False
        if event.key == pygame.K_RETURN:
            # Title field submits; description field inserts a newline unless CTRL.
            if self.active == 1 and not (mods & pygame.KMOD_CTRL):
                self.fields[1] += "\n"
                return False
            self.on_submit(self.fields[0], self.fields[1])
            return True
        if event.key == pygame.K_BACKSPACE:
            self.fields[self.active] = self.fields[self.active][:-1]
            return False
        ch = event.unicode
        if ch and ch.isprintable():
            self.fields[self.active] += ch
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

        self.board = board
        self.keys = keys
        self.git = git
        self.settings = settings

        self.mode = self.NORMAL
        self.selected_id: str | None = None
        self.running = True

        # drag state
        self.drag_item: Item | None = None
        self.drag_offset = (0, 0)
        self.drag_pos = (0, 0)
        self.mouse_down_pos = None

        # modal state
        self.text_input: TextInput | None = None
        self.editor: ItemEditor | None = None
        self.confirm: Confirm | None = None

        # keybindings editor state
        self.kb_index = 0
        self.kb_capturing = False

        # cached card rects from last render, for hit-testing
        self.card_rects: list[CardRect] = []
        self.column_rects: dict[str, pygame.Rect] = {}

        self.toast = ""
        self.toast_until = 0

    # --- helpers ---------------------------------------------------------

    def notify(self, text: str, ms: int = 2000) -> None:
        self.toast = text
        self.toast_until = pygame.time.get_ticks() + ms

    def persist(self, message: str) -> None:
        """Save board to disk and queue a git push."""
        self.board.save()
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
            self.render()
            self.clock.tick(60)
        self.git.stop()
        pygame.quit()

    def reload_board(self) -> None:
        self.board = Board.load(self.board.path)
        if self.selected_id and not self.board.find(self.selected_id):
            self.selected_id = None
        self.notify("reloaded from git")

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
                self.navigate(event.key)
                return
            if event.key == pygame.K_RETURN and self.selected():
                self.start_edit()
                return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.on_mouse_down(event.pos)
        elif event.type == pygame.MOUSEMOTION and self.drag_item:
            self.drag_pos = event.pos
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.on_mouse_up(event.pos)

    # --- normal-mode mouse ----------------------------------------------

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

    def on_mouse_down(self, pos) -> None:
        item = self.card_at(pos)
        self.selected_id = item.id if item else None
        self.mouse_down_pos = pos
        if item:
            for cr in self.card_rects:
                if cr.item.id == item.id:
                    self.drag_item = item
                    self.drag_offset = (pos[0] - cr.rect.x, pos[1] - cr.rect.y)
                    self.drag_pos = pos
                    break

    def on_mouse_up(self, pos) -> None:
        if not self.drag_item:
            return
        item = self.drag_item
        self.drag_item = None
        target_col = self.column_at(pos)
        # Treat tiny movements as a click (selection only, no reorder).
        moved = self.mouse_down_pos and (
            abs(pos[0] - self.mouse_down_pos[0]) > 5 or abs(pos[1] - self.mouse_down_pos[1]) > 5
        )
        if not moved or target_col is None:
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

    # --- keyboard navigation --------------------------------------------

    def navigate(self, key: int) -> None:
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
                    return
            return

        col_idx = cols.index(item.column)
        col_items = self.board.items_in(item.column)
        row = col_items.index(item)

        if key in (pygame.K_UP, pygame.K_DOWN):
            step = -1 if key == pygame.K_UP else 1
            new_row = (row + step) % len(col_items)  # cycle within column
            self.selected_id = col_items[new_row].id
        else:  # left / right
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

        def submit(title: str, description: str) -> None:
            title = title.strip()
            if title:
                item = self.board.create(title, column=col, description=description.strip())
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
                self.persist(f"edit '{title}'")
        self.editor = ItemEditor("Edit item", submit, title=item.title, description=item.description)
        self.mode = self.EDIT

    def start_delete(self) -> None:
        item = self.selected()
        if not item:
            self.notify("no item selected")
            self.mode = self.NORMAL
            return

        def yes() -> None:
            title = item.title
            self.board.delete(item.id)
            self.selected_id = None
            self.persist(f"delete '{title}'")
        self.confirm = Confirm(f"Delete '{item.title}'?", yes)
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
            self.persist(f"point '{item.title}' = {item.points}")
        self.text_input = TextInput(f"Points for '{item.title}':", submit,
                                    initial=str(item.points), numeric=True)
        self.mode = self.TEXT

    def do_move(self, delta: int) -> None:
        item = self.selected()
        if not item:
            self.notify("no item selected")
            self.mode = self.NORMAL
            return
        self.board.move_relative(item.id, delta)
        self.persist(f"move '{item.title}' to {item.column}")
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
            self.notify(f"{action} -> {code_to_key_name(event.key)}")
            return
        if self.keys.matches("cancel", event.key):
            self.keys.save()
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

    def draw_columns(self, w: int, h: int) -> None:
        self.card_rects = []
        self.column_rects = {}
        cols = self.board.columns
        n = max(1, len(cols))
        area_top = TOPBAR_H + COL_GAP
        area_bottom = h - STATUSBAR_H - COL_GAP
        total_gap = COL_GAP * (n + 1)
        col_w = (w - total_gap) / n
        col_h = area_bottom - area_top

        for i, col in enumerate(cols):
            x = COL_GAP + i * (col_w + COL_GAP)
            rect = pygame.Rect(int(x), area_top, int(col_w), col_h)
            self.column_rects[col] = rect
            accent = COLUMN_COLORS[i % len(COLUMN_COLORS)]
            self.draw_column(col, rect, accent)

    def draw_column(self, name: str, rect: pygame.Rect, accent) -> None:
        pygame.draw.rect(self.screen, COL_BG, rect, border_radius=10)
        header = pygame.Rect(rect.x, rect.y, rect.w, HEADER_H)
        pygame.draw.rect(self.screen, COL_HEADER, header, border_radius=10)
        pygame.draw.rect(self.screen, accent, (rect.x, rect.y, 6, HEADER_H), border_top_left_radius=10)

        items = self.board.items_in(name)
        pts = sum(it.points for it in items)
        label = self.font.render(name, True, TEXT)
        self.screen.blit(label, (rect.x + 16, rect.y + HEADER_H // 2 - label.get_height() // 2))
        meta = self.font_sm.render(f"{pts} pts", True, MUTED)
        self.screen.blit(meta, (rect.right - meta.get_width() - 12,
                                rect.y + HEADER_H // 2 - meta.get_height() // 2))

        y = rect.y + HEADER_H + CARD_GAP
        for item in items:
            if self.drag_item and item.id == self.drag_item.id:
                continue  # drawn floating
            ch = self.draw_card(item, rect.x + COL_PAD, y, rect.w - 2 * COL_PAD)
            self.card_rects.append(CardRect(item, ch))
            y += ch.height + CARD_GAP

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

    def draw_card(self, item: Item, x: int, y: int, w: int, floating: bool = False) -> pygame.Rect:
        inner_w = w - 2 * CARD_PAD
        lines = self.wrap_text(item.title, self.font, inner_w - 40)
        text_h = len(lines) * self.font.get_linesize()

        desc_lines: list[str] = []
        if self.show_description(item, floating):
            # Descriptions may contain explicit newlines; wrap each paragraph.
            for para in item.description.split("\n"):
                desc_lines.extend(self.wrap_text(para, self.font_sm, inner_w) if para else [""])
        desc_h = len(desc_lines) * self.font_sm.get_linesize()
        if desc_lines:
            desc_h += 6  # gap between title and description

        card_h = max(CARD_MIN_H, text_h + desc_h + 2 * CARD_PAD)
        rect = pygame.Rect(x, y, w, card_h)
        if floating:
            color = CARD_BG_DRAG
        elif item.id == self.selected_id:
            color = CARD_BG_SEL
        else:
            color = CARD_BG
        pygame.draw.rect(self.screen, color, rect, border_radius=8)
        if item.id == self.selected_id and not floating:
            pygame.draw.rect(self.screen, ACCENT, rect, width=2, border_radius=8)

        ty = y + CARD_PAD
        for line in lines:
            surf = self.font.render(line, True, TEXT)
            self.screen.blit(surf, (x + CARD_PAD, ty))
            ty += self.font.get_linesize()

        if desc_lines:
            ty += 6
            for line in desc_lines:
                surf = self.font_sm.render(line, True, MUTED)
                self.screen.blit(surf, (x + CARD_PAD, ty))
                ty += self.font_sm.get_linesize()

        # points badge
        if item.points:
            badge = self.font_sm.render(str(item.points), True, (20, 24, 30))
            bw = badge.get_width() + 14
            brect = pygame.Rect(rect.right - bw - CARD_PAD, y + CARD_PAD, bw, 22)
            pygame.draw.rect(self.screen, BADGE, brect, border_radius=11)
            self.screen.blit(badge, (brect.x + 7, brect.y + 11 - badge.get_height() // 2))
        return rect

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
        surf = self.font_sm.render(status, True, MUTED)
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

    def draw_text_input(self, w: int, h: int) -> None:
        self.dim(w, h)
        rect = self.panel(w, h, 560, 180)
        ti = self.text_input
        prompt = self.font.render(ti.prompt, True, TEXT)
        self.screen.blit(prompt, (rect.x + 24, rect.y + 28))
        box = pygame.Rect(rect.x + 24, rect.y + 72, rect.w - 48, 40)
        pygame.draw.rect(self.screen, BG, box, border_radius=6)
        pygame.draw.rect(self.screen, ACCENT, box, width=1, border_radius=6)
        # blinking cursor
        cursor = "|" if (pygame.time.get_ticks() // 500) % 2 == 0 else " "
        txt = self.font.render(ti.text + cursor, True, TEXT)
        self.screen.blit(txt, (box.x + 10, box.y + box.h // 2 - txt.get_height() // 2))
        foot = self.font_sm.render("[ENTER] confirm   [ESC] cancel", True, MUTED)
        self.screen.blit(foot, (rect.x + 24, rect.bottom - 28))

    def draw_editor(self, w: int, h: int) -> None:
        self.dim(w, h)
        ed = self.editor
        rect = self.panel(w, h, 620, 380)
        prompt = self.font_lg.render(ed.prompt, True, TEXT)
        self.screen.blit(prompt, (rect.x + 24, rect.y + 18))

        cursor_on = (pygame.time.get_ticks() // 500) % 2 == 0
        y = rect.y + 70
        field_heights = [40, 150]
        for i, (label, value) in enumerate(zip(ed.labels, ed.fields)):
            lab = self.font_sm.render(label, True, MUTED)
            self.screen.blit(lab, (rect.x + 24, y))
            box = pygame.Rect(rect.x + 24, y + 22, rect.w - 48, field_heights[i])
            pygame.draw.rect(self.screen, BG, box, border_radius=6)
            border = ACCENT if i == ed.active else MUTED
            pygame.draw.rect(self.screen, border, box, width=2 if i == ed.active else 1, border_radius=6)
            # Render (possibly multi-line) value, with a cursor on the active field.
            shown = value + ("|" if (i == ed.active and cursor_on) else "")
            tx, ty = box.x + 10, box.y + 8
            for line in shown.split("\n"):
                for wrapped in self.wrap_text(line, self.font, box.w - 20) if line else [""]:
                    surf = self.font.render(wrapped, True, TEXT)
                    self.screen.blit(surf, (tx, ty))
                    ty += self.font.get_linesize()
            y += 22 + field_heights[i] + 16

        foot = self.font_sm.render(
            "TAB / ↑↓ switch field   ·   ENTER save (CTRL+ENTER in description)   ·   ESC cancel",
            True, MUTED)
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
