"""
Keybinding availability contexts.

Loads `keybinding_contexts.yaml`, the business-logic map describing WHEN each
keybinding action is active given the live app state (mode, open views, cursor
items). Unlike `keybindings.py` — which owns the per-machine `{action: key}`
mapping in userdata — this file is committed app configuration, identical for
everyone, so it lives beside this module inside the package rather than under
`userdata/`.

The document is served verbatim to the web UI, which evaluates each rule
against its own state snapshot; see the YAML header for the state shape and the
rule fields.
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_CONTEXTS_PATH = Path(__file__).with_name("keybinding_contexts.yaml")


def load_keybinding_contexts(path: Path = DEFAULT_CONTEXTS_PATH) -> dict:
    """
    Read the contexts document as a plain dict, or an empty {version, actions}
    skeleton if the file is missing so the UI always receives a usable shape.
    """
    if not path.exists():
        return {"version": 1, "actions": {}}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"version": 1, "actions": {}}
