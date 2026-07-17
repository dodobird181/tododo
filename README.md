# Tododo

An encrypted Kanban board for personal and collaborative use, backed by GitHub
for file storage and version control.

## MCP server

Tododo exposes its board operations to MCP-capable agents (e.g. Claude Code) as
a second front door beside the HTTP API. Every tool forwards to the running app,
so the app's `Backend` stays the only writer to the event log.

### Install the console script

```bash
pipx install .        # global, on PATH (recommended)
pip install .         # into the current environment
```

This provides the `tododo` command.

### Register into a repository

```bash
python -m tododo.install [DIR ...]           # console mode (PATH command)
python -m tododo.install --mode linked [DIR] # pin this checkout instead
```

The app must be running (`python -m tododo`, port 8760) for tools to work.

## Encryption

Every event is encrypted at rest with AES-256-GCM before it lands in the
git-tracked `events-encrypted/` mirror. The scheme is *envelope encryption*:

- A random 32-byte **data key (DEK)** encrypts the events themselves.
- The DEK is **wrapped** (encrypted) under a key derived from your passphrase
  and stored in `events-encrypted/keyring`.
- The passphrase comes from the `TODODO_PASSPHRASE` environment variable. It is
  required — the app refuses to start if it is unset or empty. It is the only
  secret; the `keyring` file is useless without it, and losing the passphrase
  means the log cannot be decrypted.

Because the passphrase only guards the DEK (not each event), changing the
passphrase re-wraps one small file instead of re-encrypting the whole log.

```bash
export TODODO_PASSPHRASE='your-real-secret'   # add to your shell profile
```

## Key rotation

There are three distinct operations. Pick the one that matches your situation.
All commands are run from the repo root; commit `events-encrypted/` afterwards.

### 1. First-time migration (legacy log → envelope) — once, ever

Only needed for a log created before envelope encryption existed (v1, per-file
salt). Converts every event to the current format and creates the `keyring`.
Needs the **old** passphrase the legacy files were encrypted with.

```bash
export TODODO_PASSPHRASE='your-real-secret'                  # the NEW passphrase
python -m tododo.rekey migrate --old-passphrase OLD_PASS     # omit to be prompted
git add events-encrypted/ && git commit -m "TODODO: migrate to envelope encryption"
```

This re-encrypts the whole log, so it is the slow one — but you do it once. After
it succeeds, `python -m tododo` starts under the new passphrase and every later
rotation is the cheap `rewrap` below. You do **not** migrate again to rotate.

### 2. Change the passphrase — cheap, anytime

Re-wraps the data key under a new passphrase. Events are not touched.

```bash
export TODODO_PASSPHRASE='new-passphrase'                    # the NEW passphrase
python -m tododo.rekey rewrap --old-passphrase CURRENT_PASS  # omit to be prompted
git add events-encrypted/keyring && git commit -m "TODODO: rotate passphrase"
```

Update `TODODO_PASSPHRASE` in your shell profile to the new value. Other clones
pick up the new `keyring` on their next pull and must use the new passphrase.

### 3. Rotate the data key (suspected compromise) — full re-encrypt

If the passphrase or key may have leaked, re-wrapping is not enough — the bytes
protecting the events must change, which means re-encrypting every event under a
fresh DEK. Run the migration again against the current log:

```bash
python -m tododo.rekey migrate --old-passphrase CURRENT_PASS
```

Note: git history still contains the ciphertext written under the old key, so a
leaked passphrase exposes everything committed before the rotation. Rewriting
history (e.g. `git filter-repo`) is the only way to purge the old ciphertext.

