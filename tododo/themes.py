"""
Theme files: CSS under `userdata/themes/`, seeded from the bundled defaults.

Each theme file sets the app's CSS custom properties on `:root`; the UI swaps
the active theme by loading one file's contents. Users can edit these or drop in
their own `theme<N>.css`. Bundled palettes are ported from the OLD app.
"""

from __future__ import annotations

import shutil
from pathlib import Path

BUNDLED = Path(__file__).parent / "themes"


def seed_themes(themes_dir: Path) -> None:
    """
    Copy any bundled theme not already present into `themes_dir` (once).
    """
    themes_dir.mkdir(parents=True, exist_ok=True)
    for source in BUNDLED.glob("*.css"):
        destination = themes_dir / source.name
        if not destination.exists():
            shutil.copyfile(source, destination)


def list_themes(themes_dir: Path) -> list[str]:
    return sorted(path.stem for path in themes_dir.glob("*.css"))


def read_theme(themes_dir: Path, name: str) -> str | None:
    """
    The CSS for one theme, or `None`. `name` is reduced to a bare filename to
    prevent path traversal.
    """
    path = themes_dir / f"{Path(name).name}.css"
    return path.read_text(encoding="utf-8") if path.is_file() else None
