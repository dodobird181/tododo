# UI Behaviour Checklist

Living document. `[x]` = implemented in the web UI ([tododo/web/index.html](../tododo/web/index.html)); notes record where and any deviations.

- [x] Hitting ENTER on an item opens it for editing.
  - `Enter` on the cursor card (or double-click) opens the full editor. Bound as the `edit` action; rebindable.
- [x] Editing items opens a modal that allows editing across all fields (except deleted, id, and order.) Start and end are datetimes not just dates. Should open a classic calendar picker view.
  - `modalEdit`: title, description, start, end, board, column, assigned_to, report_to. `start`/`end` use `<input type="datetime-local">` (native calendar+time picker). Save diffs each field and emits one `EditItem` event per change. Changing board re-populates the column options.
- [x] Columns should be minimizable and which columns are minimized in which boards should be saved to userdata/workspace.yaml, see the example in /OLD.
  - Click a column header (or leader → `z`) to toggle. Persisted via `GET/POST /workspace` → [userdata/workspace.yaml](../tododo/workspace.py) as `boards.<id>.minimized`. Minimized columns collapse to a vertical bar with a count.
- [x] Themes should be in userdata/themes/theme1.css, theme2.css, etc., and prepopulated with the four themes from /OLD for testing.
  - Seeded on startup into `userdata/themes/` from bundled CSS ([tododo/themes/](../tododo/themes/)): `default` (Tokyo Night), `nord`, `dracula`, `gruvbox`. Switcher (leader → `t`) lists them via `GET /themes`, applies via `GET /theme?name=`, persists the choice in `workspace.yaml`. **Deviation:** files are named by palette (`default.css` …) rather than `theme1.css`; drop-in `*.css` still works.
- [x] Forget about CTRL except for dragging items. Use Space to start command sequences instead (or whatever keybinding space is set to.)
  - Leader is now the `leader` key (default `Space`, rebindable). Tap it, then an action key. Ctrl is used only for `Ctrl+Arrow` drag. `Ctrl+N` etc. fall through to the browser.
- [x] Implement multi-select of items using shift.
  - `Shift+Click` toggles a card; `Shift+Arrow` extends the selection along the cursor. `delete` and `Ctrl+←/→` (horizontal drag) act on the whole selection; vertical reorder acts on the cursor card.

## Follow-ups / notes
- Reordering (`order`) and multi-field edits emit one event per field; concurrent same-field edits still surface as conflicts (by design).
- Assignee/report fields are free-text github logins (no validation yet).
- `userdata/` is gitignored (per-machine), so workspace/theme/keybindings do not sync through the event log.

- [x] Moving the cursor / selection to a minimized column should temporarily expand it so the user cna see it's contents.
  - `isCollapsed(col)` renders a pinned-minimized column expanded while the cursor or a selected item sits in it; `moveCursor` now navigates every column (not just visible ones). Header badge shows `▾` (minimized but expanded) vs `▸` (collapsed).
- [x] CTRL + Enter when editing an item should save it and close the modal.
  - `modalEdit`'s key handler saves + closes on `Ctrl+Enter` (works from any field, including the description editor). Hint shown in the footer.
- [x] The board shortcut shouldn't just switch boards / toggle a list. it should open a modal for searching and selecting boards where the most recently selected board (saved in workspace.yaml) is at the top of the list of boards. they should be ordered chronologically with the current board excluded from the list altogether.
  - `switch_board` → `modalBoards`: searchable list, current board excluded, sorted by `boards.<id>.opened` (persisted in [workspace.yaml](../tododo/workspace.py)) descending. Every board switch records a fresh `opened` timestamp via `openBoard`.
- [x] Can i get a real markdown editor for the description PLEASEEEEEEEEEEE PRETTY PELASEEE
  - `markdownEditor`: toolbar (bold/italic/heading/list/numbered/code/quote/link) + textarea + **live preview**, dependency-free `renderMarkdown` (headings, bold/italic, inline + fenced code, lists, blockquote, hr, links). Used for the description field in the item editor. 💛
- [x] The page title should be TODODO: [BOARD NAME GOES HERE.] and tjhere should be no board dropdown living in the top left.
  - `updateTitle()` sets both `document.title` and the header `<h1>` to `TODODO: <board name>`. The top-left `<select>` dropdown is gone; switch boards via the `boards` button or leader → `b` (the board picker modal).


- [x] Page title should just be bopard name, no TODODO: prefix.
  - `updateTitle()` sets `document.title` and the header `<h1>` to the board name (falls back to "Tododo" when no board).
- [x] columns with no items in them should nevertheless be selectable for the purposes of creating new items in them.
  - New `cursorCol` focus model: arrow keys land on empty columns (highlighted via `.colfocus`); `create` / leader→`n` targets `focusedColumn()`. `moveCursor` traverses every column.
- [x] On all the cancelable dialogues, e.g., "delete?" dialogue. The arrow keys should navigate between Cancel and the other buttons that perform actions.
  - `modalConfirm` renders a focusable `[Cancel, Confirm]` button row; ←/↑ and →/↓ move focus (`.btnfocus` outline), Enter fires the focused button, Escape cancels.
- [x] The item edit view should be a workspace option / button for it to be on the right hand side / pane.
  - `workspace.edit_pane` (persisted) + the header **edit: modal/pane** toggle. `buildEditForm` is shared; `openEdit` routes to `modalEdit` or `mountPane` (docked right-hand `<aside id="editpane">`, Ctrl+Enter/Escape aware).
- [x] The markdown editing should be in-place the same as obsidian. So instead of an editor pane and a preview pane, it is all in one as you're editing!
  - Single-pane editor: a transparent textarea over a `mdHighlight` backdrop that colourises markdown in place (bold/italic/code/heading/quote/list/link, dimmed syntax markers). Character-metric-preserving (monospace) so the caret stays aligned. **Deviation from Obsidian:** syntax markers are dimmed, not hidden, and headings are recoloured but not resized — required to keep the overlay aligned without a full editor engine.
- [ ] Items should be drag and droppable inside the columns and you can get rid of the little clickable UI arrows for moving them.
- [x] Add a calendar mode (leader → `C`, i.e. Space then Shift+C) that hides the kanban board and shows every item with both a start and an end.
  - `calendarMode` + `toggleCalendar`: `paint()` routes to `paintCalendar` while active, rendering a Google-Calendar-style **week grid** (day columns × hour rows) from `calendarEvents()` (items with both `start` and `end`). Same-day items are positioned/sized by time in their day column with greedy side-by-side lane packing (`packLanes`) for overlaps; multi-day items sit in the all-day band as spanning bars; a red now-line marks the current time on today's column; today's date is circled. Nav bar (‹ / Today / ›) plus `←`/`→` move by week; vertical scroll is preserved across the periodic repaints. Clicking an event opens the editor; `Escape` exits. Reachable via the `calendar` action (leader key, default `C`) or the header **calendar** button. Scope: current board's items.
- [x] An item must have both a start and an end datetime (schedule validation).
  - `buildEditForm.save` rejects a save when exactly one of start/end is set, or end precedes start, showing an inline error and keeping the editor open. New items are seeded with both by the backend (see below), so they satisfy this by default.
- [x] Add a settings file (modelled on /OLD) with configurable defaults for a new item's start/end, relative to now.
  - [tododo/settings.py](../tododo/settings.py): `userdata/settings.yaml` (gitignored) over a `DEFAULT_SETTINGS` dict, exposed via `GET/POST /settings` and the header **settings** editor. `Backend.create_item` seeds `start`/`end` from `default_datetimes(...)`: each is an offset from now (default start +30m, end +60m) snapped onto a grid with a configurable rounding direction (`ceil`/`floor`/`none`) and interval (clamped to a 5-minute minimum).