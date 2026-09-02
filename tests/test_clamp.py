"""Tier 1 — the clamp (wayfinder ticket 08 → implementation 18).

The P1–P5 fixtures live in pathological.py, shared verbatim with tier 3's
render legs (the gate asserts the render against this module's prediction).
"""

import colorsys

import pytest
from anki_theme.palette import contrast_ratio, map_palette
from anki_theme.theme_clamp import (
    BLACK,
    FG_FLOOR,
    LINK_FLOOR,
    ON_TINT_FLOOR,
    clamp_on_tint,
    clamp_palette,
    map_with_clamp,
)
from pathological import CHAIN, P1, P2, P3, P4, P5, base_palette
from theme_fixtures import THEMES, theme_palette


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


# The fixtures themselves are pathological.py's — imported above. The local
# name keeps the historical call sites readable.
palette = base_palette


# Stock invisibility — the floors hold below every stock palette.


@pytest.mark.parametrize("theme", THEMES)
def test_stock_palette_clamps_nothing(theme: str) -> None:
    original = theme_palette(theme)
    clamped, adjustments = clamp_palette(original)
    assert adjustments == ()
    assert clamped == original
    mapping, adjustments = map_with_clamp(original, True)
    assert adjustments == ()
    assert mapping == map_palette(original)


def test_healthy_base_clamps_nothing() -> None:
    assert clamp_palette(palette())[1] == ()
    mapping, adjustments = map_with_clamp(palette(), True)
    assert adjustments == ()
    assert mapping == map_palette(palette())


# P1 — core foreground guard.


def test_p1_foreground_lifted_to_the_4_5_floor() -> None:
    clamped, adjustments = clamp_palette(P1)
    assert len(adjustments) == 1
    adjustment = adjustments[0]
    assert adjustment.key == "foreground"
    assert adjustment.old == P1["foreground"]
    assert adjustment.floor == FG_FLOOR
    assert not adjustment.unsatisfiable
    new = clamped["foreground"]
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
    assert clamped == {**P1, "foreground": new}


def test_p1_log_line_carries_key_old_new_ratios_floor() -> None:
    (adjustment,) = clamp_palette(P1)[1]
    line = adjustment.line()
    assert "foreground" in line
    assert P1["foreground"] in line
    assert clamp_palette(P1)[0]["foreground"] in line
    assert "@4.5" in line
    assert "vs background/dark_background/darker_background" in line
    assert "→" in line


def test_nudged_foreground_is_re_measured_by_the_on_tint_pass() -> None:
    """The chain: guard 1 lifts the foreground, and guard 3 then evaluates
    the on-tint picks *of the clamped palette* — the nudged value is the
    candidate that fails and gets replaced."""
    assert len(clamp_palette(CHAIN)[1]) == 1
    nudged = clamp_palette(CHAIN)[0]["foreground"]
    mapping, adjustments = map_with_clamp(CHAIN, True)
    assert [a.key for a in adjustments] == [
        "foreground",
        "SELECTED_FG",
        "HIGHLIGHT_FG",
        "FG_DISABLED",
    ]
    for adjustment in adjustments[1:]:
        assert adjustment.old == nudged  # the locked pick was the nudged fg
        assert adjustment.new == "#ffffff"
        assert min(adjustment.before) < ON_TINT_FLOOR
        assert min(adjustment.after) > 4.5
    assert mapping.vars["SELECTED_FG"] == "#ffffff"


# P2 — link guard.


def test_p2_link_lifted_to_the_3_0_floor() -> None:
    clamped, adjustments = clamp_palette(P2)
    assert len(adjustments) == 1
    adjustment = adjustments[0]
    assert adjustment.key == "bright_blue"
    assert adjustment.old == P2["bright_blue"]
    assert adjustment.floor == LINK_FLOOR
    assert not adjustment.unsatisfiable
    new = clamped["bright_blue"]
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
    clamped, adjustments = clamp_palette(distinct)
    assert [a.key for a in adjustments] == ["bright_blue"]
    assert clamped["blue"] == "#1a1a1a"
    # Equal pair (the `white` shape): bright_blue is split off so blue and
    # the flags keep the stock value while the link is fixed.
    equal = dict(distinct)
    equal["bright_blue"] = equal["blue"] = "#eeeeee"
    clamped, adjustments = clamp_palette(equal)
    assert [a.key for a in adjustments] == ["bright_blue"]
    assert clamped["blue"] == "#eeeeee"
    mapping, _ = map_with_clamp(clamped, True)
    assert mapping.vars["FG_LINK"] == clamped["bright_blue"]
    assert mapping.vars["STATE_NEW"] == "#eeeeee"
    # Sole source: blue itself is adjusted — the log line names it, and the
    # shared key rides: STATE_NEW/FLAG_4 take the nudged value too. That is
    # the deliberate resolution of ticket 08's states/flags-verbatim clause
    # against its link-guard table (a sole-source link has no other key).
    sole = dict(distinct)
    del sole["bright_blue"]
    sole["blue"] = "#eeeeee"
    clamped, adjustments = clamp_palette(sole)
    assert [a.key for a in adjustments] == ["blue"]
    assert adjustments[0].detail == "sole link source"
    mapping, _ = map_with_clamp(clamped, True)
    assert mapping.vars["STATE_NEW"] == clamped["blue"]
    assert mapping.vars["FLAG_4"] == clamped["blue"]


# P3 — on-tint amendment (the mapping post-pass).


def test_p3_mid_luminance_fill_extends_on_tint_candidates() -> None:
    # The pre-pass guards are all satisfied — P3 isolates guard 3.
    assert clamp_palette(P3)[1] == ()
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


def test_fg_disabled_extends_candidates_on_a_straddling_composite() -> None:
    """Ledger row 7's guard (ticket 25): a palette whose fg/bg both
    straddle the muted composite — no on-tint pick reads on it, so the
    post-pass extends to the extremes (black wins on a mid-gray fill)."""
    hostile = base_palette(
        foreground="#8a8a8a",
        background="#767676",
        muted="#808080",
        selection="#1a1a1a",
        accent="#1a1a1a",
        blue="#1a1a1a",
        bright_blue="#1a1a1a",
    )
    locked = map_palette(hostile)
    composite = "#7b7b7b"  # muted@0.5 over background
    assert contrast_ratio(locked.vars["FG_DISABLED"], composite) < ON_TINT_FLOOR
    clamped, adjustments = clamp_on_tint(locked, hostile)
    (adjustment,) = [a for a in adjustments if a.key == "FG_DISABLED"]
    assert adjustment.new == BLACK
    assert adjustment.relationship == "on muted@0.5 over background"
    assert clamped.vars["FG_DISABLED"] == BLACK


# P4 / P5 — infeasible backgrounds: max-min foreground + the honest log.


@pytest.mark.parametrize("fixture", [P4, P5], ids=["straddling", "dead-zone-trio"])
def test_infeasible_backgrounds_take_max_min_foreground(fixture: dict) -> None:
    clamped, adjustments = clamp_palette(fixture)
    assert len(adjustments) == 1
    adjustment = adjustments[0]
    assert adjustment.key == "foreground"
    assert adjustment.unsatisfiable
    assert "AA unsatisfiable across backgrounds, max-min chosen" in adjustment.line()
    new = clamped["foreground"]
    assert_nudge_preserved_hue_and_sat(adjustment.old, new)
    # The chosen point maximizes the minimum contrast: better than before,
    # but honestly short of AA — it never claims satisfaction.
    assert min(adjustment.after) > min(adjustment.before)
    assert min(adjustment.after) < FG_FLOOR
    assert clamped == {**fixture, "foreground": new}


def test_p4_p5_max_min_points() -> None:
    # Analytic max-min values: P4 balances #d0d0d0/#303030 ≈ 2.92; P5
    # balances #595959/#ffffff ≈ 2.65 (both computed from the WCAG formula).
    p4 = clamp_palette(P4)[1][0].after
    assert 2.8 <= min(p4) <= 3.05
    p5 = clamp_palette(P5)[1][0].after
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
    clamped, (adjustment,) = clamp_palette(gray)
    assert not adjustment.unsatisfiable
    new = clamped["foreground"]
    assert hls(new)[1] < hls("#808080")[1]  # the dark extreme is the only side
    for key in ("background", "dark_background", "darker_background"):
        assert contrast_ratio(new, gray[key]) >= FG_FLOOR


def test_missing_keys_leave_their_guards_silent() -> None:
    assert clamp_palette({})[1] == ()
    assert clamp_palette({"foreground": "#808080"})[0] == {"foreground": "#808080"}
    # Link guard silent without a background to measure against.
    no_bg = palette(blue="#eeeeee", bright_blue="#eeeeee")
    del no_bg["background"]
    assert clamp_palette(no_bg)[1] == ()
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
    # P1 triggers guard 1, and its nudged foreground then misses the 3.0
    # bar on the muted composite — the chained FG_DISABLED extension rides
    # along; each adjustment is reported exactly once, with the mapping
    # built from the clamped palette.
    clamped, _ = clamp_palette(P1)
    mapping, adjustments = map_with_clamp(P1, True)
    assert [a.key for a in adjustments] == ["foreground", "FG_DISABLED"]
    assert len({a.key for a in adjustments}) == len(adjustments)
    assert mapping.vars["FG"] == clamped["foreground"]
    assert mapping.vars["FG_DISABLED"] == "#ffffff"
