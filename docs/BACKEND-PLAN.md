# Tododo Backend Plan

Scope: **backend only** — event log, replay/projection, API (HTTP + MCP),
encryption, and GitHub sync. No UI/web this pass. Architecture source of truth is
[Tododo Design.drawio.xml](../Tododo%20Design.drawio.xml); domain is
[tododo.dsl.md](tododo.dsl.md). Reference implementation to mine (but not copy
wholesale) is [OLD/](../OLD/).

## Architecture (from the drawio)

```
 UI ─ request ─┐                         events/            events-encrypted/
 LLM ─ MCP ────┤                        (untracked,          (tracked, git)
               ▼                          plaintext)              ▲
        ┌─────────────┐  enqueue   ┌──────────────┐  write  ┌──────────┐  encrypt
        │ API Process │──────────► │ Actor Process│───────► │  events/ │───────────►
        │  UUID+200   │            │ replay→parent│         └──────────┘   (Filewatcher)
        └─────────────┘            │  →write event│              ▲  decrypt
               ▲ poll by UUID      └──────────────┘              │  (inbound pull)
               └───────────────────── projection ◄──── replay ───┘
                                                          │
                                                    GitSync push/pull
```

The **event log is the source of truth**; `Item`/`Board` are projections rebuilt
by replaying `events/` from zero. Git only ever tracks the *encrypted* mirror, so
it never sees a mergeable text conflict — every event is a uniquely-named file.

## Modules to build

| Module | Drawio box | Responsibility |
|--------|-----------|----------------|
| `models.py` | — | `Event`, `Item`, `Board`, `Conflict`, `Field` dataclasses per [tododo.dsl.md](tododo.dsl.md). |
| `log.py` | Item Folder (Untracked) | Append-only event store: write `events/<id>.yaml`, read all, tail. Only writer of raw files. |
| `projection.py` | (replay) | Materialized in-memory state. `apply(event)` idempotent (keyed by id), maintains per-`(target,field)` DAG heads; >1 head → `Conflict`. Full replay only at startup; local writes and pulled events both stream through `apply`. Winner by `(at,id)`. |
| `actor.py` | Actor Process | Drain command queue: **replay-to-current → pick `parent` → build `Event` → `log.append`**. Track UUID→result for polling. |
| `crypto.py` | (encryption) | AES-256-GCM per file, PBKDF2 passphrase key. Reuse [OLD/filewatcher_encrypt.py](../OLD/filewatcher_encrypt.py) scheme. |
| `filewatcher.py` | Filewatcher Process | Watch `events/` → encrypt new files → `events-encrypted/`. On inbound git pull, decrypt new `.enc` → `events/` → feed each new event id through `projection.apply` (incremental, no full replay). |
| `gitsync.py` | (git) | Commit/push `events-encrypted/`, pull-before-push, background poll. Reuse [OLD/tododo/gitsync.py](../OLD/tododo/gitsync.py) (best-effort, threaded). |
| `server.py` | API Process | HTTP/JSON: writes enqueue a command → return `{uuid}` + 200; `GET /job?uuid=` polls; reads fold the projection. |
| `mcp.py` | "MCP" tool call | Thin adapter exposing the same operations as MCP tools that enqueue commands. |

## Event & storage layout

- `events/<id>.yaml` — raw plaintext event, **gitignored**. Filename = `Event.id`.
- `events-encrypted/<id>.yaml.enc` — ciphertext of the above, **git-tracked**.
  **Filename stays plaintext** (only *contents* encrypted) so git shows clean
  file-adds, replay learns ids without decrypting, and the no-conflict property
  holds. Leaks only random event UUIDs + count. *(confirmed)*
- On clone only `events-encrypted/` exists → filewatcher decrypts all → `events/`
  rebuilt → projection replays.

## Request lifecycle (write)

1. `POST /item` (or MCP tool) → API validates, mints `uuid`, pushes a command to
   the actor queue, returns `{"uuid": ...}` + 200 immediately.
2. Actor replays log to current, resolves the `parent` for the touched field,
   builds the `Event`, writes `events/<id>.yaml`, records result under `uuid`.
3. Filewatcher sees the new file → encrypts → `events-encrypted/` → GitSync
   commits + pushes.
4. UI polls `GET /job?uuid=` until it returns the applied event / new state.

Reads (`ViewBoard`, `ListItems`, `ListConflicts`) skip the queue — they read the
materialized in-memory projection synchronously (no replay).

**Inbound (git pull):** GitSync pulls new `.enc` files → filewatcher decrypts →
`events/` → each new event id is streamed through `projection.apply`. No full
replay; the pulled events just extend the in-memory DAG.

## Design decisions

- **DD1 — Process model.** *(confirmed)* **One process, 3 threads** (API server +
  actor drainer + filewatcher/gitsync), queue as an explicit in-memory boundary
  so it can split into real processes later. Durability comes from the event
  files, not the queue.
- **DD2 — Conflict timing.** *(confirmed)* Detected at **replay**, never blocks a
  write. Actor's only pre-write step is replay-to-current to choose `parent`.
- **DD3 — Encryption.** *(confirmed)* AES-256-GCM per file, PBKDF2-from-passphrase,
  encrypt contents only, plaintext filenames (see layout). Events are immutable →
  encrypt once, never re-encrypt.
- **DD4 — Projection order.** Before a user resolves a conflict, the projection
  must still show *something*; pick the field's winner by `(at, id)` sort so it's
  deterministic across machines. A `Conflict` is still reported alongside.
- **DD5 — In-memory materialized state (replay once).** State is held in memory
  and the log is replayed **once, at startup**. After that, both the actor's local
  writes and the filewatcher's decrypted inbound pulls stream through one path,
  `projection.apply(event)`:
  - Per `(target, field)` the projection keeps the DAG of events linked by
    `parent`. **Heads = leaf events** (no child). One head → its value wins
    (tiebreak `(at, id)`); **>1 head → `Conflict`**.
  - `apply` is **idempotent** (same event id twice = no-op, so an event that both
    the actor wrote and the pull re-sees is safe) and **order-independent** (a
    late/concurrent pulled event just links to its `parent` wherever it sits). An
    event whose parent hasn't arrived yet is **buffered** until it does.
  - Because insertion is order-independent, streaming events one-by-one builds the
    exact same DAG as a full replay-from-zero — that equivalence is what licenses
    "replay once, then apply incrementally", even across git pulls.
  - Interior nodes of a collapsed (single linear head) field may be pruned to
    bound memory. Persisting the materialized state to disk to skip the startup
    replay is a later optimization, not v1.

  Worked example — Item X `title`:

  ```
  e1  parent=∅        "Buy milk"          heads={e1}
  e2  parent=e1       "Buy oat milk"      (Alice)
  e3  parent=e1       "Buy almond milk"   (Bob)      -> heads={e2,e3}  => CONFLICT
  e4  parent=[e2,e3]  "Buy oat milk"      (resolve)  -> heads={e4}     => resolved
  ```

  A *tail-only* scheme (store just the current value) would overwrite e2 with e3
  and lose the e1 fork — the conflict vanishes silently. The DAG keeps the parent
  links, so the fork is visible as two heads. Pull order (e3 before e2, etc.)
  yields the same final DAG.

## Build sequence

1. `models.py` + `log.py` + `projection.py` — pure core, testable with no I/O
   beyond the events folder. Verify: hand-write a few event files, assert the
   projection and a synthetic conflict.
2. `actor.py` + in-memory queue + UUID job map. Verify: enqueue commands, assert
   correct `parent` lineage and event files on disk.
3. `crypto.py` + `filewatcher.py`. Verify: write raw event → `.enc` appears →
   decrypt round-trips.
4. `gitsync.py`. Verify: two clones, edit each, both push/pull, replay converges
   with conflicts surfaced (not a git merge error).
5. `server.py` (HTTP) then `mcp.py`. Verify: `POST` returns uuid, poll returns
   result; MCP tool call produces an identical event.

## Test plan

A layered `pytest` suite in `tests/` mirroring the modules. Per CLAUDE.md, tests
are never run automatically — Sam runs `pytest`. Shared fixtures: a temp events
dir and a synthetic `Event` factory (`make_event(op, target, field, value,
parent=...)`).

**Pure unit (fast, no I/O):**
- `test_models` — `to_dict`/`from_dict` round-trips for Event/Item/Board/Field/
  Conflict; tolerance of malformed / missing fields.
- `test_projection` — the core; most tests live here:
  - fold correctness; single-head winner by `(at, id)`.
  - conflict detection (two heads); **no** conflict when edits touch *different*
    fields of the same item.
  - `ResolveConflict` collapses heads back to one.
  - **idempotent apply** — same event id applied twice == once.
  - **order-independence** — any causally-valid permutation of a fixed event set
    yields identical state (property-based via `hypothesis`, optional).
  - buffering — an event whose `parent` hasn't arrived is held, then resolves.
- `test_crypto` — encrypt→decrypt round-trip; wrong passphrase fails; GCM tamper
  detection; unique salt/nonce per call.

**Integration (tmpdir / git):**
- `test_log` — append writes `events/<id>.yaml`; read-all; idempotent re-append.
- `test_filewatcher` — raw write → `.enc` mirror appears; delete mirrors; inbound
  decrypt round-trip. Drive handler methods directly (don't wait on real fs
  events) for determinism.
- `test_gitsync` — two clones of a bare remote; edit each; push/pull; assert
  replay **converges with a surfaced `Conflict`**, not a git merge error. *(key
  correctness gate)*
- `test_actor` — enqueue command → correct `Event` built, `parent` lineage
  correct, uuid→result recorded.
- `test_server` — route dispatch; `POST` returns uuid; poll returns result; reads
  fold the projection. Call the handler directly or via an http test client.
- `test_mcp` — parity: an MCP tool call produces an identical event to the HTTP
  path.

**Correctness-gate scenarios (must-have):**
1. Concurrent same-field edits → 1 conflict; deterministic interim winner.
2. Resolve → conflict gone, chosen value stands, replay idempotent.
3. Different-field edits to same item → no conflict, both apply.
4. Out-of-order pull (parent already superseded) → same result as in-order.
5. Clone-from-encrypted-only → decrypt-all → projection equals origin.
6. Idempotent apply — apply same id twice == once.
