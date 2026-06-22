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

- Each edit commits immediately; pushes are **batched** — at most one push every
  `push_interval` seconds (default 2), bundling all commits made in between, always
  fetching + merging remote changes first (pull before push).
- The remote is polled for others' changes every `poll_interval` seconds
  (default 2); the board reloads live when the file changes. The status bar shows
  the last-fetch time.
- A `pre-push` git hook (installed on startup) also fetches before any push.

### Fetch instantly on a new commit (webhook)

A local git hook can't fire when *someone else* pushes — that's a remote event —
so the app can optionally run a small HTTP receiver. A POST to it triggers an
immediate fetch+merge instead of waiting for the periodic poll.

Enable it in `settings.yaml`:

```yaml
webhook_enabled: true
webhook_port: 8765
webhook_secret: 'some-shared-secret'   # optional but recommended
```

Then make the port reachable by the sender and point a webhook at it:

1. Expose it — on a laptop, a tunnel works: `cloudflared tunnel --url http://localhost:8765`
   (or `ngrok http 8765`). On a public host, just open the port.
2. In your GitHub repo: **Settings → Webhooks → Add webhook**, Payload URL =
   the exposed URL, Content type = `application/json`, Secret = the same
   `webhook_secret`, events = *Just the push event*.

Now every push from anyone triggers your local app to fetch within a second. A
`GET` to the URL returns `200` for a quick liveness check.

### Concurrent edits

Edits from multiple people are reconciled by git, and the sync never blocks on a
conflict. Non-conflicting changes (different items, additions, reordering) merge
automatically. When two people change the *same* lines, git auto-resolves using
the `merge_conflicts` setting:

- `incoming` (default) — the remote side wins (`git merge -X theirs`)
- `current` — your local side wins (`git merge -X ours`)

Because the merge is line-based, the board is parse-validated and de-duplicated
after every merge (`Board.load` drops duplicate ids); if a merge produces
something git genuinely can't resolve, sync backs off and shows `git: needs
manual merge` in the status bar rather than committing a broken board.

Tradeoff: on a true same-line conflict one side's edit is dropped silently per the
setting — there is no per-item dialog. Keeping each item's YAML block multi-line
(the default `Board.save` format) keeps edits to different items far enough apart
that they merge cleanly.
