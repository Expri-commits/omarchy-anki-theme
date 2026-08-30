"""Palette-derived expectations: computed from the fixture, never remembered.

Each surface's expected colors derive from the vendored stock palette through
the locked mapping (ankiya.palette) — the oracle side of
docs/verification.md's "palette-derived oracles" principle. The aqt-side
grounding (which element consumes which var) is locked in each oracle
property's comment, verified against aqt 26.08.1's own CSS:

  deck canvas      body background → --canvas            (CANVAS)
  current row      tr.deck.current td background paints
                   --border-subtle opaque                (BORDER_SUBTLE)
  deck name        a.deck { color: var(--fg) }           (FG)
  reviewer canvas  body background → --canvas            (CANVAS)
  review buttons   button { background: var(--button-bg) } (BUTTON_BG)
  editor page      --bs-body-bg                          (bootstrap extra)
  editor inputs    --canvas-elevated                     (CANVAS_ELEVATED)
  editor focus     :focus { border-color: var(--border-focus) } (BORDER_FOCUS)
  menubar          QPalette window/text → CANVAS/FG      (Qt leg)
"""

from __future__ import annotations

import re
from pathlib import Path

from ankiya.palette import load_raw, map_palette

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "themes"

_HEX_RE = re.compile(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})")
_RGBA_RE = re.compile(
    r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9.]+)\s*\)"
)


def rgb(color: str) -> tuple[int, int, int]:
    """A mapped var value (`#rrggbb` or `rgba(r, g, b, a)`) → rgb triple."""
    if match := _HEX_RE.fullmatch(color.lower()):
        return tuple(int(g, 16) for g in match.groups())  # type: ignore[return-value]
    if match := _RGBA_RE.fullmatch(color.lower()):
        r, g, b, _a = match.groups()
        return int(r), int(g), int(b)
    raise ValueError(f"unparseable color {color!r}")


class ThemeOracle:
    """Expected colors for one palette, derived through the locked mapping."""

    def __init__(self, fixture_dir: str) -> None:
        text = (FIXTURES / fixture_dir / "colors.toml").read_text()
        self.palette, self.mode = load_raw(text)
        self.mapping = map_palette(self.palette)
        self.dark = self.mode == "dark"
        self.vars = self.mapping.vars

    def var(self, aqt_name: str) -> tuple[int, int, int]:
        return rgb(self.vars[aqt_name])

    # -- surface points (see module docstring for the aqt grounding) --------

    @property
    def canvas(self) -> tuple[int, int, int]:
        return self.var("CANVAS")

    @property
    def current_row(self) -> tuple[int, int, int]:
        return self.var("BORDER_SUBTLE")

    @property
    def fg(self) -> tuple[int, int, int]:
        """Core foreground: deck names and menubar text."""
        return self.var("FG")

    @property
    def button_fill(self) -> tuple[int, int, int]:
        return self.var("BUTTON_BG")

    @property
    def editor_input_fill(self) -> tuple[int, int, int]:
        return self.var("CANVAS_ELEVATED")

    @property
    def focus_ring(self) -> tuple[int, int, int]:
        return self.var("BORDER_FOCUS")
