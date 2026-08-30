"""The pathological palettes P1–P5 (ticket 08's fixtures), shared verbatim.

Tier 1 (test_clamp.py) predicts on them; tier 3 (the gate_full legs) renders
them as real user themes and asserts the render against the same prediction —
one source of truth, so a drift between the fixtures would break the
"log lines match tier-1's prediction" assert by construction.

Shapes (see ticket 08 and the clamp module docstring):

  P1  foreground invisible on the background trio — feasible, one nudge.
  P2  link == background — the link guard alone (backgrounds light).
  P3  mid-luminance accent+selection — the on-tint amendment alone.
  P4  straddling backgrounds — max-min foreground, `AA unsatisfiable`.
  P5  dead-zone trio #ffffff/#595959/#010101 — max-min foreground.
"""

from __future__ import annotations


def base_palette(**overrides: str) -> dict[str, str]:
    """A healthy catppuccin-shaped base the clamp never touches."""
    palette = {
        "background": "#1e1e2e",
        "dark_background": "#181825",
        "darker_background": "#11111b",
        "lighter_background": "#313244",
        "foreground": "#cdd6f4",
        "muted": "#585b70",
        "selection": "#45475a",
        "accent": "#89b4fa",
        "blue": "#89b4fa",
        # Equal to blue — the `white`-theme fallback shape; healthy either way.
        "bright_blue": "#89b4fa",
    }
    palette.update(overrides)
    return palette


# P1 — foreground invisible on a (dark) background trio: feasible, the nudge
# lifts it to the 4.5 floor against the lightest background. Selection/accent
# are light so the on-tint pass stays quiet (the chained case is its own test).
P1 = base_palette(foreground="#202030", selection="#89b4fa")

# The chained fixture: P1 with the base mid-dark selection — the nudged
# foreground still cannot read on it, so guard 3 extends afterwards.
CHAIN = base_palette(foreground="#202030")

# P2 — link invisible on the background (link == background exactly). The
# background family is all light so the dark foreground clears guard 1 and
# only the link guard fires.
P2 = base_palette(
    foreground="#1e1e2e",
    background="#89b4fa",
    dark_background="#7aa3f0",
    darker_background="#6b93e6",
    blue="#89b4fa",
    bright_blue="#89b4fa",
)

# P3 — mid-luminance accent and selection: both base on-tint candidates fall
# below 3.0, so the amendment extends the candidates; nothing else fails.
P3 = base_palette(
    foreground="#d0d0d0",
    background="#4a4a4a",
    dark_background="#2a2a2a",
    darker_background="#1a1a1a",
    selection="#909090",
    accent="#909090",
)

# P4 — straddling backgrounds: two light + one dark, no foreground clears 4.5
# against all three (pairwise band violated) → max-min + the honest log line.
P4 = base_palette(
    foreground="#808080",
    background="#e0e0e0",
    dark_background="#d0d0d0",
    darker_background="#303030",
    selection="#45475a",
    accent="#303030",
    blue="#1a1a1a",
    bright_blue="#1a1a1a",
)

# P5 — the dead-zone trio: passes the extremes-only test (#ffffff/#010101
# admit a narrow foreground band) but the mid-luminance #595959 kills it.
P5 = base_palette(
    foreground="#808080",
    background="#ffffff",
    dark_background="#595959",
    darker_background="#010101",
    selection="#2a2a2a",
    accent="#2a2a2a",
    blue="#1a1a1a",
    bright_blue="#1a1a1a",
)

# The night_mode each pathological theme's colors.toml declares — chosen to
# match its background family, so Anki's own polarity and the palette agree.
MODES: dict[str, str] = {
    "p1": "dark",
    "p2": "light",
    "p3": "dark",
    "p4": "dark",
    "p5": "dark",
}
