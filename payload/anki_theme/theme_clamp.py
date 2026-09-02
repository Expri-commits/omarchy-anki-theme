"""The clamp: normalize-then-map pre-pass for hostile palettes (ticket 08).

A user palette can pair a foreground with backgrounds it disappears into.
Policy: **clamp toward legibility, foregrounds only, never fall back
wholesale** — the line is empirical, each floor being the highest WCAG
bar that zero of the 22 stock palettes violate, so stock renders are
invisible to the clamp and only what the Omarchy ecosystem itself would
never ship gets adjusted.

Guarded relationships (floors as measured against all 22 stock palettes):

  foreground vs background, dark_background, darker_background   4.5 (min 5.30)
  link key (bright_blue, else blue) vs background                3.0 (min 3.06)
  on-tint text (SELECTED_FG, HIGHLIGHT_FG, on_accent) vs fill    3.0 (min 3.14)
  disabled text (FG_DISABLED) vs its composited fill             3.0 (min 5.19)

Verbatim by policy — never clamped: backgrounds (the user chose them; we
adjust what reads, not what it reads on), subtle/faint text (stock sits
below 4.5 by design), states and flags (deck-table accentuation is not
body text), accent as ornament, card-template CSS.

Mechanics: guards 1–2 nudge the foreground-side key's HSL **lightness**
only — hue and saturation preserved — by the smallest step that clears
the floor against every guarded fill at once, measured on the rounded
8-bit result. Ticket 08 derives the per-fill termination bound (white or
black clears 4.58:1 against any fill, so a single-fill nudge always
terminates) and the pairwise-band algebra behind infeasibility; both are
realized here as one lightness scan, which is exact where the algebra is
a proxy: it searches the full lightness line for the nearest point
clearing every fill, and when no point does — straddling backgrounds, a
mid-luminance dead zone — it takes the max-min point and the adjustment
is logged as unsatisfiable. Guard 3 is ticket 08's amendment to ticket 07
rule 3, applied after the locked mapping: when the mapped on-tint choice
measures below 3.0 against its fill, the candidate set extends to
{foreground, background, #ffffff, #000000} — the best of those clears
4.58:1 against any fill by the same math.

The link-key choice keeps states and flags verbatim wherever possible:
`bright_blue` is adjusted when the palette carries it (if it equalled
`blue`, it simply becomes distinct and the flags keep stock `blue`);
`blue` itself is touched only when it is the sole link source, and the
log line says so.

GUI-free like the rest of the pure core: the Applier's `_map` calls
`map_with_clamp`; ticket 16's suites keep calling `map_palette` directly.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, replace

from anki_theme.palette import (
    DISABLED_ALPHA,
    Mapping,
    channels,
    composite_over,
    contrast_ratio,
    map_palette,
    relative_luminance,
)

FG_FLOOR = 4.5
LINK_FLOOR = 3.0
ON_TINT_FLOOR = 3.0

WHITE = "#ffffff"
BLACK = "#000000"

GUARDED_BACKGROUNDS = ("background", "dark_background", "darker_background")

# Guard 3's slots: (mapping slot, palette fill key, alpha — None for a raw
# fill). "on_accent" is the injected `.primary` rule's color
# (Mapping.on_accent), not an aqt var. FG_DISABLED's fill is the composited
# disabled-button fill the mapping itself derives against (ledger row 7).
ON_TINT_SLOTS = (
    ("SELECTED_FG", "selection", None),
    ("HIGHLIGHT_FG", "selection", None),
    ("on_accent", "accent", None),
    ("FG_DISABLED", "muted", DISABLED_ALPHA),
)

# Scan resolution for the lightness nudge: 1/1024 steps cover the line
# densely enough that a feasible band wider than ~0.001 in lightness lands
# a valid point; anything narrower degrades to the max-min fallback below.
_LIGHTNESS_STEPS = 1024


@dataclass(frozen=True)
class Adjustment:
    """One clamped value, fully described for its log line.

    before/after carry the measured contrast ratios against the guarded
    fills, in the relationship's fill order. unsatisfiable marks the
    max-min fallback: no color on the nudged lightness line clears the
    floor against every fill at once.
    """

    key: str
    old: str
    new: str
    relationship: str
    floor: float
    before: tuple[float, ...]
    after: tuple[float, ...]
    unsatisfiable: bool = False
    detail: str = ""

    def line(self) -> str:
        ratios = "/".join(f"{ratio:.2f}" for ratio in self.before)
        after = "/".join(f"{ratio:.2f}" for ratio in self.after)
        text = (
            f"contrast clamp: {self.key} {self.old} → {self.new} "
            f"({self.relationship} @{self.floor:g}: {ratios} → {after})"
        )
        if self.detail:
            text += f", {self.detail}"
        if self.unsatisfiable:
            text += " — AA unsatisfiable across backgrounds, max-min chosen"
        return text


def _from_hls(hue: float, sat: float, lightness: float) -> str:
    r, g, b = (round(c * 255) for c in colorsys.hls_to_rgb(hue, lightness, sat))
    return f"#{r:02x}{g:02x}{b:02x}"


def _nudge(color: str, fills: tuple[str, ...], floor: float) -> tuple[str, bool]:
    """Smallest lightness move of `color` (hue/sat fixed) clearing `floor`
    against every fill, measured on rounded 8-bit channels.

    Returns (color, unsatisfiable): the nearest valid point on the lightness
    line — or, when none exists, the point maximizing the minimum contrast.
    Both ends of the line are pure black/white regardless of hue/sat, which
    is the per-fill bound guaranteeing a single fill always has a valid
    point; only jointly-hostile fills can force the fallback.
    """
    r, g, b = channels(color)
    hue, lightness, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    # Luminances hoisted out of the scan: each step measures the one
    # candidate luminance against cached fill values.
    fill_luminances = tuple(relative_luminance(fill) for fill in fills)

    best_valid: tuple[float, str] | None = None  # (|Δlightness|, color)
    best_min: tuple[float, float, str] | None = None  # (min ratio, -|Δl|, color)
    for step in range(_LIGHTNESS_STEPS + 1):
        candidate_lightness = step / _LIGHTNESS_STEPS
        candidate = _from_hls(hue, sat, candidate_lightness)
        candidate_luminance = relative_luminance(candidate)
        min_ratio = min(
            (max(candidate_luminance, fl) + 0.05) / (min(candidate_luminance, fl) + 0.05)
            for fl in fill_luminances
        )
        delta = abs(candidate_lightness - lightness)
        if min_ratio >= floor and (best_valid is None or delta < best_valid[0]):
            best_valid = (delta, candidate)
        if best_min is None or (min_ratio, -delta) > best_min[:2]:
            best_min = (min_ratio, -delta, candidate)
    if best_valid is not None:
        return best_valid[1], False
    assert best_min is not None  # the scan always yields a candidate
    return best_min[2], True


def clamp_palette(palette: dict[str, str]) -> tuple[dict[str, str], tuple[Adjustment, ...]]:
    """Guards 1–2: nudge `foreground` and the link key toward their floors.

    Returns the (possibly) adjusted palette and its adjustment log. Missing
    keys leave their guard silent — a palette without a key degrades exactly
    per the mapping's missing-key policy, and the clamp never invents one.
    """
    adjusted = dict(palette)
    adjustments: list[Adjustment] = []

    foreground = palette.get("foreground")
    guarded = [(key, palette[key]) for key in GUARDED_BACKGROUNDS if key in palette]
    if foreground is not None and guarded:
        fills = tuple(fill for _, fill in guarded)
        before = tuple(contrast_ratio(foreground, fill) for fill in fills)
        if min(before) < FG_FLOOR:
            new, unsatisfiable = _nudge(foreground, fills, FG_FLOOR)
            if new != foreground:
                adjusted["foreground"] = new
                adjustments.append(
                    Adjustment(
                        key="foreground",
                        old=foreground,
                        new=new,
                        relationship="vs " + "/".join(key for key, _ in guarded),
                        floor=FG_FLOOR,
                        before=before,
                        after=tuple(contrast_ratio(new, fill) for fill in fills),
                        unsatisfiable=unsatisfiable,
                    )
                )

    link_key = "bright_blue" if "bright_blue" in palette else "blue"
    link = palette.get(link_key)
    background = palette.get("background")
    if link is not None and background is not None:
        before = contrast_ratio(link, background)
        if before < LINK_FLOOR:
            new, unsatisfiable = _nudge(link, (background,), LINK_FLOOR)
            if new != link:
                adjusted[link_key] = new
                adjustments.append(
                    Adjustment(
                        key=link_key,
                        old=link,
                        new=new,
                        relationship="vs background",
                        floor=LINK_FLOOR,
                        before=(before,),
                        after=(contrast_ratio(new, background),),
                        unsatisfiable=unsatisfiable,
                        detail="sole link source" if link_key == "blue" else "",
                    )
                )

    return adjusted, tuple(adjustments)


def clamp_on_tint(
    mapping: Mapping, palette: dict[str, str]
) -> tuple[Mapping, tuple[Adjustment, ...]]:
    """Guard 3: extend the on-tint candidates of a mapped palette.

    Runs on the clamped palette's mapping (the chained policy: a nudged
    foreground is the on-tint candidate the mapping actually used). Slots
    that degraded for missing keys are skipped — there is no rendered
    value to guard.
    """
    adjustments: list[Adjustment] = []
    vars_ = dict(mapping.vars)
    on_accent = mapping.on_accent
    for slot, fill_key, alpha in ON_TINT_SLOTS:
        value = on_accent if slot == "on_accent" else vars_.get(slot)
        fill = palette.get(fill_key)
        if fill is not None and alpha is not None:
            over = palette.get("background")
            fill = fill if over is None else composite_over(fill, alpha, over)
        if value is None or fill is None:
            continue
        before = contrast_ratio(value, fill)
        if before >= ON_TINT_FLOOR:
            continue
        candidates = [c for c in (palette.get("foreground"), palette.get("background")) if c]
        candidates += [WHITE, BLACK]
        best = max(candidates, key=lambda c: contrast_ratio(c, fill))
        if slot == "on_accent":
            on_accent = best
        else:
            vars_[slot] = best
        relationship = (
            f"on {fill_key}" if alpha is None else f"on {fill_key}@{alpha:g} over background"
        )
        adjustments.append(
            Adjustment(
                key=slot,
                old=value,
                new=best,
                relationship=relationship,
                floor=ON_TINT_FLOOR,
                before=(before,),
                after=(contrast_ratio(best, fill),),
                detail="extended candidates",
            )
        )
    return replace(mapping, vars=vars_, on_accent=on_accent), tuple(adjustments)


def map_with_clamp(
    palette: dict[str, str], clamp_enabled: bool
) -> tuple[Mapping, tuple[Adjustment, ...]]:
    """The Applier's `_map`: faithful or clamped mapping + every adjustment.

    Faithful mode (`contrast_clamp = false`) is literal pass-through —
    `map_palette` verbatim, nothing adjusted, nothing logged.
    """
    if not clamp_enabled:
        return map_palette(palette), ()
    clamped, adjustments = clamp_palette(palette)
    mapping, on_tint = clamp_on_tint(map_palette(clamped), clamped)
    return mapping, adjustments + on_tint
