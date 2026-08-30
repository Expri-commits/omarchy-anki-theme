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
from sampling import (  # noqa: E402
    TOLERANCE,
    Shot,
    assert_color,
    channel_delta,
    contrast_ratio,
    scan_for_color,
)

pytestmark = pytest.mark.gate

APPLY_BUDGET_MS = 50.0
SWAP_TO_RECOLOR_S = 0.250
STARTUP_BUDGET_MS = 100.0


# -- sample-map point resolution ---------------------------------------------


def _dom(probe: dict, view: str) -> dict:
    entry = probe.get("dom", {}).get(view)
    if not entry or not entry.get("ok"):
        raise AssertionError(f"DOM probe for view {view!r} missing or failed: {entry}")
    return entry["result"]


def _shot_offset(session, probe: dict, shot: Shot) -> tuple[int, int]:
    dx, dy = session.shot_offset(shot, probe["window"])
    if dx < 0 or dy < 0:
        raise AssertionError(
            f"shot {shot.size} smaller than window {probe['window']} — capture mismatch"
        )
    return dx, dy


def _window_xy(session, probe: dict, surface: str, point_name: str) -> tuple[float, float]:
    spec = session.map["points"][surface][point_name]
    if spec["view"] == "qt":
        anchor = spec["anchor"]
        if anchor == "qt.menubar":
            x, y, w, h = probe["qt"]["menubar"]
        elif anchor == "qt.action":
            actions = probe["qt"].get("menubar_actions", [])
            wanted = spec["action"]
            # QMenuBar action texts carry & mnemonics ("&Help")
            rects = [a["rect"] for a in actions if a["text"].replace("&", "") == wanted]
            if not rects:
                raise AssertionError(
                    f"{surface}/{point_name}: no menubar action {wanted!r} in "
                    f"{[a['text'] for a in actions]}"
                )
            x, y, w, h = rects[0]
        else:
            raise AssertionError(f"{surface}/{point_name}: unknown qt anchor {anchor!r}")
    else:
        view = spec["view"]
        dom = _dom(probe, view)
        dpr = dom.get("dpr") or 1.0
        vx, vy, vw, vh = probe["views"][view]
        anchor = spec.get("anchor")
        if anchor:
            rect = dom[anchor.removeprefix("dom.")]
            x = vx + (rect["x"] + rect["w"] * spec.get("fx", 0.5) + spec.get("dx", 0)) * dpr
            y = vy + (rect["y"] + rect["h"] * spec.get("fy", 0.5) + spec.get("dy", 0)) * dpr
        else:
            x = vx + (vw / dpr) * spec.get("fx", 0.5)
            y = vy + (vh / dpr) * spec.get("fy", 0.5)
    return x, y


def _point(session, probe: dict, surface: str, point_name: str, shot: Shot) -> tuple[int, int]:
    x, y = _window_xy(session, probe, surface, point_name)
    dx, dy = _shot_offset(session, probe, shot)
    return int(round(x)) + dx, int(round(y)) + dy


def _scan_region(
    session, probe: dict, surface: str, point_name: str, shot: Shot
) -> tuple[int, int, int, int]:
    """The shot-coordinates region a scan point searches: the anchor rect
    (DOM or menubar action) for glyph scans, the 6px strip just left of the
    anchor for focus rings (the ring is the element's border)."""
    spec = session.map["points"][surface][point_name]
    if spec["view"] == "qt":
        actions = probe["qt"].get("menubar_actions", [])
        rects = [
            a["rect"] for a in actions if a["text"].replace("&", "") == spec["action"]
        ]
        if not rects:
            raise AssertionError(f"{surface}/{point_name}: action {spec['action']!r} not found")
        x, y, w, h = rects[0]
    else:
        view = spec["view"]
        dom = _dom(probe, view)
        dpr = dom.get("dpr") or 1.0
        vx, vy, _vw, _vh = probe["views"][view]
        rect = dom[spec["anchor"].removeprefix("dom.")]
        x = vx + rect["x"] * dpr
        y = vy + rect["y"] * dpr
        w = rect["w"] * dpr
        h = rect["h"] * dpr
    dx, dy = _shot_offset(session, probe, shot)
    x, y, w, h = x + dx, y + dy, w, h
    if spec.get("scan_ring"):
        # The focused field's ring (a CSS outline) paints in the 1-2 px
        # around the rect's edge — cover x-3..x so the ring column is in.
        return int(x - 3), int(y + 2), 4, int(h - 4)
    return int(x) + 1, int(y) + 1, int(w) - 2, int(h) - 2


def _sample(session, probe, surface, point_name, shot, expected, scan=False):
    """One sample-map point → (xy, sampled rgb), asserted against `expected`."""
    if scan:
        region = _scan_region(session, probe, surface, point_name, shot)
        xy, sample = scan_for_color(shot, region, expected)
        delta = channel_delta(sample, expected)
        if delta > TOLERANCE:
            raise AssertionError(
                f"{surface}/{point_name}: closest pixel rgb{sample} at {xy} still "
                f"{delta} > {TOLERANCE}/channel from expected rgb{expected} ({shot.path})"
            )
        return xy, sample
    xy = _point(session, probe, surface, point_name, shot)
    return xy, assert_color(surface, point_name, shot, xy, expected)


# -- threshold + record asserts ----------------------------------------------


def assert_switch_record(record: dict, t_swap: float, oracle: ThemeOracle, leg: str):
    where = f"switch leg {leg!r}"
    assert record["errors"] == [], f"{where}: apply errors {record['errors']}"
    assert record["vars"] == len(VAR_RULES) and record["skipped"] == 0, (
        f"{where}: mapped {record['vars']}+{record['skipped']} vars, "
        f"expected {len(VAR_RULES)}+0"
    )
    assert record["clamped"] == 0, f"{where}: clamp adjusted a stock palette"
    assert record["dark"] == oracle.dark, f"{where}: polarity mismatch"
    assert record["apply_ms"] <= APPLY_BUDGET_MS, (
        f"{where}: in-app apply took {record['apply_ms']}ms (budget {APPLY_BUDGET_MS}ms)"
    )
    elapsed = record["applied_at"] - t_swap
    assert elapsed <= SWAP_TO_RECOLOR_S, (
        f"{where}: swap → recolor applied took {elapsed * 1000:.0f}ms "
        f"(budget {SWAP_TO_RECOLOR_S * 1000:.0f}ms)"
    )
    assert record["engine_profiles"] >= 1, f"{where}: sveltekit leg dead (no profile scripted)"
    assert record["views"] >= 1, f"{where}: no open webview was restyled"
    return record


# -- surface asserts -----------------------------------------------------------


def assert_deck_and_menubar(session, oracle: ThemeOracle, label: str) -> None:
    probe = session.probe("deck")
    shot = Shot(session.capture("main", label))
    canvas_xy, canvas = _sample(
        session, probe, "deck", "canvas", shot, oracle.canvas
    )
    _sample(session, probe, "deck", "row_fill", shot, oracle.current_row)
    _name_xy, name = _sample(
        session, probe, "deck", "deck_name", shot, oracle.fg, scan=True
    )
    _sample(session, probe, "menubar", "bg", shot, oracle.canvas)
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
    _sample(
        session, probe, "add", "focus_ring", shot, oracle.focus_ring, scan=True
    )


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
        f"startup accounted {record['vars']}+{record['skipped']} vars, "
        f"expected {len(VAR_RULES)}"
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
