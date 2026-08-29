"""Shared access to the vendored stock palettes (imported by the tier-1 tests).

pytest's flat layout (no tests/__init__.py) puts this directory on sys.path,
so test modules import it as a top-level module.
"""

from pathlib import Path

from ankiya.palette import load_palette

THEMES_DIR = Path(__file__).parent / "fixtures" / "themes"
THEMES = sorted(p.name for p in THEMES_DIR.iterdir() if p.is_dir())


def theme_palette(name: str) -> dict[str, str]:
    return load_palette((THEMES_DIR / name / "colors.toml").read_text())
