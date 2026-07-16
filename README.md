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
