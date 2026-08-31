"""Palette-derived expectations: computed from the fixture, never remembered.

Each surface's expected colors derive from the vendored stock palette through
the locked mapping (anki_theme.palette) — the oracle side of
docs/verification.md's "palette-derived oracles" principle. The aqt-side
grounding (which element consumes which var) is locked in each oracle
property's comment, verified against aqt 26.08.1's own CSS/QSS:

  deck canvas      body background → --canvas            (CANVAS)
  current row      tr.deck.current td background paints
                   --border-subtle opaque                (BORDER_SUBTLE)
  deck name        a.deck { color: var(--fg) }           (FG)
  reviewer canvas  body background → --canvas            (CANVAS)
  review buttons   button { background: var(--button-bg) } (BUTTON_BG)
  editor page      --bs-body-bg                          (bootstrap extra)
  editor inputs    --canvas-elevated                     (CANVAS_ELEVATED)
  editor focus     :focus { border-color: var(--border-focus) } (BORDER_FOCUS)
  prefs dialog     native Qt: QPalette Window → CANVAS; the tab pane paints
                   CANVAS_ELEVATED (aqt.stylesheets.tabwidget) — the svelte
                   page hides on a non-current Labs tab
  stats page       body background → --canvas            (CANVAS)
  stats bar        flot bar fill: colLearn = var(STATE_NEW)
                   at fill=0.7 over the page canvas      (STATE_NEW @0.7)
  menubar          QPalette window/text → CANVAS/FG      (Qt leg)
  menu popup       QMenu background = CANVAS_OVERLAY; ::item:selected =
                   HIGHLIGHT_BG (selection @ 0.5) composited over it —
                   an alpha blend, unlike the opaque deck row (ticket 23
                   characterization, aqt.stylesheets.menu)
"""

from __future__ import annotations

import re
from pathlib import Path

from anki_theme.palette import Mapping, load_raw, map_palette

REPO = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "themes"

_HEX_RE = re.compile(r"#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})")
_RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")
_RGBA_RE = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9.]+)\s*\)")


def rgb(color: str) -> tuple[int, int, int]:
    """A mapped var value (`#rrggbb`, `rgb(r, g, b)` or `rgba(r, g, b, a)`,
    the last two as reported by DOM computed styles) → rgb triple."""
    if match := _HEX_RE.fullmatch(color.lower()):
        return tuple(int(g, 16) for g in match.groups())  # type: ignore[return-value]
    if match := _RGB_RE.fullmatch(color.lower()):
        return tuple(int(g) for g in match.groups())  # type: ignore[return-value]
    if match := _RGBA_RE.fullmatch(color.lower()):
        r, g, b, _a = match.groups()
        return int(r), int(g), int(b)
    raise ValueError(f"unparseable color {color!r}")


def blend_over(
    fg_rgba: tuple[int, int, int, float], bg: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Composite one alpha color over an opaque background (the renderer's
    own math, per channel, rounded — the 8-bit result that hits the screen)."""
    r, g, b, a = fg_rgba
    br, bg_, bb = bg
    return (
        round(a * r + (1 - a) * br),
        round(a * g + (1 - a) * bg_),
        round(a * b + (1 - a) * bb),
    )


def rgba_parts(color: str) -> tuple[int, int, int, float]:
    match = _RGBA_RE.fullmatch(color.lower())
    if match is None:
        raise ValueError(f"not an rgba() color: {color!r}")
    r, g, b, a = match.groups()
    return int(r), int(g), int(b), float(a)


class ThemeOracle:
    """Expected colors for one palette, derived through the locked mapping."""

    def __init__(
        self,
        fixture_dir: str | None = None,
        *,
        palette: dict[str, str] | None = None,
        mode: str | None = None,
        mapping: Mapping | None = None,
    ) -> None:
        if fixture_dir is not None:
            text = (FIXTURES / fixture_dir / "colors.toml").read_text()
            self.palette, self.mode = load_raw(text)
        else:
            if palette is None or mode is None:
                raise ValueError("either a fixture dir or palette+mode is required")
            self.palette, self.mode = palette, mode
        # A supplied mapping overrides the plain one — the clamp legs pass
        # map_with_clamp's result so the oracle expects what the runtime
        # actually applies, not the authored palette verbatim.
        self.mapping = mapping if mapping is not None else map_palette(self.palette)
        self.dark = self.mode == "dark"
        self.vars = self.mapping.vars

    @classmethod
    def from_palette(cls, palette: dict[str, str], mode: str) -> ThemeOracle:
        return cls(palette=palette, mode=mode)

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
    def canvas_elevated(self) -> tuple[int, int, int]:
        """Elevated fills: editor inputs and the prefs tab pane."""
        return self.var("CANVAS_ELEVATED")

    @property
    def editor_input_fill(self) -> tuple[int, int, int]:
        return self.canvas_elevated

    @property
    def focus_ring(self) -> tuple[int, int, int]:
        return self.var("BORDER_FOCUS")

    # -- tier-3 surfaces -----------------------------------------------------

    @property
    def menu_bg(self) -> tuple[int, int, int]:
        """QMenu background: CANVAS_OVERLAY (dark_background verbatim)."""
        return self.var("CANVAS_OVERLAY")

    @property
    def menu_highlight(self) -> tuple[int, int, int]:
        """QMenu::item:selected: HIGHLIGHT_BG (selection @ 0.5) composited
        over the menu background — the blend the sampled pixel shows."""
        alpha_fill = rgba_parts(self.vars["HIGHLIGHT_BG"])
        return blend_over(alpha_fill, self.menu_bg)

    @property
    def link(self) -> tuple[int, int, int]:
        return self.var("FG_LINK")

    @property
    def selected_fg(self) -> tuple[int, int, int]:
        """Text on the selection fill (menu highlight row): SELECTED_FG —
        the on-tint slot the clamp's guard 3 owns."""
        return self.var("SELECTED_FG")

    @property
    def stats_added_bar(self) -> tuple[int, int, int]:
        """Legacy stats "Added" bar (26.08.1): theme_manager._update_stat_colors
        sets s.colLearn = var(STATE_NEW) (aqt/theme.py), and the intro graph
        draws it as a flot bar with fill=0.7, lineWidth=0 (anki/stats.py) —
        an alpha fill composited over the page canvas."""
        bar = self.var("STATE_NEW")
        return blend_over((bar[0], bar[1], bar[2], 0.7), self.canvas)


def anki_default(var_name: str, night_mode: str) -> tuple[int, int, int]:
    """Anki's own color for a var slot — read live from aqt.colors in this
    process, never remembered (the below-floor oracle: the inert add-on
    leaves Anki's defaults on screen)."""
    import aqt.colors as ak_colors
    from aqt.qt import QColor

    raw = getattr(ak_colors, var_name)[night_mode]
    color = QColor(raw)
    if not color.isValid():
        raise ValueError(f"aqt default {var_name}={raw!r} is not a color")
    return color.red(), color.green(), color.blue()
