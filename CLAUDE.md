# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`tododo` is a pygame kanban board. A single YAML file (`board.yaml`, or any
`*.yaml` board file in the repo root) is the source of truth; **git is the
durable backing store**. Every edit auto-commits locally and pushes (batched);
the remote is polled in the background so the running app reloads live when
someone else edits.

## Commands

```sh
poetry install
poetry run python -m tododo            # run the app (opens last-used board)
```

Command-line board access (for agents/scripts, no GUI):

```sh
python -m tododo.agent list [--column Todo] [--board board.yaml]   # JSON; --column '' = all
python -m tododo.agent move <id> <column> [--actor NAME] [--commit]
python -m tododo.agent edit <id> [--title T] [--description D] [--actor NAME] [--commit]
python -m tododo.agent create <title> [--column C] [--actor NAME] [--commit]
```

There is **no test suite** and no lint/typecheck config. `test.yaml` is a board
file, not tests. Verify changes by running the app.

## Exploring the code

Use the `codegraph` CLI to explore/read/list — it is indexed for this repo and
cheaper than `ls`/`grep`/`find`/`rg`:

```sh
codegraph files                 # file/symbol tree
codegraph query <symbol>        # find symbols
codegraph node <name>           # a symbol's source + caller/callee trail
codegraph explore <query...>    # relevant symbols + call paths in one shot
```

## Architecture

Wiring lives in [main.py](tododo/main.py): load `Board` + `Keybindings` +
`Settings`, install git hooks, start `GitSync`, then run `App`.

- **[board.py](tododo/board.py)** — the data model. `Board` (columns, items,
  `authors` registry keyed by email) and `Item`. `Board.load`/`save` are the
  only YAML I/O for boards. **`load` is also the merge safety net**: it dedupes
  items by id (a line-based git merge's main corruption mode is a duplicated
  item block) and snaps orphaned columns back to the first column. `save` writes
  via a `.tmp` + atomic replace and keeps each item multi-line so different
  items merge cleanly.

- **[gitsync.py](tododo/gitsync.py)** — the sync engine, two daemon threads:
  a **committer** (drains a queue, commits each mutation immediately) and a
  **syncer** (once/sec: batched push throttled by `push_interval`, always
  pull-before-push, idle fetch with exponential backoff up to
  `poll_backoff_max`). Concurrent edits reconcile with `git merge -X ours/theirs`
  per the `merge_conflicts` setting; a merge is parse-validated through
  `Board.load`/`save` before commit and falls back to `git: needs manual merge`
  rather than committing a broken board. **All git calls are best-effort** —
  failures surface as a status-bar string, never a crash. `_git_lock` serializes
  subprocess calls; `board_changed`/`on_commit` events signal the UI.

- **[ui.py](tododo/ui.py)** — everything visual and interactive (~175 symbols).
  `App` is the main loop and owns selection, drag/drop, palette, editors, board
  switching, and file-reload watching. `EditBuffer`/`TextInput`/`ItemEditor`/
  `Confirm` are the text-entry and modal widgets. After a mutation the app calls
  `persist(message)` → `board.save()` + `git.push_change(message)`.

- **[agent.py](tododo/agent.py)** — the CLI above. Mutations are attributed to
  an `--actor` (defaults to `$TODODO_ACTOR`, then `"agent"`): stored on the item
  and used as the git author of `--commit`, so in-app blame and `git log` agree.

- **[history.py](tododo/history.py)** — reconstructs per-item events by reading
  `git log` of the board file; timestamps/actors live in git, **not** in the
  YAML.

- **[avatars.py](tododo/avatars.py)** — GitHub avatars keyed by git email,
  resolved on background threads and cached into `board.authors` (so lookups are
  one-shot and version-controlled); falls back to a monogram circle.

### The defaults / user-copy pattern

Settings, keybindings, and themes all follow the same split: a
version-controlled `default_*.yaml` ships in the repo; on first run a
**gitignored** personal copy is generated from it and is what the app actually
reads. Files: `default_settings.yaml` → `settings.yaml`;
`default_keybindings.yaml` → `keybindings.yaml`; `themes/default_theme.yaml`
(+ bundled alternates) → `themes/current_theme.yaml`. Per-machine view state
(minimized columns, last-opened board) lives in gitignored
[userdata/workspace.yaml](tododo/workspace.py), namespaced per board.

## Working on this board via `[CLAUDE]` tasks

The user drives work by adding `[CLAUDE]` items to the **Todo** column of a
board file while the app runs live and owns the file. Per [prompt.md](prompt.md):
move a task to **Doing** the moment you start it and to **Done** only after you
verify it works. Prefer `python -m tododo.agent` for board moves so
`Board.load` normalization applies rather than hand-editing YAML.

The user commits in their own batches ("TODODO N") — do not offer to commit.
