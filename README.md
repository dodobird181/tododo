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
- **Enter** opens the editor for the selected item (title + description).
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

- A background thread fetches + merges remote changes every ~2 minutes; the board
  reloads live when the file changes.
- Creating/editing/moving/deleting items commits and pushes automatically.
- A `pre-push` git hook (installed on startup) fetches before any push.
