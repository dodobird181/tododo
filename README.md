# tododo

A pygame kanban board where a single YAML file (`board.yaml`) is the source of
truth, and git is the durable backing store. Every edit auto-commits and pushes;
the repo is fetched in the background and always before a push.

## Run

```sh
poetry install
poetry run python -m tododo
```

## Controls

- **Click** a card to select it. **Drag** a card to move/reorder between columns.
- **Arrow keys** navigate the selection: `←/→` across columns, `↑/↓` within a
  column (both wrap around).
- **Enter** opens the editor for the selected item (title + description). In the
  editor, **CTRL+A / C / V** select-all / copy / paste. Descriptions accept
  markdown (`# heading`, `**bold**`, `*italic*`, `` `code` ``, `- bullets`,
  `> quotes`) and render soft-styled on the card, Obsidian-style.
- **CTRL + Space** opens the command palette. Every command also works directly
  as **CTRL + <key>** without opening the palette:
  - **C** — create a new item (title + description)
  - **D** — delete selected item (with confirmation)
  - **CTRL + ← / →** — move selected item left / right one column
  - **CTRL + ↑ / ↓** — move selected item up / down within its column
  - **P** — assign a point value
  - **K** — edit keybindings
- Held keys repeat (e.g. hold backspace to delete, hold an arrow to keep moving).
- In the keybindings editor: `↑/↓` select, `Enter` rebind, `Esc` save + close.

## Data

- `board.yaml` — columns + items (title, description, points). Version-controlled;
  the single source of truth.
- `default_keybindings.yaml` / `default_settings.yaml` — version-controlled defaults.
- `keybindings.yaml` / `settings.yaml` — your personal copies, gitignored,
  auto-generated from the defaults on first run.

## Settings

`settings.yaml` (`descriptions`): `selected` shows a card's description only when
it is selected (default); `all` keeps every description expanded.

## Git sync

- Each edit commits immediately; pushes are bundled and throttled to at most once
  per second, always fetching + merging remote changes first (pull before push).
- When idle, the repo is fetched every ~2 minutes; the board reloads live when the
  file changes. The status bar shows the last-fetch time.
- A `pre-push` git hook (installed on startup) also fetches before any push.

### Concurrent edits

Edits from multiple people are reconciled with a **semantic 3-way merge by item
id** (`tododo/merge.py`) rather than git's line-by-line text merge:

- Edits to *different* items, additions, and reordering merge automatically — no
  conflict.
- Only a genuine **same-item collision** (both sides edited the same item, or one
  edited while the other deleted it, or the same id was added differently) pauses
  to ask. An in-app dialog shows **current vs incoming** field-by-field; press
  **C** keep current, **I** take incoming, **B** keep both, or **Esc** to cancel
  the merge. Your choices are committed as the merge and pushed.

This means you can almost never lose work to a race, and you never hand-edit git
conflict markers.
