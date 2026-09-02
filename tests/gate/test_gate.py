"""Tier-2 gate: the fast live leg (ticket 22, docs/verification.md §Tier 2).

One session, one scratch Anki (dedicated base, dev-linked payload, the gate
control add-on), asserted live switches through the production
``omarchy theme set`` path (plus a decoupling flip at launch when the session
started on Catppuccin):

  Catppuccin (dark)          full surface asserts: deck browser + menubar,
                             reviewer, editor (Add screen, sveltekit)
  Gruvbox (dark)             the same-polarity critical path — Anki's own
                             apply early-returns here, so the add-on's
                             watcher leg is the only recolor; the deck canvas,
                             menubar and the Add window (kept open since
                             Catppuccin, no page rebuild) must be pixel-exact
  Catppuccin → Gruvbox       the orphan-skip leg (ticket 25): a planted
                             never-shown LegacyStatsWebView under the main
                             window must not change the restyled count
  Catppuccin Latte (light)   full surface asserts again on the light polarity
  Catppuccin (dark)          the polarity flip mid-review (ticket 26): the
                             flat toolbar's inline background copy must be
                             dropped and re-taken in place — no navigation
                             between the switch and the captures

Timing is recorded and asserted: de-gated 2026-08-30 (correctness first),
re-armed 2026-09-01 by the perf-polish ticket once the accumulated-view
plateau had a cause and a fix (see assert_timing). Contrast asserts are
computed from sampled render pixels (deck-name text × canvas), and every
pixel assert names its surface + sample point on failure.
"""

from __future__ import annotations

import json
import time

import pytest
from anki_theme.palette import VAR_RULES
from oracles import ThemeOracle, rgb
from points import sample as _sample
from sampling import Shot, contrast_ratio

pytestmark = pytest.mark.gate

# The per-leg costs every applied record decomposes into (runtime.apply).
LEG_NAMES = ("qt_chrome", "web_css", "engine_scripts", "theme_did_change", "open_pages")

# Re-armed thresholds (docs/verification.md §Thresholds). The 2026-08-30
# de-gating (correctness first) ended here, after the full-matrix plateau
# was root-caused as aqt-side, not ours (ticket 25's census diagnosis):
#  - aqt 26.08's DeckStats parents its webview to mw and never deletes it —
#    every stats open strands a never-shown LegacyStatsWebView that the
#    walk was evaling (now skipped; tier 2's orphan-skip leg);
#  - every Preferences open leaks a whole hidden dialog window (deleteLater
#    notwithstanding) and each stats open adds its spare to the tree, so
#    the widget tree grows with dialog use and aqt's own _apply_style
#    re-polish — the qt_chrome leg — grows with it (11 ms → ~900 ms over
#    the 22-theme matrix, all of it aqt's pipeline recoloring Qt chrome).
# So the split is by ownership: the delivery legs we own get tight budgets,
# asserted on every switch; the apply TOTAL gets a session-scale runaway
# bound only (the totals legitimately scale with the widget tree on this
# build, and one number must not gate both a 4-view tier-2 session and a
# 22-theme matrix tail).
OPEN_PAGES_BUDGET_MS = 100.0  # the restyle walk + evals (0.2–10 ms observed)
WEB_CSS_BUDGET_MS = 25.0  # css regen + atomic write (~0.3–1.5 ms)
ENGINE_SCRIPTS_BUDGET_MS = 25.0  # profile script upserts (~0.1–0.3 ms)
THEME_HOOK_BUDGET_MS = 50.0  # theme_did_change dispatch incl. our subscriber (~0.2–1.8)
APPLY_BUDGET_MS = 1500.0  # total incl. aqt's qt_chrome: runaway bound
SWAP_TO_APPLIED_BUDGET_MS = 2000.0  # 150 ms debounce + apply + poll granularity
STARTUP_APPLY_BUDGET_MS = 100.0  # the launch apply: 3–4 views, no dialogs yet
FRAME_INTERVAL_TOLERANCE_S = 0.35  # tier 3's frame-diff cross-check


def note_perf(session, row: dict) -> None:
    """Every switch's timing lands in the run's artifacts for the perf-log
    session row — the assert budgets above and the recorded numbers are the
    same data, one gated, one kept."""
    if session is None:
        return
    with (session.run_dir / "perf-switch-records.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")


# -- record asserts --------------------------------------------------------------


def perf_row(record: dict, t_swap: float, leg: str) -> dict:
    """The one switch-timing row every record assert appends."""
    return {
        "leg": leg,
        "apply_ms": record["apply_ms"],
        "swap_to_applied_ms": round((record["applied_at"] - t_swap) * 1000, 1),
        "polarity_flip": bool(record.get("polarity_flip")),
        "views": record.get("views"),
        "leg_ms": record.get("leg_ms"),
    }


def assert_timing(record: dict, t_swap: float, leg: str) -> None:
    """The re-armed thresholds, split by ownership (see the constants block):
    the delivery legs the add-on owns get tight budgets on every switch;
    the apply total gets a session-scale runaway bound, because aqt's own
    re-polish legitimately scales with the widget tree on this build."""
    legs = record["leg_ms"]
    for name, budget in (
        ("web_css", WEB_CSS_BUDGET_MS),
        ("engine_scripts", ENGINE_SCRIPTS_BUDGET_MS),
        ("theme_did_change", THEME_HOOK_BUDGET_MS),
        ("open_pages", OPEN_PAGES_BUDGET_MS),
    ):
        assert legs[name] <= budget, (
            f"switch leg {leg!r}: owned leg {name} {legs[name]}ms over its "
            f"{budget:.0f}ms budget — full breakdown {legs}"
        )
    assert record["apply_ms"] <= APPLY_BUDGET_MS, (
        f"switch leg {leg!r}: apply {record['apply_ms']}ms over the "
        f"{APPLY_BUDGET_MS:.0f}ms runaway bound ({record['views']} views) — "
        f"legs {legs}"
    )
    swap_ms = (record["applied_at"] - t_swap) * 1000
    assert swap_ms <= SWAP_TO_APPLIED_BUDGET_MS, (
        f"switch leg {leg!r}: swap→applied {swap_ms:.0f}ms over the "
        f"{SWAP_TO_APPLIED_BUDGET_MS:.0f}ms budget"
    )


def assert_switch_record(
    record: dict,
    t_swap: float,
    oracle: ThemeOracle,
    leg: str,
    session=None,
    expected_clamps: int = 0,
) -> dict:
    """The one record assert for switch legs, stock and pathological alike:
    `expected_clamps` carries tier-1's predicted clamp count for pathological
    palettes (0 for stock, where any adjustment is a finding)."""
    where = f"switch leg {leg!r}"
    assert record["errors"] == [], f"{where}: apply errors {record['errors']}"
    # Expected coverage is fixture-derived, never hardcoded: palettes lacking
    # keys (last-horizon, solitude have no brown/orange) legitimately map
    # fewer vars — the degrade-to-defaults policy riding `skipped`.
    assert record["vars"] == len(oracle.mapping.vars) and record["skipped"] == len(
        oracle.mapping.skipped
    ), (
        f"{where}: mapped {record['vars']}+{record['skipped']} vars, expected "
        f"{len(oracle.mapping.vars)}+{len(oracle.mapping.skipped)} "
        "(the fixture through the locked mapping)"
    )
    assert record["clamped"] == expected_clamps, (
        f"{where}: clamp adjusted {record['clamped']}, expected {expected_clamps}"
    )
    assert record["dark"] == oracle.dark, f"{where}: polarity mismatch"
    assert tuple(record["leg_ms"]) == LEG_NAMES, (
        f"{where}: per-leg breakdown missing or malformed: {record.get('leg_ms')}"
    )
    # Record before asserting: a red run must keep every leg's numbers —
    # the artifacts are the calibration data that explains the failure.
    note_perf(session, perf_row(record, t_swap, leg))
    assert_timing(record, t_swap, leg)
    assert record["engine_profiles"] >= 1, f"{where}: sveltekit leg dead (no profile scripted)"
    assert record["views"] >= 1, f"{where}: no open webview was restyled"
    return record


# -- surface asserts -----------------------------------------------------------


def assert_startup(session, record: dict | None) -> None:
    """Startup sanity, shared by both tiers' startup legs: the apply completed
    cleanly and inside the startup budget. The bootloader's sync check (a
    0.30 ms current-check per docs/performance.md) rides at import before it
    and is not separately observable from outside the process."""
    assert record is not None
    assert record["errors"] == [], f"startup apply errors: {record['errors']}"
    assert record["vars"] + record["skipped"] == len(VAR_RULES), (
        f"startup accounted {record['vars']}+{record['skipped']} vars, expected {len(VAR_RULES)}"
    )
    assert record["apply_ms"] <= STARTUP_APPLY_BUDGET_MS, (
        f"startup apply {record['apply_ms']}ms over the {STARTUP_APPLY_BUDGET_MS:.0f}ms budget"
    )
    note_perf(session, {"leg": "startup", "apply_ms": record["apply_ms"]})


def assert_deck_and_menubar(session, oracle: ThemeOracle, label: str) -> None:
    probe = session.probe("deck")
    shot = Shot(session.capture("main", label))
    canvas_xy, canvas = _sample(session, probe, "deck", "canvas", shot, oracle.canvas)
    _sample(session, probe, "deck", "row_fill", shot, oracle.current_row)
    _name_xy, name = _sample(session, probe, "deck", "deck_name", shot, oracle.fg, scan=True)
    # The menubar's fill is a scan, not a fixed point: window width varies
    # with the layout, so a fractional x could sit on a menu label's glyphs.
    _sample(session, probe, "menubar", "bg", shot, oracle.canvas, scan=True)
    _sample(session, probe, "menubar", "menu_text", shot, oracle.fg, scan=True)
    # The nav toolbar (ToolbarWebView — a stdHtml page, the surface the
    # 2026-08-30 clip caught the gate never sampling): the fancy bar paints
    # canvas-elevated in the deck state this assert runs in.
    _sample(session, probe, "menubar", "toolbar_bg", shot, oracle.canvas_elevated)

    # Contrast from the render itself (docs/verification.md): the sampled
    # text pixel vs the sampled fill pixel, at the floor the sample map
    # locks — the render-side proof the clamp's guarantee holds.
    spec = session.map["contrast"]["deck_name_vs_canvas"]
    ratio = contrast_ratio(name, canvas)
    floor = spec["floor"]
    assert ratio >= floor, (
        f"deck/deck_name x canvas contrast {ratio:.2f} < {floor} "
        f"(text rgb{name} at {_name_xy} vs fill rgb{canvas} at {canvas_xy}, {shot.path})"
    )


def assert_reviewer(session, oracle: ThemeOracle, label: str) -> None:
    probe = session.probe("review")
    shot = Shot(session.capture("main", label))
    # Characterized on 26.08.1: in night mode the reviewer page paints
    # --canvas; in light mode the page background mirrors the card's own
    # authored background (Anki blends the card into the page). Both
    # polarities prove theming left the notetype layer alone.
    card_face = rgb(session.seed["card_face_bg"])
    page = oracle.canvas if oracle.dark else card_face
    _sample(session, probe, "review", "canvas", shot, page)
    _sample(session, probe, "review", "card_face", shot, card_face)
    _sample(session, probe, "review", "button_fill", shot, oracle.button_fill)
    # The flat review toolbar is a translucent glass strip whose body wears
    # an inline copy of the reviewer page's computed background (aqt
    # TopWebView.update_background_image, refreshed only on the card page's
    # updateToolbar ping — ankitects/anki#5240): dark renders exactly canvas
    # (glass over canvas, verified delta<=3 across the dark stocks); light
    # composites over the card-mirrored page — notetype content,
    # characterized not oracle-able. The mid-review flip leg below pins the
    # re-copy that nothing in aqt performs.
    if oracle.dark:
        _sample(session, probe, "menubar", "toolbar_review", shot, oracle.canvas)


def assert_editor(session, oracle: ThemeOracle, label: str) -> None:
    probe = session.probe("add")
    shot = Shot(session.capture("add", label))
    _sample(session, probe, "add", "page_bg", shot, oracle.canvas)
    _sample(session, probe, "add", "input_fill", shot, oracle.canvas_elevated)
    _sample(session, probe, "add", "focus_ring", shot, oracle.focus_ring, scan=True)


# -- the legs ------------------------------------------------------------------


def test_startup_sanity(gate_session):
    assert_startup(gate_session, gate_session.startup_record)


def test_catppuccin_deck_and_menubar(gate_session):
    oracle = ThemeOracle("catppuccin")
    record, t_swap, _t_set_done = gate_session.switch("catppuccin")
    assert_switch_record(record, t_swap, oracle, "catppuccin full", gate_session)
    gate_session.cmd("show_deck")
    assert_deck_and_menubar(gate_session, oracle, "catppuccin")


def test_catppuccin_reviewer(gate_session):
    oracle = ThemeOracle("catppuccin")
    gate_session.cmd("show_review")
    assert_reviewer(gate_session, oracle, "catppuccin")


def test_catppuccin_editor(gate_session):
    oracle = ThemeOracle("catppuccin")
    gate_session.cmd("open_add")
    assert_editor(gate_session, oracle, "catppuccin")


def test_same_polarity_switch_gruvbox(gate_session):
    """The critical leg: Catppuccin → Gruvbox, dark → dark. Nothing navigates
    between the switch and the captures — the deck canvas, the menubar and
    the still-open Add window can only have recolored through the add-on's
    live legs (open-page eval + engine-script refresh)."""
    oracle = ThemeOracle("gruvbox")
    gate_session.cmd("show_deck")  # rebuilt under Catppuccin
    record, t_swap, _t_set_done = gate_session.switch("gruvbox")
    assert_switch_record(record, t_swap, oracle, "gruvbox same-polarity", gate_session)
    # Deck + menubar + toolbar pixel-exact against the Gruvbox oracle, no
    # rebuild between the switch and the captures.
    assert_deck_and_menubar(gate_session, oracle, "gruvbox-live")
    # The Add window (open since the Catppuccin leg) recolored live too.
    add_probe = gate_session.probe("add")
    add_shot = Shot(gate_session.capture("add", "gruvbox-live"))
    _sample(gate_session, add_probe, "add", "page_bg", add_shot, oracle.canvas)


def test_hidden_main_window_spares_skipped(gate_session):
    """Ticket 25: aqt's DeckStats parents its webview to the MAIN window and
    never deletes it — every stats open leaks one never-shown chart-heavy
    page there, and evaling each cost ~50 ms on every later apply (the
    matrix plateau: 12 ms @ 5 views → ~1 s @ 26). Hidden children of mw are
    never-shown spares (mw's own webviews are visible in every state and
    rebuild through will_set_content on state changes), so the walk skips
    them; hidden views of other windows (open dialogs' inactive tabs) still
    eval. Proven with the leak's exact artifact: a planted never-shown
    LegacyStatsWebView under mw must not change the restyled count."""
    session = gate_session
    session.cmd("show_deck")
    before = session.switch("catppuccin")[0]["views"]
    planted = session.cmd("plant_hidden_webview")
    assert planted["ok"], planted
    record, t_swap, _t_set_done = session.switch("gruvbox")
    assert_switch_record(record, t_swap, ThemeOracle("gruvbox"), "orphan-skip", session)
    assert record["views"] == before, (
        f"the walk restyled {record['views']} views after planting the hidden "
        f"mw-child ({before} before) — leaked DeckStats spares are being eval'd"
    )
    # Skip, not absence: the planted view still exists and the census (the
    # walk's own expression) marks it would-not-eval.
    census = session.cmd("census")
    rows = [r for r in census["rows"] if r["object_name"] == "gate_planted_hidden"]
    assert rows and not rows[0]["would_eval"], (
        f"planted view not in census as a skipped mw-child: {rows}"
    )


def test_latte_all_surfaces(gate_session):
    oracle = ThemeOracle("catppuccin-latte")
    record, t_swap, _t_set_done = gate_session.switch("catppuccin-latte")
    assert_switch_record(record, t_swap, oracle, "latte full", gate_session)
    gate_session.cmd("show_deck")
    assert_deck_and_menubar(gate_session, oracle, "latte")
    gate_session.cmd("show_review")
    assert_reviewer(gate_session, oracle, "latte")
    assert_editor(gate_session, oracle, "latte")


def test_polarity_flip_mid_review(gate_session):
    """The field-found hole (ticket 26, ankitects/anki#5240): the flat
    toolbar's body wears an inline copy of the reviewer page's computed
    background, refreshed by aqt only on the card page's updateToolbar ping
    — a light→dark flip while sitting in the reviewer left the strip in the
    old polarity's composite. Flip under Latte to Catppuccin and assert in
    place: with no navigation between the switch and the captures, the
    toolbar strip can only be the dark composite if the runtime dropped and
    re-took the copy."""
    oracle = ThemeOracle("catppuccin")
    gate_session.cmd("show_review")  # flatten + light inline composite
    record, t_swap, _t_set_done = gate_session.switch("catppuccin")
    assert_switch_record(record, t_swap, oracle, "catppuccin mid-review flip", gate_session)
    # The re-copy is deliberate: it waits out Anki's 180 ms transition
    # window (runtime REVIEW_TOOLBAR_COPY_MS) so the strip is sampled in
    # its settled composite, not the mid-flight blend.
    time.sleep(0.4)
    assert_reviewer(gate_session, oracle, "catppuccin-mid-review")
