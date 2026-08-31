"""Tier-2 gate: the fast live leg (ticket 22, docs/verification.md §Tier 2).

One session, one scratch Anki (dedicated base, dev-linked payload, the gate
control add-on), four asserted live switches through the production
``omarchy theme set`` path (plus a decoupling flip at launch when the session
started on Catppuccin):

  Catppuccin (dark)          full surface asserts: deck browser + menubar,
                             reviewer, editor (Add screen, sveltekit)
  Gruvbox (dark)             the same-polarity critical path — Anki's own
                             apply early-returns here, so the add-on's
                             watcher leg is the only recolor; the deck canvas,
                             menubar and the Add window (kept open since
                             Catppuccin, no page rebuild) must be pixel-exact
  Catppuccin Latte (light)   full surface asserts again on the light polarity
  Catppuccin (dark)          the polarity flip mid-review (ticket 26): the
                             flat toolbar's inline background copy must be
                             dropped and re-taken in place — no navigation
                             between the switch and the captures

Timing is recorded, never gated (user directive 2026-08-30): every switch's
apply cost and swap→applied latency land in the run's artifacts for the
perf-log session; thresholds return with the perf-polish ticket once
correctness is green. Contrast asserts are computed from sampled render
pixels (deck-name text × canvas), and every pixel assert names its surface +
sample point on failure.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(GATE_DIR))

from ankiya.palette import VAR_RULES  # noqa: E402
from oracles import ThemeOracle, rgb  # noqa: E402
from points import sample as _sample  # noqa: E402
from sampling import Shot, contrast_ratio  # noqa: E402

pytestmark = pytest.mark.gate


def note_perf(session, row: dict) -> None:
    """Timing is recorded, never gated (user directive 2026-08-30): switch
    costs land in the run's artifacts for the perf-log session row; thresholds
    return with the perf-polish ticket once correctness is green."""
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
    }


def assert_switch_record(
    record: dict, t_swap: float, oracle: ThemeOracle, leg: str, session=None
) -> dict:
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
    assert record["clamped"] == 0, f"{where}: clamp adjusted a stock palette"
    assert record["dark"] == oracle.dark, f"{where}: polarity mismatch"
    note_perf(session, perf_row(record, t_swap, leg))
    assert record["engine_profiles"] >= 1, f"{where}: sveltekit leg dead (no profile scripted)"
    assert record["views"] >= 1, f"{where}: no open webview was restyled"
    return record


# -- surface asserts -----------------------------------------------------------


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
    _sample(session, probe, "add", "input_fill", shot, oracle.editor_input_fill)
    _sample(session, probe, "add", "focus_ring", shot, oracle.focus_ring, scan=True)


# -- the legs ------------------------------------------------------------------


def test_startup_sanity(gate_session):
    """Startup sanity: the apply completed cleanly. The bootloader's sync
    check (a 0.30 ms current-check per docs/performance.md) rides at import
    before it and is not separately observable from outside the process."""
    record = gate_session.startup_record
    assert record is not None
    assert record["errors"] == [], f"startup apply errors: {record['errors']}"
    assert record["vars"] + record["skipped"] == len(VAR_RULES), (
        f"startup accounted {record['vars']}+{record['skipped']} vars, expected {len(VAR_RULES)}"
    )
    note_perf(gate_session, {"leg": "startup", "apply_ms": record["apply_ms"]})


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
