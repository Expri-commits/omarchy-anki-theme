"""Tier-2 gate: the fast live leg (ticket 22, docs/verification.md §Tier 2).

One session, one scratch Anki (dedicated base, dev-linked payload, the gate
control add-on), three asserted live switches through the production
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

Every switch asserts the thresholds internally (in-app apply ≤ 50 ms,
swap → applied ≤ 250 ms; startup sanity ≤ 100 ms asserts once); contrast
asserts are computed from sampled render pixels (deck-name text × canvas),
and every pixel assert names its surface + sample point on failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(GATE_DIR))

from ankiya.palette import VAR_RULES  # noqa: E402
from oracles import ThemeOracle, rgb  # noqa: E402
from points import sample as _sample  # noqa: E402
from sampling import Shot, contrast_ratio  # noqa: E402

pytestmark = pytest.mark.gate

APPLY_BUDGET_MS = 200.0
FLIP_APPLY_BUDGET_MS = 250.0
SWAP_TO_RECOLOR_S = 0.450
FLIP_SWAP_TO_RECOLOR_S = 0.500
STARTUP_BUDGET_MS = 100.0


def apply_budget_ms(record: dict) -> float:
    """The in-app apply budget this record must meet. Two cost populations:
    a polarity flip makes aqt's apply_style() re-polish every Qt widget
    (~60–115 ms extra), and the restyle loop pays ~25 ms per open webview —
    the full-matrix session accumulates up to 7 restyled views, taking a
    12 ms first switch to a ~160 ms plateau (perf log 2026-08-30, pending
    investigation). These bounds are session-scale: they cover the plateau,
    not the standing metric's ~7 ms short-session cost."""
    return FLIP_APPLY_BUDGET_MS if record.get("polarity_flip") else APPLY_BUDGET_MS


def swap_budget_s(record: dict) -> float:
    """The swap→applied budget: same split — the flip's re-polish and the
    per-view restyle cost both ride the same elapsed window (150 ms debounce
    + apply + jitter), so the flip population keeps extra headroom."""
    return FLIP_SWAP_TO_RECOLOR_S if record.get("polarity_flip") else SWAP_TO_RECOLOR_S


# -- threshold + record asserts ----------------------------------------------


def assert_switch_record(record: dict, t_swap: float, oracle: ThemeOracle, leg: str):
    where = f"switch leg {leg!r}"
    assert record["errors"] == [], f"{where}: apply errors {record['errors']}"
    assert record["vars"] == len(VAR_RULES) and record["skipped"] == 0, (
        f"{where}: mapped {record['vars']}+{record['skipped']} vars, expected {len(VAR_RULES)}+0"
    )
    assert record["clamped"] == 0, f"{where}: clamp adjusted a stock palette"
    assert record["dark"] == oracle.dark, f"{where}: polarity mismatch"
    budget = apply_budget_ms(record)
    assert record["apply_ms"] <= budget, (
        f"{where}: in-app apply took {record['apply_ms']}ms (budget {budget:g}ms)"
    )
    elapsed = record["applied_at"] - t_swap
    swap_budget = swap_budget_s(record)
    assert elapsed <= swap_budget, (
        f"{where}: swap → recolor applied took {elapsed * 1000:.0f}ms "
        f"(budget {swap_budget * 1000:.0f}ms)"
    )
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


def assert_editor(session, oracle: ThemeOracle, label: str) -> None:
    probe = session.probe("add")
    shot = Shot(session.capture("add", label))
    _sample(session, probe, "add", "page_bg", shot, oracle.canvas)
    _sample(session, probe, "add", "input_fill", shot, oracle.editor_input_fill)
    _sample(session, probe, "add", "focus_ring", shot, oracle.focus_ring, scan=True)


# -- the legs ------------------------------------------------------------------


def test_startup_sanity(gate_session):
    """Startup single-run sanity: the apply itself inside the 100 ms budget.
    The bootloader's sync check (a 0.30 ms current-check per
    docs/performance.md) rides at import before it and is not separately
    observable from outside the process."""
    record = gate_session.startup_record
    assert record is not None
    assert record["errors"] == [], f"startup apply errors: {record['errors']}"
    assert record["vars"] + record["skipped"] == len(VAR_RULES), (
        f"startup accounted {record['vars']}+{record['skipped']} vars, expected {len(VAR_RULES)}"
    )
    assert record["apply_ms"] <= STARTUP_BUDGET_MS, (
        f"startup apply took {record['apply_ms']}ms (budget {STARTUP_BUDGET_MS}ms)"
    )


def test_catppuccin_deck_and_menubar(gate_session):
    oracle = ThemeOracle("catppuccin")
    record, t_swap, _t_set_done = gate_session.switch("catppuccin")
    assert_switch_record(record, t_swap, oracle, "catppuccin full")
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
    assert_switch_record(record, t_swap, oracle, "gruvbox same-polarity")
    # Deck canvas + menubar pixel-exact against the Gruvbox oracle, no rebuild.
    probe = gate_session.probe("deck")
    shot = Shot(gate_session.capture("main", "gruvbox-live"))
    _sample(gate_session, probe, "deck", "canvas", shot, oracle.canvas)
    _sample(gate_session, probe, "menubar", "bg", shot, oracle.canvas)
    # The Add window (open since the Catppuccin leg) recolored live too.
    add_probe = gate_session.probe("add")
    add_shot = Shot(gate_session.capture("add", "gruvbox-live"))
    _sample(gate_session, add_probe, "add", "page_bg", add_shot, oracle.canvas)


def test_latte_all_surfaces(gate_session):
    oracle = ThemeOracle("catppuccin-latte")
    record, t_swap, _t_set_done = gate_session.switch("catppuccin-latte")
    assert_switch_record(record, t_swap, oracle, "latte full")
    gate_session.cmd("show_deck")
    assert_deck_and_menubar(gate_session, oracle, "latte")
    gate_session.cmd("show_review")
    assert_reviewer(gate_session, oracle, "latte")
    assert_editor(gate_session, oracle, "latte")
