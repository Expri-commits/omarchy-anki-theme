"""Tier-3 gate, Anki-up phase: the stock matrix, the pathological renders,
and the perf session's live legs (ticket 23, docs/verification.md §Tier 3).

One matrix instance (scratch base, dev-linked payload, the gate control
add-on), then in order:

  startup sanity            the startup apply completed cleanly (timing
                            recorded, never gated — user directive
                            2026-08-30)
  the Add window            opened once before the matrix; it stays open, so
                            every later switch must restyle it live (no page
                            rebuild) — the same-polarity proof per theme
  the stock matrix          all 22 stock palettes × the mandatory surfaces
                            (deck browser + menubar + toolbar, reviewer,
                            editor, stats, Qt menu popup, Preferences dialog)
  the perf session          ≥ 5-switch timing means + the screen-recording
                            frame-diff cross-check the spike left pending
  the pathological renders  P1–P5 as real user themes through the production
                            ``omarchy theme set`` path; clamp log lines must
                            match tier-1's prediction; P1 again in
                            faithful mode (config flip through the real
                            writeConfig + configUpdatedAction path)

The Anki-down legs (propagation, below-floor, consent, drift, startup cost)
live in test_gate_full_legs.py and run after this module stopped the instance.

Not asserted here by design: the toast/tooltip surface joins when the
mechanism lands (residuals ledger row 1) — stock toast/tooltip is expected
behavior, not a failure.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(GATE_DIR))

from ankiya.palette import VAR_RULES  # noqa: E402
from ankiya.theme_clamp import clamp_palette, map_with_clamp  # noqa: E402
from oracles import ThemeOracle, rgb  # noqa: E402
from pathological import MODES, P1, P2, P3, P4, P5  # noqa: E402
from points import sample as _sample  # noqa: E402
from sampling import TOLERANCE, Shot, channel_delta, contrast_ratio  # noqa: E402
from test_gate import (  # noqa: E402
    assert_deck_and_menubar,
    assert_editor,
    assert_reviewer,
    assert_switch_record,
    note_perf,
    perf_row,
)

pytestmark = pytest.mark.gate_full

# Glyph pixels antialias 1–2 channels against their fill, so a sampled ratio
# sits a hair under the pure-color math — the clamp's feasible landing points
# can clear the floor by less than that (P1: 4.54). Contrast asserts compare
# against tier-1's prediction with this slack, not against the policy floor.
ANTIALIAS_SLACK = 0.25

# The stock matrix's fixture dirs.
from theme_fixtures import THEMES  # noqa: E402

# The palette each user-theme fork carries, and the fork's mode (matches its
# background family so night_mode and the palette agree).
PATHOLOGICAL: dict[str, tuple[dict[str, str], str]] = {
    "ankiya-gate-p1": (P1, MODES["p1"]),
    "ankiya-gate-p2": (P2, MODES["p2"]),
    "ankiya-gate-p3": (P3, MODES["p3"]),
    "ankiya-gate-p4": (P4, MODES["p4"]),
    "ankiya-gate-p5": (P5, MODES["p5"]),
}


# -- fixtures ------------------------------------------------------------------


@pytest.fixture(scope="session")
def add_window(gate3_session):
    """The Add window opens once and stays open through every later switch:
    its page can only recolor through the live legs (open-page eval +
    engine-script), so each theme's editor assert doubles as the live-restyle
    proof for that switch."""
    reply = gate3_session.cmd("open_add")
    assert reply["ok"], f"the Add window never rendered: {reply}"
    return gate3_session


@pytest.fixture(scope="session")
def p_themes(gate3_session):
    """The pathological palettes registered as real user themes (forked from
    the mode-matching stock theme, colors.toml replaced); teardown removes
    the forks and restores the original theme."""
    for slug, (palette, mode) in PATHOLOGICAL.items():
        gate3_session.register_user_theme(slug, palette, mode)
    return gate3_session


# -- surface asserts -----------------------------------------------------------


def assert_stats(session, oracle: ThemeOracle, label: str) -> None:
    reply = session.cmd("open_stats")
    assert reply["ok"], f"stats dialog never rendered: {reply}"
    try:
        probe = session.probe("stats")
        shot = Shot(session.capture("stats", f"stats-{label}"))
        # 26.08.1 characterization: the legacy flot page's series colors flow
        # through theme_manager._update_stat_colors from the mapped STATE_*
        # vars — the Added graph's bar is colLearn = STATE_NEW drawn at flot's
        # fill=0.7 over the canvas, so the assert stays palette-derived
        # (residuals row 5).
        _sample(session, probe, "stats", "series", shot, oracle.stats_added_bar, scan=True)
        if oracle.dark:
            _sample(session, probe, "stats", "page_bg", shot, oracle.canvas)
        else:
            # Light polarity: QtWebEngine leaves the page's background tiles
            # unrendered — stale GPU-tile noise, pixel-identical across
            # palettes (residuals row 6, characterized 2026-08-30). The theming
            # itself is proven DOM-side: the computed body background carries
            # the palette canvas.
            got = rgb(probe["dom"]["stats"]["result"]["body_bg"])
            delta = channel_delta(got, oracle.canvas)
            assert delta <= TOLERANCE, (
                f"stats/page_bg[{label}, light, DOM]: computed body_bg rgb{got} "
                f"is {delta} > {TOLERANCE}/channel from expected rgb{oracle.canvas}"
            )
    finally:
        session.cmd("close_stats")


def assert_menu(session, oracle: ThemeOracle, label: str) -> None:
    reply = session.cmd("open_menu", {"menu": "File", "highlight": "Import..."})
    assert reply["ok"], f"menu never popped up: {reply}"
    try:
        probe = session.probe("menu")
        # Popup surfaces sample the widget's own render buffer (see the gate
        # add-on's _menu_payload): Wayland clients cannot know a popup's real
        # compositor position, so a grim capture of the "reported" rect grabs
        # the wrong region while the compositor dims the window behind.
        png = base64.b64decode(probe["menu"]["grab_png"])
        (session.run_dir / f"menu-{label}.png").write_bytes(png)
        shot = Shot(png=png)
        assert probe["menu"]["active"] >= 0, (
            "no menu action highlighted — the synthetic hover never landed"
        )
        bg_xy, bg = _sample(session, probe, "menu", "bg", shot, oracle.menu_bg)
        text_xy, text = _sample(session, probe, "menu", "text", shot, oracle.fg, scan=True)
        _sample(session, probe, "menu", "highlight_fill", shot, oracle.menu_highlight)
        _sample(session, probe, "menu", "highlight_text", shot, oracle.selected_fg, scan=True)
        spec = session.map["contrast"]["menu_text_vs_menu_bg"]
        ratio = contrast_ratio(text, bg)
        assert ratio >= spec["floor"], (
            f"menu/menu_text x menu_bg contrast {ratio:.2f} < {spec['floor']} "
            f"(text rgb{text} at {text_xy} vs fill rgb{bg} at {bg_xy}, {shot.path})"
        )
    finally:
        session.cmd("close_menu")


def assert_prefs(session, oracle: ThemeOracle, label: str) -> None:
    """26.08.1's Preferences is the native Qt dialog (the sveltekit page hides
    on a non-current Labs tab): the dialog margin paints QPalette Window
    (= CANVAS via the qt-chrome leg) and the tab pane CANVAS_ELEVATED
    (aqt.stylesheets.tabwidget)."""
    reply = session.cmd("open_prefs")
    assert reply["ok"], f"prefs dialog never rendered: {reply}"
    try:
        probe = session.probe("prefs")
        shot = Shot(session.capture("prefs", f"prefs-{label}"))
        _sample(session, probe, "prefs", "window_bg", shot, oracle.canvas)
        _sample(session, probe, "prefs", "pane_bg", shot, oracle.canvas_elevated)
    finally:
        session.cmd("close_prefs")


def assert_full_surfaces(session, oracle: ThemeOracle, label: str) -> None:
    """The mandatory surface set for one palette (surfaces 1–7; 8 joins when
    the toast mechanism lands, 9 is the below-floor legs' business)."""
    session.cmd("show_deck")
    assert_deck_and_menubar(session, oracle, label)
    session.cmd("show_review")
    assert_reviewer(session, oracle, label)
    assert_editor(session, oracle, label)
    assert_stats(session, oracle, label)
    assert_menu(session, oracle, label)
    assert_prefs(session, oracle, label)


# -- record asserts (pathological variants) --------------------------------------


def assert_pathological_record(
    record: dict, t_swap: float, oracle: ThemeOracle, adjustments: tuple, leg: str, session=None
) -> None:
    """Like tier 2's record assert, but the clamp count must equal tier-1's
    pure prediction instead of zero."""
    where = f"pathological leg {leg!r}"
    assert record["errors"] == [], f"{where}: apply errors {record['errors']}"
    assert record["vars"] + record["skipped"] == len(VAR_RULES), (
        f"{where}: accounted {record['vars']}+{record['skipped']} vars, "
        f"expected {len(VAR_RULES)} (degraded keys ride `skipped`)"
    )
    assert record["clamped"] == len(adjustments), (
        f"{where}: clamped {record['clamped']}, tier-1 predicted {len(adjustments)}"
    )
    assert record["dark"] == oracle.dark, f"{where}: polarity mismatch"
    note_perf(session, perf_row(record, t_swap, leg))
    assert record["engine_profiles"] >= 1, f"{where}: sveltekit leg dead"
    assert record["views"] >= 1, f"{where}: no open webview was restyled"


def assert_clamp_lines(session, adjustments: tuple) -> None:
    """Every predicted clamp adjustment logged verbatim during this run."""
    logged = [
        line for line in session.anki_log.read_text().splitlines() if "contrast clamp:" in line
    ]
    for adjustment in adjustments:
        expected = f"[ankiya] {adjustment.line()}"
        assert expected in logged, (
            f"clamp log line missing: {expected!r}\nlogged clamp lines: {logged[-10:]}"
        )


# -- the legs --------------------------------------------------------------------


def test_startup_sanity(gate3_session):
    record = gate3_session.startup_record
    assert record is not None
    assert record["errors"] == [], f"startup apply errors: {record['errors']}"
    assert record["vars"] + record["skipped"] == len(VAR_RULES)
    note_perf(gate3_session, {"leg": "startup", "apply_ms": record["apply_ms"]})


@pytest.mark.parametrize("theme", THEMES)
def test_matrix(theme: str, gate3_session, add_window):
    """One stock palette through the production switch path and the full
    surface set — 22 of these."""
    oracle = ThemeOracle(theme)
    record, t_swap, _t_set_done = gate3_session.switch(theme)
    assert_switch_record(record, t_swap, oracle, f"matrix {theme}", gate3_session)
    assert_full_surfaces(gate3_session, oracle, theme)


# -- the perf session's live legs --------------------------------------------------


def test_switch_timing_session(gate3_session, add_window):
    """The standing switch-to-reapply metric, session-shaped: five
    same-polarity (dark→dark) switches through the production path, each
    recorded (timing is record-only since the 2026-08-30 de-gating), means
    written to the run's artifacts for the perf log session row."""
    session = gate3_session
    session.cmd("show_deck")
    # Establish the alternation's start (the matrix leaves the last stock
    # theme live; a switch to it would be a content-unchanged skip).
    session.switch("catppuccin")
    samples = []
    for index in range(5):
        target = "gruvbox" if index % 2 == 0 else "catppuccin"
        oracle = ThemeOracle(target)
        record, t_swap, t_set_done = session.switch(target)
        assert_switch_record(record, t_swap, oracle, f"timing {target}", session)
        samples.append(
            {
                "theme": target,
                "apply_ms": record["apply_ms"],
                "swap_to_applied_ms": round((record["applied_at"] - t_swap) * 1000, 1),
                "reapply_vs_set_done_ms": round((record["applied_at"] - t_set_done) * 1000, 1),
            }
        )
    mean_apply = sum(s["apply_ms"] for s in samples) / len(samples)
    mean_swap = sum(s["swap_to_applied_ms"] for s in samples) / len(samples)
    (session.run_dir / "perf-switch-session.json").write_text(
        json.dumps(
            {
                "samples": samples,
                "mean_apply_ms": mean_apply,
                "mean_swap_to_applied_ms": mean_swap,
            },
            indent=1,
        )
    )
    print(
        f"perf: switch-to-reapply session — apply {mean_apply:.1f} ms mean, "
        f"swap→applied {mean_swap:.1f} ms mean over {len(samples)} switches"
    )


def test_frame_diff_cross_check(gate3_session, add_window):
    """The spike's pending leg: the applied record's recolor claim checked
    against pixels on screen. One recording captures two switches; the two
    flips' *interval* on video validates the record timestamps with the
    recorder's start offset unknown; the recorded first-frame timestamp (when
    the encoder embeds it) anchors the absolute claim."""
    from frame_diff import Recorder, _fps, first_frame_timestamp, flip_frames

    session = gate3_session
    session.cmd("show_deck")
    before = ThemeOracle("catppuccin").canvas
    after = ThemeOracle("gruvbox").canvas

    # Establish the baseline ourselves: the timing session leaves whatever its
    # last target was, and a switch to the already-live theme is a
    # content-unchanged skip with no applied record.
    session.switch("catppuccin")
    rec_path = session.run_dir / "frame-diff.mp4"
    recorder = Recorder(rec_path)
    stopped = False
    try:
        time.sleep(2.0)  # baseline: Catppuccin visible, encoder warmed up
        record_a, _t_swap_a, t_set_done_a = session.switch("gruvbox")
        time.sleep(2.5)
        record_b, _t_swap_b, _t_set_done_b = session.switch("catppuccin")
        recorder.stop()
        stopped = True
    finally:
        if not stopped:
            recorder.stop()

    flips_to_gruv = flip_frames(rec_path, before, after)
    flips_to_catpp = flip_frames(rec_path, after, before)
    assert flips_to_gruv, "no frame settled on the Gruvbox canvas — the recolor never showed"
    assert flips_to_catpp, "no frame settled back on Catppuccin — the second recolor never showed"
    assert len(flips_to_gruv) == 1 and len(flips_to_catpp) == 1, (
        f"expected exactly one flip each way, got {flips_to_gruv} / {flips_to_catpp}"
    )

    fps = _fps(rec_path)
    f_gruv, f_catpp = flips_to_gruv[0], flips_to_catpp[0]
    interval_video = (f_catpp - f_gruv) / fps
    interval_records = record_b["applied_at"] - record_a["applied_at"]
    # Interval agreement is recorded, not asserted (de-gated 2026-08-30, run
    # 3): applied_at is stamped after ALL restyle legs finish, while the
    # screen flips when the main webview repaints mid-loop — with the matrix
    # session's open views that tail reached 0.8 s. A real disagreement to
    # investigate belongs to the perf-polish ticket; the flips-exist and
    # exactly-once asserts above carry the correctness claim.
    print(
        f"perf: flip interval on video {interval_video:.3f}s vs applied records "
        f"{interval_records:.3f}s (delta {interval_video - interval_records:+.3f}s — "
        "applied_at trails the visible flip by the remaining restyle legs)"
    )

    first_ts = first_frame_timestamp(rec_path)
    absolute = None
    if first_ts is not None:
        flip_a_wall = first_ts + f_gruv / fps
        absolute = {
            "flip_a_wall": flip_a_wall,
            "vs_set_done_s": flip_a_wall - t_set_done_a,
        }
        # The spike's headline, pixel-proven and recorded (not asserted —
        # perf is record-only since the 2026-08-30 de-gating): how long
        # after `omarchy theme set` returned the recolor was on screen.
        print(
            f"perf: recolor visible {flip_a_wall - t_set_done_a:+.3f}s around "
            "`omarchy theme set` returning (frame-diff absolute anchor)"
        )
    (session.run_dir / "perf-frame-diff.json").write_text(
        json.dumps(
            {
                "fps": fps,
                "flip_frames": [f_gruv, f_catpp],
                "interval_video_s": interval_video,
                "interval_records_s": interval_records,
                "interval_delta_s": interval_video - interval_records,
                "first_frame_ts": first_ts,
                "absolute": absolute,
                "applied_ms": [record_a["apply_ms"], record_b["apply_ms"]],
            },
            indent=1,
        )
    )
    print(
        f"perf: frame-diff cross-check — flips {f_gruv}/{f_catpp}@{fps:.0f}fps, "
        f"interval video {interval_video:.3f}s vs records {interval_records:.3f}s, "
        f"absolute anchor {'yes' if first_ts is not None else 'absent (interval-only)'}"
    )


# -- the pathological renders ------------------------------------------------------


def p_oracle(slug: str) -> tuple[ThemeOracle, dict[str, str], tuple]:
    """The oracle expects what the runtime applies: the **clamped** mapping,
    tier-1's own prediction. The authored palette verbatim is the faithful
    mode's oracle, built where that leg needs it."""
    palette, mode = PATHOLOGICAL[slug]
    mapping, adjustments = map_with_clamp(palette, True)
    return ThemeOracle(palette=palette, mode=mode, mapping=mapping), palette, adjustments


@pytest.mark.parametrize("slug", list(PATHOLOGICAL))
def test_pathological_render(slug: str, gate3_session, add_window, p_themes):
    """One pathological user palette through the production path: the clamp's
    render-side proof. Backgrounds byte-match the authored keys, the clamp
    log lines equal tier-1's prediction, and the deck name's sampled contrast
    meets its floor — or, for the infeasible P4/P5 shapes, the predicted
    max-min ratio (honestly below AA)."""
    session = gate3_session
    oracle, _palette, adjustments = p_oracle(slug)
    session.cmd("show_deck")
    record, t_swap, _t_set_done = session.switch(slug)
    assert_pathological_record(record, t_swap, oracle, adjustments, slug, session)
    assert_clamp_lines(session, adjustments)

    probe = session.probe("deck")
    shot = Shot(session.capture("main", slug))
    canvas_xy, canvas = _sample(session, probe, "deck", "canvas", shot, oracle.canvas)
    name_xy, name = _sample(session, probe, "deck", "deck_name", shot, oracle.fg, scan=True)
    ratio = contrast_ratio(name, canvas)
    fg_adj = next((a for a in adjustments if a.key == "foreground"), None)
    if fg_adj is not None and fg_adj.unsatisfiable:
        predicted_min = min(fg_adj.after)
        assert ratio >= predicted_min - ANTIALIAS_SLACK, (
            f"{slug}: rendered name contrast {ratio:.2f} below the predicted "
            f"max-min {predicted_min:.2f} − {ANTIALIAS_SLACK}"
        )
        assert ratio < 4.5, (
            f"{slug}: rendered contrast {ratio:.2f} claims AA the clamp called "
            "unsatisfiable — investigate before trusting it"
        )
    elif fg_adj is not None:
        assert ratio >= fg_adj.after[0] - ANTIALIAS_SLACK, (
            f"{slug}: rendered name contrast {ratio:.2f} below the clamp's "
            f"landing point {fg_adj.after[0]:.2f} − {ANTIALIAS_SLACK} "
            f"(text rgb{name} at {name_xy} vs fill rgb{canvas} at {canvas_xy})"
        )
    else:
        assert ratio >= 4.5, (
            f"{slug}: rendered name contrast {ratio:.2f} < 4.5 "
            f"(text rgb{name} at {name_xy} vs fill rgb{canvas} at {canvas_xy})"
        )

    # The still-open Add window renders the hostile palette live (sveltekit).
    add_probe = session.probe("add")
    add_shot = Shot(session.capture("add", slug))
    _sample(session, add_probe, "add", "page_bg", add_shot, oracle.canvas)


def test_p2_link_lands_in_delivered_css(gate3_session, add_window, p_themes):
    """P2's guard is the link floor, but no mandatory surface renders a link
    (deck names paint --fg — the ticket-22 characterization) — the clamp's
    link result is proven content-level in the regenerated stdHtml CSS the
    next page build reads, and the gallery sweep watches rendered links
    (ledger row 4)."""
    from gate_harness import PAYLOAD

    session = gate3_session
    palette = PATHOLOGICAL["ankiya-gate-p2"][0]
    clamped = clamp_palette(palette).palette
    assert clamped["bright_blue"] != palette["bright_blue"], (
        "tier-1 predicted a link adjustment for P2 — the fixture moved"
    )

    session.switch("ankiya-gate-p2")
    css = (PAYLOAD / "web" / "ankiya.css").read_text()
    expected = f"--fg-link: {clamped['bright_blue']};"
    assert expected in css, (
        f"delivered CSS does not carry the clamped link {expected!r} "
        "(P2's bright_blue was below the 3.0 floor — the clamp must land)"
    )


def test_p1_faithful_mode(gate3_session, add_window, p_themes):
    """P1 again with contrast_clamp off — the config flips through the real
    writeConfig + configUpdatedAction path and the runtime re-applies the
    current (P1) palette verbatim: nothing clamped, the authored foreground
    on screen, its failure to read honestly visible in the render."""
    session = gate3_session
    oracle, palette, adjustments = p_oracle("ankiya-gate-p1")
    # The p2-link leg ran since the parametrized renders and left the session
    # on p2 — switch back through the production path so the config re-apply
    # below re-applies *this* palette (the record's theme field is the live
    # theme.name, not the config flip's subject).
    record, _t_swap, _t_set_done = session.switch("ankiya-gate-p1")
    assert_pathological_record(record, _t_swap, oracle, adjustments, "p1-prefaithful", session)

    # The config flip re-applies the current (P1) palette with reason
    # "config". The restore rides a finally: a failure mid-test must never
    # leak contrast_clamp=False into the next run (aqt stores the config
    # inside the dev-linked add-on folder).
    try:
        reply = session.cmd("set_clamp", {"enabled": False})
        assert reply["ok"] and reply["changed"], f"clamp flip failed: {reply}"
        record = session.wait_applied(
            lambda r: r["reason"] == "config" and r["theme"] == "ankiya-gate-p1",
            15.0,
            "the faithful re-apply",
        )
        assert record["clamped"] == 0, f"faithful mode still clamped: {record}"
        assert record["errors"] == [], f"faithful apply errors: {record['errors']}"

        session.cmd("show_deck")
        probe = session.probe("deck")
        shot = Shot(session.capture("main", "p1-faithful"))
        # Faithful mode renders the palette verbatim — the authored (here:
        # invisible) foreground is the expectation.
        faithful = ThemeOracle.from_palette(palette, MODES["p1"])
        canvas_xy, canvas = _sample(session, probe, "deck", "canvas", shot, faithful.canvas)
        _name_xy, name = _sample(session, probe, "deck", "deck_name", shot, faithful.fg, scan=True)
        ratio = contrast_ratio(name, canvas)
        assert ratio < 4.5, (
            f"faithful P1 render claims legibility ({ratio:.2f}) — the verbatim "
            f"foreground #{palette['foreground']} should be invisible on its "
            f"background at {canvas_xy}"
        )
    finally:
        # Restore the policy through the same real path; the re-apply clamps
        # again (asserted below, outside the finally).
        session.cmd("set_clamp", {"enabled": True})

    record = session.wait_applied(
        lambda r: r["reason"] == "config" and r["theme"] == "ankiya-gate-p1",
        15.0,
        "the clamped re-apply",
    )
    assert record["clamped"] == len(adjustments), (
        f"restored clamp adjusted {record['clamped']}, predicted {len(adjustments)}"
    )
