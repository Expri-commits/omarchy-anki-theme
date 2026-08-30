"""Tier 1 — the locked mapping (wayfinder tickets 07 → implementation 16).

Fixtures are the 22 stock Omarchy palettes, vendored from
/usr/share/omarchy/themes so the oracles never drift with the live install.
"""

import re

import pytest
from ankiya.palette import (
    VAR_NAMES,
    PaletteError,
    contrast_ratio,
    fingerprint,
    load_palette,
    map_palette,
    pick_on_tint,
    relative_luminance,
)
from theme_fixtures import THEMES, THEMES_DIR, theme_palette

# The three stock palettes without `orange`/`brown` — their consumers
# (FLAG_2, STATE_BURIED, FLAG_7) degrade to Anki defaults (ticket 07 rule 5).
DEGRADATION_THEMES = {"last-horizon", "solitude", "white"}

CONSUMED_KEYS = (
    "background",
    "dark_background",
    "darker_background",
    "lighter_background",
    "foreground",
    "dark_foreground",
    "light_foreground",
    "muted",
    "selection",
    "accent",
    "bright_blue",
    "blue",
    "green",
    "red",
    "orange",
    "yellow",
    "magenta",
    "cyan",
    "brown",
)

_OPAQUE_RE = re.compile(r"#[0-9a-f]{6}")
_TRANSLUCENT_RE = re.compile(r"rgba\(\d+, \d+, \d+, 0\.[45]\)")


def full_palette(**overrides: str) -> dict[str, str]:
    """A synthetic palette carrying every consumed key (tests delete as needed)."""
    palette = dict.fromkeys(CONSUMED_KEYS, "#123456")
    palette.update(overrides)
    return palette


def test_all_22_stock_themes_vendored() -> None:
    assert len(THEMES) == 22


# The tier-1 tripwire itself (mapping ⊇ vendored snapshot) lives in
# test_drift.py with the snapshot it now ships beside (payload/ankiya/).


@pytest.mark.parametrize("theme", THEMES)
def test_every_stock_palette_maps_completely(theme: str) -> None:
    mapping = map_palette(theme_palette(theme))
    if theme in DEGRADATION_THEMES:
        assert dict(mapping.skipped) == {
            "FLAG_2": ("orange",),
            "STATE_BURIED": ("orange",),
            "FLAG_7": ("brown",),
        }
        assert set(mapping.vars) == set(VAR_NAMES) - {"FLAG_2", "STATE_BURIED", "FLAG_7"}
    else:
        assert mapping.skipped == ()
        assert set(mapping.vars) == set(VAR_NAMES)
        assert mapping.bootstrap == {
            "--bs-body-bg": mapping.vars["CANVAS"],
            "--bs-body-color": mapping.vars["FG"],
            "--bs-border-color": mapping.vars["BORDER"],
            "--bs-link-color": mapping.vars["FG_LINK"],
        }
        assert mapping.on_accent is not None


# Rule 2's other half: everything outside these four vars is opaque.
STOCK_ALPHA_VARS = {"CANVAS_GLASS", "BUTTON_DISABLED", "SELECTED_BG", "HIGHLIGHT_BG"}


@pytest.mark.parametrize("theme", THEMES)
def test_only_the_stock_alpha_vars_carry_alpha(theme: str) -> None:
    mapping = map_palette(theme_palette(theme))
    for name, value in mapping.vars.items():
        if name in STOCK_ALPHA_VARS:
            assert _TRANSLUCENT_RE.fullmatch(value), (theme, name, value)
        else:
            assert _OPAQUE_RE.fullmatch(value), (theme, name, value)
    for name, value in mapping.bootstrap.items():
        assert _OPAQUE_RE.fullmatch(value), (theme, name, value)


def test_catppuccin_spot_values() -> None:
    """The table's channel assignments, checked once against real keys."""
    m = map_palette(theme_palette("catppuccin")).vars
    assert m["CANVAS"] == "#1e1e2e"  # background
    assert m["CANVAS_ELEVATED"] == "#161622"  # dark_background
    assert m["CANVAS_CODE"] == "#101019"  # darker_background
    assert m["BUTTON_BG"] == "#313244"  # lighter_background
    assert m["FG"] == "#cdd6f4"
    assert m["FG_FAINT"] == "#585b70"  # muted
    assert m["BORDER_SUBTLE"] == "#45475a"  # selection — the deck-row highlight
    assert m["BORDER"] == "#585b70"  # muted, not selection
    assert m["BORDER_FOCUS"] == "#89b4fa"  # accent
    assert m["FLAG_7"] == "#7b5b55"  # brown
    assert m["STATE_SUSPENDED"] == "#f9e2af"  # yellow


def test_stock_alpha_rules() -> None:
    """Palette hue under Anki's stock translucency (rule 2)."""
    m = map_palette(theme_palette("catppuccin")).vars
    assert m["CANVAS_GLASS"] == "rgba(30, 30, 46, 0.4)"
    assert m["BUTTON_DISABLED"] == "rgba(88, 91, 112, 0.5)"
    assert m["SELECTED_BG"] == "rgba(69, 71, 90, 0.5)"
    assert m["HIGHLIGHT_BG"] == "rgba(69, 71, 90, 0.5)"
    assert m["BUTTON_PRIMARY_DISABLED"] == "#585b70"  # opaque in stock


def test_on_tint_is_luminance_derived_not_polarity() -> None:
    # Dark palette: light foreground wins against the mid-dark selection.
    m_dark = map_palette(theme_palette("catppuccin"))
    assert m_dark.vars["SELECTED_FG"] == "#cdd6f4"
    assert m_dark.vars["HIGHLIGHT_FG"] == "#cdd6f4"
    # Light palette whose selection tint is also light: the *dark* foreground
    # contrasts more — same rule, opposite-looking outcome, `mode` unseen.
    m_latte = map_palette(theme_palette("catppuccin-latte"))
    assert m_latte.vars["SELECTED_FG"] == "#4c4f69"
    # On-accent: catppuccin's light-blue accent takes the dark background
    # (7.8:1) over the light foreground (1.5:1) — the luminance rule, where
    # Anki's hardcoded white measures below the AA bar.
    assert m_dark.on_accent == "#1e1e2e"


def test_pick_on_tint_follows_contrast_not_argument_position() -> None:
    # Mid-gray fill: black (6.58:1) beats white (3.19:1) — from either slot.
    assert pick_on_tint("#ffffff", "#000000", "#909090") == "#000000"  # background wins
    assert pick_on_tint("#000000", "#ffffff", "#909090") == "#000000"  # foreground wins


def test_fg_accent_fallback() -> None:
    without_blues = {k: v for k, v in full_palette().items() if k not in ("blue", "bright_blue")}
    # Fallback when equal (the `white` theme's case: bright_blue == blue).
    equal = map_palette({**without_blues, "blue": "#1a1a1a", "bright_blue": "#1a1a1a"})
    assert equal.vars["FG_LINK"] == "#1a1a1a"
    # Fallback when bright_blue is absent.
    plain_only = map_palette({**without_blues, "blue": "#1a1a1a"})
    assert plain_only.vars["FG_LINK"] == "#1a1a1a"
    # Distinct bright_blue wins.
    bright = map_palette({**without_blues, "blue": "#1a1a1a", "bright_blue": "#89b4fa"})
    assert bright.vars["FG_LINK"] == "#89b4fa"
    assert bright.vars["ACCENT_CARD"] == "#89b4fa"
    # Neither: the accent-taking vars degrade.
    neither = map_palette(without_blues)
    assert neither.vars.get("FG_LINK") is None
    assert neither.vars.get("ACCENT_CARD") is None
    assert ("FG_LINK", ("bright_blue", "blue")) in neither.skipped


def test_missing_key_degrades_exactly_its_consumers() -> None:
    m = map_palette({k: v for k, v in full_palette().items() if k != "selection"})
    assert dict(m.skipped) == {
        "BORDER_SUBTLE": ("selection",),
        "SELECTED_BG": ("selection",),
        "HIGHLIGHT_BG": ("selection",),
        "SELECTED_FG": ("selection",),
        "HIGHLIGHT_FG": ("selection",),
    }
    assert len(m.vars) == len(VAR_NAMES) - 5


def test_mode_and_non_color_keys_are_ignored() -> None:
    palette = theme_palette("hackerman")  # carries hyprland_* non-color keys
    assert "mode" not in palette
    assert not any(key.startswith("hyprland_") for key in palette)
    # `mode` is dropped at load, so the mapping is a function of color keys only.
    assert map_palette(palette).skipped == ()


def test_malformed_value_degrades_like_an_absent_key() -> None:
    text = (
        (THEMES_DIR / "catppuccin" / "colors.toml")
        .read_text()
        .replace('selection = "#45475a"', 'selection = "sparkly"')
    )
    palette = load_palette(text)
    assert "selection" not in palette
    assert map_palette(palette).skipped[0] == ("BORDER_SUBTLE", ("selection",))


def test_malformed_toml_raises() -> None:
    with pytest.raises(PaletteError):
        load_palette("accent = ")
    with pytest.raises(PaletteError):
        load_palette("[unterminated")


def test_contrast_ratio_known_values() -> None:
    assert contrast_ratio("#000000", "#000000") == 1.0
    assert contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert relative_luminance("#ffffff") == pytest.approx(1.0)
    assert relative_luminance("#000000") == pytest.approx(0.0)


def test_fingerprint_stable_across_formatting_and_ignored_keys() -> None:
    palette = {"background": "#111111", "foreground": "#eeeeee"}
    assert fingerprint(palette, "dark") == fingerprint(dict(palette), "dark")
    # Same palette content, different formatting/extra non-color keys.
    a = load_palette('background = "#111111"\nforeground = "#eeeeee"\nmode = "dark"')
    b = load_palette('foreground="#eeeeee"\nbackground="#111111"\nmode="dark"\n')
    assert fingerprint(a, "dark") == fingerprint(b, "dark")


def test_fingerprint_tracks_palette_and_mode_changes() -> None:
    palette = {"background": "#111111", "foreground": "#eeeeee"}
    assert fingerprint(palette, "dark") != fingerprint(palette, "light")
    assert fingerprint(palette, "dark") != fingerprint(
        {"background": "#222222", "foreground": "#eeeeee"}, "dark"
    )
    # A palette swap between stock themes never collides.
    assert fingerprint(theme_palette("catppuccin"), "dark") != fingerprint(
        theme_palette("gruvbox"), "dark"
    )


def test_load_raw_returns_mode_alongside_palette() -> None:
    from ankiya.palette import load_raw

    text = 'background = "#111111"\nmode = "dark"\nhyprland_border = "x"'
    palette, mode = load_raw(text)
    assert palette == {"background": "#111111"}
    assert mode == "dark"
    # mode is the runtime's polarity input only — absent or non-string degrades
    # to None (light), never a mapping input.
    assert load_raw('background = "#111111"')[1] is None
    assert load_raw("mode = 3")[1] is None
    with pytest.raises(PaletteError):
        load_raw("mode = ")
