"""Omarchy palette → Anki color variables: the locked mapping, pure functions.

Wayfinder ticket 07 locked the table; this module is its executable form. It
imports nothing from aqt/Qt — the applier (ticket 17) consumes `Mapping` on
the Anki side, tier-1 pytest consumes it everywhere else.

Cross-cutting rules encoded here (ticket 07):
  1. Total reskin, polarity-agnostic — `mode` is never consulted; every var
     gets one absolute value (the applier writes it to both light/dark slots).
  2. Palette hue + stock translucency — glass 0.4, disabled button 0.5,
     selection/highlight 0.5; everything else opaque.
  3. On-tint derivation — foreground on a tinted fill is whichever of
     `foreground`/`background` contrasts more against the fill.
  5. Missing keys degrade exactly the vars consuming them to Anki defaults
     (skip + log via `Mapping.skipped`); extra keys are ignored.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass

# `#rrggbb` — the only value form Omarchy colors.toml carries for color keys
# (verified across all 22 stock themes); anything else is treated as absent.
_HEX_RE = re.compile(r"#[0-9a-f]{6}")

# Stock Anki translucency (ticket 07 rule 2, grounded in _aqt/colors.py):
# CANVAS_GLASS 0.4, BUTTON_DISABLED / SELECTED_BG / HIGHLIGHT_BG 0.5.
GLASS_ALPHA = 0.4
DISABLED_ALPHA = 0.5
SELECTION_ALPHA = 0.5


class PaletteError(ValueError):
    """colors.toml is not parseable TOML — the caller decides how to surface."""


# A var's source: a palette key verbatim, or a derived rule.
#   ("alpha", key, a)      palette hue under stock alpha `a`
#   ("fg_accent",)         `bright_blue`, falling back to `blue` (rule 5)
#   ("on_tint", key)       luminance-derived foreground on fill `key` (rule 3)
type Rule = str | tuple[str, ...]

VAR_RULES: dict[str, Rule] = {
    # Neutral surfaces
    "CANVAS": "background",
    "CANVAS_INSET": "background",
    "CANVAS_ELEVATED": "dark_background",
    "CANVAS_OVERLAY": "dark_background",
    "CANVAS_CODE": "darker_background",
    "SHADOW_INSET": "darker_background",
    "CANVAS_GLASS": ("alpha", "background", GLASS_ALPHA),
    # Text
    "FG": "foreground",
    "FG_SUBTLE": "dark_foreground",
    "FG_DISABLED": "dark_foreground",
    "FG_FAINT": "muted",
    "FG_LINK": ("fg_accent",),
    "SCROLLBAR_BG_HOVER": "dark_foreground",
    "SCROLLBAR_BG_ACTIVE": "light_foreground",
    # Borders
    "BORDER_SUBTLE": "selection",
    "BORDER": "muted",
    "BORDER_STRONG": "dark_foreground",
    "BORDER_FOCUS": "accent",
    # Buttons
    "BUTTON_BG": "lighter_background",
    "BUTTON_GRADIENT_START": "background",
    "BUTTON_GRADIENT_END": "lighter_background",
    "BUTTON_HOVER_BORDER": "dark_foreground",
    "BUTTON_PRIMARY_BG": "accent",
    "BUTTON_PRIMARY_GRADIENT_START": "accent",
    "BUTTON_PRIMARY_GRADIENT_END": "accent",
    "BUTTON_PRIMARY_DISABLED": "muted",
    "BUTTON_DISABLED": ("alpha", "muted", DISABLED_ALPHA),
    # Scrollbar
    "SCROLLBAR_BG": "muted",
    # Shadows
    "SHADOW": "muted",
    "SHADOW_SUBTLE": "muted",
    "SHADOW_FOCUS": "accent",
    # Selection / highlight
    "SELECTED_BG": ("alpha", "selection", SELECTION_ALPHA),
    "HIGHLIGHT_BG": ("alpha", "selection", SELECTION_ALPHA),
    "SELECTED_FG": ("on_tint", "selection"),
    "HIGHLIGHT_FG": ("on_tint", "selection"),
    # Accents
    "ACCENT_CARD": ("fg_accent",),
    "ACCENT_NOTE": "green",
    "ACCENT_DANGER": "red",
    # Card states
    "STATE_NEW": "blue",
    "STATE_LEARN": "red",
    "STATE_REVIEW": "green",
    "STATE_BURIED": "orange",
    "STATE_SUSPENDED": "yellow",
    "STATE_MARKED": "magenta",
    # Flags
    "FLAG_1": "red",
    "FLAG_2": "orange",
    "FLAG_3": "green",
    "FLAG_4": "blue",
    "FLAG_5": "magenta",
    "FLAG_6": "cyan",
    "FLAG_7": "brown",
}

# Bootstrap extras for the sveltekit pages (CSS-only — no aqt.colors slot).
BOOTSTRAP_RULES: dict[str, Rule] = {
    "--bs-body-bg": "background",
    "--bs-body-color": "foreground",
    "--bs-border-color": "muted",
    "--bs-link-color": ("fg_accent",),
}

# Every aqt.colors name the mapping claims to cover (insertion order = table
# order). The tier-1 tripwire asserts this against the installed Anki's
# snapshot (ticket 21); unknown runtime names are skipped + logged by the
# applier (rule 6).
VAR_NAMES = tuple(VAR_RULES)


@dataclass(frozen=True)
class Mapping:
    """A palette mapped onto Anki's color variables.

    vars: aqt.colors name → value string (`#rrggbb` or `rgba(r, g, b, a)` —
        the two forms ThemeManager.qcolor parses), covering every var whose
        consuming keys are present.
    bootstrap: CSS-only `--bs-*` extras, same policy.
    skipped: (var, missing palette keys) — vars and bootstrap extras left at
        Anki's own values (missing-key degradation, rule 5).
    on_accent: the on-accent foreground for the injected `.primary` rule
        (rule 3), or None when its keys are missing.
    """

    vars: dict[str, str]
    bootstrap: dict[str, str]
    skipped: tuple[tuple[str, tuple[str, ...]], ...]
    on_accent: str | None


def load_raw(text: str) -> tuple[dict[str, str], str | None]:
    """Parse colors.toml text into `(palette, mode)` in one pass.

    The palette keeps only `#rrggbb` values (see `load_palette`); `mode` rides
    alongside for the runtime's night_mode choice — the mapping itself never
    consults it (ticket 07's polarity-agnostic rule). Malformed TOML raises
    PaletteError.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PaletteError(str(exc)) from exc
    palette: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, str) and (match := _HEX_RE.fullmatch(value.lower())):
            palette[key] = match.group(0)
    mode = raw.get("mode")
    return palette, mode if isinstance(mode, str) else None


def load_palette(text: str) -> dict[str, str]:
    """Parse colors.toml text into `{key: "#rrggbb"}`.

    `mode` and any key whose value is not `#rrggbb` (non-color config such as
    `hyprland_active_border`) are ignored; a consumed key dropped here
    degrades exactly like an absent one. Malformed TOML raises PaletteError.
    """
    return load_raw(text)[0]


def fingerprint(palette: dict[str, str], mode: str | None) -> str:
    """Digest of exactly what theming consumes — the watcher's change test.

    Color keys plus the ``mode`` that picks night_mode; formatting churn and
    non-color config (e.g. hyprland border colors) leave it stable, so state
    -dir events that didn't change the palette never re-theme Anki.
    """
    payload = repr((mode, sorted(palette.items())))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _channels(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def relative_luminance(color: str) -> float:
    """WCAG 2.x relative luminance of a `#rrggbb` color."""
    linear = []
    for channel in _channels(color):
        s = channel / 255
        linear.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two `#rrggbb` colors (≥ 1.0)."""
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def pick_on_tint(foreground: str, background: str, fill: str) -> str:
    """Rule 3: the foreground-on-tint candidate with higher contrast wins.

    Ties resolve to `foreground` — deterministic, no luminance claim (an
    exact tie between candidates straddling the fill picks the foreground
    even when the background is the lighter candidate).
    """
    fg_ratio = contrast_ratio(foreground, fill)
    bg_ratio = contrast_ratio(background, fill)
    return foreground if fg_ratio >= bg_ratio else background


def _fg_accent(palette: dict[str, str]) -> str | None:
    bright, plain = palette.get("bright_blue"), palette.get("blue")
    if bright is not None and bright != plain:
        return bright
    return plain


def _resolve(palette: dict[str, str], rule: Rule) -> tuple[str | None, tuple[str, ...]]:
    """Resolve one rule to (value, missing keys) — value is None iff missing."""
    if isinstance(rule, str):
        value = palette.get(rule)
        return value, () if value is not None else (rule,)
    kind = rule[0]
    if kind == "alpha":
        _, key, alpha = rule
        value = palette.get(key)
        if value is None:
            return None, (key,)
        r, g, b = _channels(value)
        return f"rgba({r}, {g}, {b}, {alpha})", ()
    if kind == "fg_accent":
        value = _fg_accent(palette)
        return value, () if value is not None else ("bright_blue", "blue")
    if kind == "on_tint":
        _, fill_key = rule
        foreground, background, fill = (
            palette.get("foreground"),
            palette.get("background"),
            palette.get(fill_key),
        )
        missing = tuple(
            name
            for name, value in (
                ("foreground", foreground),
                ("background", background),
                (fill_key, fill),
            )
            if value is None
        )
        if missing:
            return None, missing
        assert foreground is not None and background is not None and fill is not None
        return pick_on_tint(foreground, background, fill), ()
    raise ValueError(f"unknown rule kind: {kind!r}")


def map_palette(palette: dict[str, str]) -> Mapping:
    """Map a loaded palette onto Anki's color variables (the locked table)."""
    vars_: dict[str, str] = {}
    bootstrap: dict[str, str] = {}
    skipped: list[tuple[str, tuple[str, ...]]] = []
    for rules, target in ((VAR_RULES, vars_), (BOOTSTRAP_RULES, bootstrap)):
        for name, rule in rules.items():
            value, missing = _resolve(palette, rule)
            if value is None:
                skipped.append((name, missing))
            else:
                target[name] = value
    fg, bg, accent = (
        palette.get("foreground"),
        palette.get("background"),
        palette.get("accent"),
    )
    on_accent = pick_on_tint(fg, bg, accent) if None not in (fg, bg, accent) else None
    return Mapping(vars_, bootstrap, tuple(skipped), on_accent)
