"""Tier 1 — the clamp (wayfinder ticket 08 → implementation 18).

Fixtures P1–P5 are the synthetic pathological palettes ticket 08 seeded:
one per guard plus the two infeasible-background shapes. Stock palettes
appear as the regression that keeps the floors stock-invisible.
"""

import colorsys

import pytest
from ankiya.palette import contrast_ratio, map_palette
from ankiya.theme_clamp import (
    BLACK,
    FG_FLOOR,
    LINK_FLOOR,
    ON_TINT_FLOOR,
    clamp_on_tint,
    clamp_palette,
    map_with_clamp,
)
from theme_fixtures import THEMES, theme_palette


def palette(**overrides: str) -> dict[str, str]:
    """A healthy catppuccin-shaped base the clamp never touches."""
    base = {
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
    base.update(overrides)
    return base


def hls(color: str) -> tuple[float, float, float]:
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)


def assert_nudge_preserved_hue_and_sat(old: str, new: str) -> None:
    h0, l0, s0 = hls(old)
    h1, l1, s1 = hls(new)
    delta_h = min(abs(h0 - h1), 1 - abs(h0 - h1))
    assert delta_h < 0.02, (old, new)
    assert abs(s0 - s1) < 0.05, (old, new)
    assert l0 != l1, "the nudge did not move"


# The pathological fixtures.

# P1 — foreground invisible on a (dark) background trio: feasible, the nudge
# lifts it to the 4.5 floor against the lightest background. Selection/accent
# are light so the on-tint pass stays quiet (the chained case is its own test).
P1 = palette(foreground="#202030", selection="#89b4fa")

# The chained fixture: P1 with the base mid-dark selection — the nudged
# foreground still cannot read on it, so guard 3 extends afterwards.
CHAIN = palette(foreground="#202030")

# P2 — link invisible on the background (link == background exactly). The
# background family is all light so the dark foreground clears guard 1 and
# only the link guard fires.
P2 = palette(
    foreground="#1e1e2e",
    background="#89b4fa",
    dark_background="#7aa3f0",
    darker_background="#6b93e6",
    blue="#89b4fa",
    bright_blue="#89b4fa",
)

# P3 — mid-luminance accent and selection: both base on-tint candidates fall
# below 3.0, so the amendment extends the candidates; nothing else fails.
P3 = palette(
    foreground="#d0d0d0",
    background="#4a4a4a",
    dark_background="#2a2a2a",
    darker_background="#1a1a1a",
    selection="#909090",
    accent="#909090",
)

# P4 — straddling backgrounds: two light + one dark, no foreground clears 4.5
# against all three (pairwise band violated) → max-min + the honest log line.
P4 = palette(
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
P5 = palette(
    foreground="#808080",
    background="#ffffff",
    dark_background="#595959",
    darker_background="#010101",
    selection="#2a2a2a",
    accent="#2a2a2a",
    blue="#1a1a1a",
    bright_blue="#1a1a1a",
)


# Stock invisibility — the floors hold below every stock palette.


@pytest.mark.parametrize("theme", THEMES)
def test_stock_palette_clamps_nothing(theme: str) -> None:
    original = theme_palette(theme)
    result = clamp_palette(original)
    assert result.adjustments == ()
    assert result.palette == original
    mapping, adjustments = map_with_clamp(original, True)
    assert adjustments == ()
    assert mapping == map_palette(original)


def test_healthy_base_clamps_nothing() -> None:
    assert clamp_palette(palette()).adjustments == ()
    mapping, adjustments = map_with_clamp(palette(), True)
    assert adjustments == ()
    assert mapping == map_palette(palette())


# P1 — core foreground guard.


def test_p1_foreground_lifted_to_the_4_5_floor() -> None:
    result = clamp_palette(P1)
    assert len(result.adjustments) == 1
    adjustment = result.adjustments[0]
    assert adjustment.key == "foreground"
    assert adjustment.old == P1["foreground"]
    assert adjustment.floor == FG_FLOOR
    assert not adjustment.unsatisfiable
    new = result.palette["foreground"]
    assert adjustment.new == new
    assert_nudge_preserved_hue_and_sat(adjustment.old, new)
    # The three backgrounds, in guard order — all clear 4.5, the lightest
    # (background) is the binding one and lands on the floor.
    fills = ("background", "dark_background", "darker_background")
    after = tuple(contrast_ratio(new, P1[key]) for key in fills)
    assert adjustment.after == after
    assert min(after) >= FG_FLOOR
    assert after[0] == min(after)
    assert after[0] <= 4.6
    # Backgrounds byte-identical; only the foreground moved.
    assert result.palette == {**P1, "foreground": new}


def test_p1_log_line_carries_key_old_new_ratios_floor() -> None:
    (adjustment,) = clamp_palette(P1).adjustments
    line = adjustment.line()
    assert "foreground" in line
    assert P1["foreground"] in line
    assert clamp_palette(P1).palette["foreground"] in line
    assert "@4.5" in line
    assert "vs background/dark_background/darker_background" in line
    assert "→" in line


def test_nudged_foreground_is_re_measured_by_the_on_tint_pass() -> None:
    """The chain: guard 1 lifts the foreground, and guard 3 then evaluates
    the on-tint picks *of the clamped palette* — the nudged value is the
    candidate that fails and gets replaced."""
    assert len(clamp_palette(CHAIN).adjustments) == 1
    nudged = clamp_palette(CHAIN).palette["foreground"]
    mapping, adjustments = map_with_clamp(CHAIN, True)
    assert [a.key for a in adjustments] == [
        "foreground",
        "SELECTED_FG",
        "HIGHLIGHT_FG",
    ]
    for adjustment in adjustments[1:]:
        assert adjustment.old == nudged  # the locked pick was the nudged fg
        assert adjustment.new == "#ffffff"
        assert min(adjustment.before) < ON_TINT_FLOOR
        assert min(adjustment.after) > 4.5
    assert mapping.vars["SELECTED_FG"] == "#ffffff"


# P2 — link guard.


def test_p2_link_lifted_to_the_3_0_floor() -> None:
    result = clamp_palette(P2)
    assert len(result.adjustments) == 1
    adjustment = result.adjustments[0]
    assert adjustment.key == "bright_blue"
    assert adjustment.old == P2["bright_blue"]
    assert adjustment.floor == LINK_FLOOR
    assert not adjustment.unsatisfiable
    new = result.palette["bright_blue"]
    assert_nudge_preserved_hue_and_sat(adjustment.old, new)
    # The background is lighter than the link, so the nudge darkens it.
    assert hls(new)[1] < hls(adjustment.old)[1]
    assert LINK_FLOOR <= contrast_ratio(new, P2["background"]) <= 3.15


def test_link_key_choice_keeps_flags_verbatim_when_possible() -> None:
    # Distinct bright_blue fails → it is adjusted; blue (STATE_NEW, FLAG_4)
    # stays verbatim.
    distinct = palette(
        background="#f5f5f5",
        dark_background="#e8e8e8",
        darker_background="#dcdcdc",
        foreground="#303030",
        blue="#1a1a1a",
        bright_blue="#eeeeee",
    )
    result = clamp_palette(distinct)
    assert [a.key for a in result.adjustments] == ["bright_blue"]
    assert result.palette["blue"] == "#1a1a1a"
    # Equal pair (the `white` shape): bright_blue is split off so blue and
    # the flags keep the stock value while the link is fixed.
    equal = dict(distinct)
    equal["bright_blue"] = equal["blue"] = "#eeeeee"
    result = clamp_palette(equal)
    assert [a.key for a in result.adjustments] == ["bright_blue"]
    assert result.palette["blue"] == "#eeeeee"
    mapping, _ = map_with_clamp(result.palette, True)
    assert mapping.vars["FG_LINK"] == result.palette["bright_blue"]
    assert mapping.vars["STATE_NEW"] == "#eeeeee"
    # Sole source: blue itself is adjusted — the log line names it, and the
    # shared key rides: STATE_NEW/FLAG_4 take the nudged value too. That is
    # the deliberate resolution of ticket 08's states/flags-verbatim clause
    # against its link-guard table (a sole-source link has no other key).
    sole = dict(distinct)
    del sole["bright_blue"]
    sole["blue"] = "#eeeeee"
    result = clamp_palette(sole)
    assert [a.key for a in result.adjustments] == ["blue"]
    assert result.adjustments[0].detail == "sole link source"
    mapping, _ = map_with_clamp(result.palette, True)
    assert mapping.vars["STATE_NEW"] == result.palette["blue"]
    assert mapping.vars["FLAG_4"] == result.palette["blue"]


# P3 — on-tint amendment (the mapping post-pass).


def test_p3_mid_luminance_fill_extends_on_tint_candidates() -> None:
    # The pre-pass guards are all satisfied — P3 isolates guard 3.
    assert clamp_palette(P3).adjustments == ()
    fill = P3["selection"]
    locked_pick = map_palette(P3).vars["SELECTED_FG"]
    assert contrast_ratio(locked_pick, fill) < ON_TINT_FLOOR
    mapping, adjustments = map_with_clamp(P3, True)
    assert len(adjustments) == 3
    assert [a.key for a in adjustments] == ["SELECTED_FG", "HIGHLIGHT_FG", "on_accent"]
    for adjustment in adjustments:
        assert adjustment.old == locked_pick
        assert adjustment.new == BLACK
        assert adjustment.detail == "extended candidates"
        assert min(adjustment.before) < ON_TINT_FLOOR
        assert min(adjustment.after) > 4.5  # white/black clear 4.58:1 on any fill
    assert mapping.vars["SELECTED_FG"] == BLACK
    assert mapping.vars["HIGHLIGHT_FG"] == BLACK
    assert mapping.on_accent == BLACK
    # Everything the guards don't own is verbatim.
    assert mapping.vars["FG"] == P3["foreground"]
    assert mapping.vars["CANVAS"] == P3["background"]


def test_p3_faithful_mode_keeps_the_locked_pick() -> None:
    mapping, adjustments = map_with_clamp(P3, False)
    assert adjustments == ()
    assert mapping == map_palette(P3)
    assert contrast_ratio(mapping.vars["SELECTED_FG"], P3["selection"]) < ON_TINT_FLOOR


def test_clamp_on_tint_rewrites_only_the_failing_slots() -> None:
    """The post-pass in isolation: the mapping object it returns differs
    from its input only in the failing on-tint slots — everything else,
    including healthy vars and the bootstrap extras, is carried over."""
    locked = map_palette(P3)
    clamped, adjustments = clamp_on_tint(locked, P3)
    assert clamped is not locked
    assert clamped.vars == {**locked.vars, "SELECTED_FG": BLACK, "HIGHLIGHT_FG": BLACK}
    assert clamped.bootstrap == locked.bootstrap
    assert clamped.on_accent == BLACK
    assert len(adjustments) == 3


# P4 / P5 — infeasible backgrounds: max-min foreground + the honest log.


@pytest.mark.parametrize("fixture", [P4, P5], ids=["straddling", "dead-zone-trio"])
def test_infeasible_backgrounds_take_max_min_foreground(fixture: dict) -> None:
    result = clamp_palette(fixture)
    assert len(result.adjustments) == 1
    adjustment = result.adjustments[0]
    assert adjustment.key == "foreground"
    assert adjustment.unsatisfiable
    assert "AA unsatisfiable across backgrounds, max-min chosen" in adjustment.line()
    new = result.palette["foreground"]
    assert_nudge_preserved_hue_and_sat(adjustment.old, new)
    # The chosen point maximizes the minimum contrast: better than before,
    # but honestly short of AA — it never claims satisfaction.
    assert min(adjustment.after) > min(adjustment.before)
    assert min(adjustment.after) < FG_FLOOR
    assert result.palette == {**fixture, "foreground": new}


def test_p4_p5_max_min_points() -> None:
    # Analytic max-min values: P4 balances #d0d0d0/#303030 ≈ 2.92; P5
    # balances #595959/#ffffff ≈ 2.65 (both computed from the WCAG formula).
    p4 = clamp_palette(P4).adjustments[0].after
    assert 2.8 <= min(p4) <= 3.05
    p5 = clamp_palette(P5).adjustments[0].after
    assert 2.5 <= min(p5) <= 2.75


# Termination — the per-fill bound.


def test_mid_gray_on_mid_gray_terminates_at_the_dark_extreme() -> None:
    """The mechanics-3 edge case: fill at the worst luminance, foreground
    equal to it. Only near-black clears 4.5 (white manages 3.95), so the
    nudge desaturates toward the extreme and stops at the floor."""
    gray = palette(
        foreground="#808080",
        background="#808080",
        dark_background="#808080",
        darker_background="#808080",
        selection="#1a1a1a",
        accent="#1a1a1a",
        blue="#1a1a1a",
        bright_blue="#1a1a1a",
    )
    result = clamp_palette(gray)
    (adjustment,) = result.adjustments
    assert not adjustment.unsatisfiable
    new = result.palette["foreground"]
    assert hls(new)[1] < hls("#808080")[1]  # the dark extreme is the only side
    for key in ("background", "dark_background", "darker_background"):
        assert contrast_ratio(new, gray[key]) >= FG_FLOOR


def test_missing_keys_leave_their_guards_silent() -> None:
    assert clamp_palette({}).adjustments == ()
    assert clamp_palette({"foreground": "#808080"}).palette == {"foreground": "#808080"}
    # Link guard silent without a background to measure against.
    no_bg = palette(blue="#eeeeee", bright_blue="#eeeeee")
    del no_bg["background"]
    assert clamp_palette(no_bg).adjustments == ()
    # Degraded on-tint slots (no fill) are skipped by the post-pass.
    mapping, adjustments = map_with_clamp({"foreground": "#d0d0d0"}, True)
    assert adjustments == ()
    assert "SELECTED_FG" not in mapping.vars


# Faithful mode.


def test_faithful_mode_adjusts_nothing() -> None:
    for fixture in (P1, P2, P3, P4, P5):
        mapping, adjustments = map_with_clamp(fixture, False)
        assert adjustments == ()
        assert mapping == map_palette(fixture)
    # The hostile values render verbatim — literal pass-through.
    assert map_with_clamp(P1, False)[0].vars["FG"] == P1["foreground"]


# The seam used by the runtime, end to end on one fixture.


def test_map_with_clamp_reports_every_adjustment_once() -> None:
    # P1 triggers one guard; each adjustment is reported exactly once, with
    # the mapping built from the clamped palette.
    result = clamp_palette(P1)
    mapping, adjustments = map_with_clamp(P1, True)
    assert adjustments == result.adjustments
    assert mapping.vars["FG"] == result.palette["foreground"]
    keys = [a.key for a in adjustments]
    assert len(keys) == len(set(keys))
