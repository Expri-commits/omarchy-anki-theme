"""Tier 1 — the var-inventory tripwire and the drift check (tickets 15/21).

The snapshot and diff routine ship in the payload (``anki_theme/drift.py``);
these tests are the dev-side enforcement point. The runtime half is the same
``run_check`` driven with fakes here — the live drift smoke is tier 3
(ticket 23).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from anki_theme.drift import (
    MARKER,
    SNAPSHOT_FILE,
    UNREADABLE_SIGNATURE,
    Drift,
    diff_inventory,
    gather_inventory,
    load_snapshot,
    record_signature,
    run_check,
    seen_signatures,
)
from anki_theme.palette import VAR_NAMES


def var() -> dict[str, str]:
    return {"light": "#000000", "dark": "#ffffff"}


def fake_colors(*names: str) -> SimpleNamespace:
    """A module-shaped stand-in for aqt.colors carrying ``names`` as proper
    var entries plus a spread of decoys a real module could also hold."""
    attrs: dict[str, object] = {name: var() for name in names}
    attrs.update(
        _private_var=var(),
        not_a_dict="#112233",
        no_dark_slot={"light": "#000000"},
        a_list=["FG"],
    )
    return SimpleNamespace(**attrs)


# -- the tripwire (the tier-1 enforcement point) --------------------------------


def test_snapshot_vendored_in_the_payload_and_covered_by_the_mapping() -> None:
    """The mapping claims every aqt.colors name the installed Anki carried
    when the snapshot was cut — the tripwire ticket 15 asked for."""
    snapshot = load_snapshot(SNAPSHOT_FILE)
    assert len(snapshot) == 51
    assert set(VAR_NAMES) == snapshot


def test_live_aqt_inventory_matches_the_snapshot() -> None:
    """The runtime gatherer, proven against the real module: an Anki upgrade
    that moves the inventory fails here first, then the snapshot is
    regenerated via scripts/regen_var_snapshot.py. A hard import, not
    importorskip — tier 1 is the dev-machine commit gate, where aqt exists;
    a silent skip would quietly defang the upgrade detector."""
    import aqt.colors as aqt_colors

    assert gather_inventory(aqt_colors) == load_snapshot(SNAPSHOT_FILE)


# -- the snapshot loader ---------------------------------------------------------


def test_load_snapshot_strips_comments_and_blanks(tmp_path: Path) -> None:
    path = tmp_path / "snap.txt"
    path.write_text("# header\n\n  FG  \nCANVAS\n# tail\n")
    assert load_snapshot(path) == frozenset({"FG", "CANVAS"})


def test_load_snapshot_rejects_empty_and_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("# only comments\n")
    with pytest.raises(ValueError, match="no var names"):
        load_snapshot(empty)
    with pytest.raises(OSError):
        load_snapshot(tmp_path / "absent.txt")


# -- the live gatherer ------------------------------------------------------------


def test_gather_inventory_collects_only_polarity_dicts() -> None:
    module = fake_colors("FG", "CANVAS")
    assert gather_inventory(module) == frozenset({"FG", "CANVAS"})


def test_gather_inventory_unreadable_when_nothing_qualifies() -> None:
    assert gather_inventory(SimpleNamespace(_FG=var(), other=1)) is None


# -- the diff ---------------------------------------------------------------------


def test_diff_equal_sets_is_no_drift() -> None:
    names = frozenset({"FG", "CANVAS"})
    assert not diff_inventory(names, names)


def test_diff_retract_class() -> None:
    drift = diff_inventory(frozenset({"FG"}), frozenset({"FG", "CANVAS"}))
    assert drift.retracted == ("CANVAS",)
    assert drift.added == ()
    assert drift.needs_tooltip


def test_diff_add_class_is_log_only() -> None:
    drift = diff_inventory(frozenset({"FG", "CANVAS"}), frozenset({"FG"}))
    assert drift == Drift((), ("CANVAS",))
    assert drift and not drift.needs_tooltip


def test_diff_rename_is_a_pair_the_retract_half_carries() -> None:
    drift = diff_inventory(frozenset({"FG_RENAMED"}), frozenset({"FG"}))
    assert drift.retracted == ("FG",)
    assert drift.added == ("FG_RENAMED",)
    assert drift.needs_tooltip


def test_diff_aliased_name_keeps_the_binding() -> None:
    # Anki adds FG_ALIASED but keeps FG: the covered name survives, so at
    # worst this is add-class — never a tooltip (ticket 15).
    drift = diff_inventory(frozenset({"FG", "FG_ALIASED"}), frozenset({"FG"}))
    assert drift.added == ("FG_ALIASED",)
    assert not drift.needs_tooltip


def test_diff_sorts_names_and_ignores_order() -> None:
    a = diff_inventory(frozenset(), frozenset({"B", "A", "C"}))
    b = diff_inventory(set(), {"C", "B", "A"})
    assert a.retracted == b.retracted == ("A", "B", "C")


def test_diff_unreadable_inventory_is_retract_class() -> None:
    drift = diff_inventory(None, frozenset({"FG"}))
    assert drift.inventory_unreadable
    assert drift.needs_tooltip
    assert drift.signature == UNREADABLE_SIGNATURE
    assert not drift.retracted and not drift.added


# -- the surfacing copy ------------------------------------------------------------


def test_signature_is_the_retract_set() -> None:
    left = Drift(("A", "B"), ())
    right = Drift(("B", "A"), ("C",))
    assert left.signature == right.signature == "A,B"
    assert Drift((), ("C",)).signature == ""


def test_log_line_names_both_sides() -> None:
    line = Drift(("CANVAS",), ("FG_NEW",)).log_line()
    assert "retracted 1 (CANVAS)" in line and "added 1 (FG_NEW)" in line
    assert "unreadable" in Drift((), (), inventory_unreadable=True).log_line()


def test_tooltip_text_is_mode_aware() -> None:
    drift = Drift(("CANVAS_CODE",), ())
    bundled = drift.tooltip_text(bundled=True)
    standalone = drift.tooltip_text(bundled=False)
    assert "plugin update" in bundled
    assert "reinstall" in standalone.lower()
    assert "1 color variable" in bundled  # singular


def test_tooltip_text_caps_and_pluralizes_the_name_list() -> None:
    drift = Drift(("A", "B", "C", "D", "E", "F"), ())
    text = drift.tooltip_text(bundled=True)
    assert "6 color variables" in text
    assert "A, B, C, D, E, …" in text
    unreadable = Drift((), (), inventory_unreadable=True).tooltip_text(bundled=True)
    assert unreadable.endswith("restore them.")


# -- the dedup marker ---------------------------------------------------------------


def test_marker_round_trip_and_merge(tmp_path: Path) -> None:
    assert seen_signatures(tmp_path) == set()
    record_signature(tmp_path, "A")
    record_signature(tmp_path, "B")
    assert seen_signatures(tmp_path) == {"A", "B"}
    assert json.loads((tmp_path / MARKER).read_text()) == {"signatures": ["A", "B"]}


def test_marker_unreadable_means_unseen(tmp_path: Path) -> None:
    (tmp_path / MARKER).write_text("{not json")
    assert seen_signatures(tmp_path) == set()


def test_marker_with_mangled_entries_fails_open(tmp_path: Path) -> None:
    # A hand-edited marker carrying non-string (even unhashable) entries
    # means "unseen", never a crash past the guard.
    (tmp_path / MARKER).write_text('{"signatures": [["nested"], 3, "STATE_NEW"]}')
    assert seen_signatures(tmp_path) == {"STATE_NEW"}


def test_record_signature_replaces_a_planted_marker_link_not_its_target(
    tmp_path: Path,
) -> None:
    """The marker write is no-follow end to end: a pre-planted symlink at the
    marker path gets replaced wholesale by mkstemp+os.replace, and its target
    is never truncated."""
    victim = tmp_path / "victim.json"
    victim.write_text('{"keep": true}\n')
    (tmp_path / MARKER).symlink_to(victim)
    record_signature(tmp_path, "A")
    assert json.loads((tmp_path / MARKER).read_text()) == {"signatures": ["A"]}
    assert not (tmp_path / MARKER).is_symlink()
    assert json.loads(victim.read_text()) == {"keep": True}
    # The pid-suffixed tmp the old code used is gone: no temp litter either.
    assert {p.name for p in tmp_path.iterdir()} == {MARKER, "victim.json"}


# -- the one startup check ----------------------------------------------------------


SNAPSHOT_NAMES = ("STOCK_A", "STOCK_B")


def write_snapshot(tmp_path: Path, *names: str) -> Path:
    path = tmp_path / "snap.txt"
    path.write_text("".join(f"{n}\n" for n in names))
    return path


def check(
    module: SimpleNamespace, state_dir: Path, *, bundled: bool = True, snapshot: Path, tooltip=None
) -> tuple[Drift | None, list[str], list[str]]:
    logs: list[str] = []
    tips: list[str] = []
    result = run_check(
        module,
        snapshot,
        state_dir,
        bundled=bundled,
        log=logs.append,
        tooltip=tooltip if tooltip is not None else tips.append,
    )
    return result, logs, tips


def test_retract_drift_logs_and_tooltips_once_then_silent(tmp_path: Path) -> None:
    """The ticket-21 acceptance shape: one log line + one transient tooltip
    on the first start; the second start logs again but stays tooltip-silent
    (state-dir signature dedup)."""
    snapshot = write_snapshot(tmp_path, *SNAPSHOT_NAMES)
    module = fake_colors("STOCK_A")  # STOCK_B retracted
    result, logs, tips = check(module, tmp_path, snapshot=snapshot)
    assert result is not None and result.needs_tooltip
    assert len(tips) == 1 and "plugin update" in tips[0]
    assert len(logs) == 1 and "retracted" in logs[0]
    assert seen_signatures(tmp_path) == {"STOCK_B"}
    _, logs, tips = check(module, tmp_path, snapshot=snapshot)
    assert tips == []
    assert any("already surfaced" in line for line in logs)


def test_add_class_drift_is_log_only(tmp_path: Path) -> None:
    snapshot = write_snapshot(tmp_path, *SNAPSHOT_NAMES)
    module = fake_colors("STOCK_A", "STOCK_B", "NEW_VAR")
    result, logs, tips = check(module, tmp_path, snapshot=snapshot)
    assert result is not None and not result.needs_tooltip
    assert tips == []
    assert len(logs) == 1 and "added 1" in logs[0]
    assert not (tmp_path / MARKER).exists()


def test_standalone_copy_says_reinstall(tmp_path: Path) -> None:
    snapshot = write_snapshot(tmp_path, *SNAPSHOT_NAMES)
    _, _, tips = check(fake_colors("STOCK_A"), tmp_path, bundled=False, snapshot=snapshot)
    assert len(tips) == 1 and "reinstall" in tips[0].lower()


def test_unreadable_inventory_surfaces_then_dedups(tmp_path: Path) -> None:
    snapshot = write_snapshot(tmp_path, *SNAPSHOT_NAMES)
    module = fake_colors()  # decoys only — gather returns None
    result, logs, tips = check(module, tmp_path, snapshot=snapshot)
    assert result is not None and result.inventory_unreadable
    assert len(tips) == 1
    assert seen_signatures(tmp_path) == {UNREADABLE_SIGNATURE}
    _, logs, tips = check(module, tmp_path, snapshot=snapshot)
    assert tips == [] and any("already surfaced" in line for line in logs)


def test_failed_tooltip_is_retried_next_start(tmp_path: Path) -> None:
    snapshot = write_snapshot(tmp_path, *SNAPSHOT_NAMES)

    def exploding(_: str) -> None:
        raise RuntimeError("no mw yet")

    result, logs, _ = check(fake_colors("STOCK_A"), tmp_path, snapshot=snapshot, tooltip=exploding)
    assert result is not None
    assert any("tooltip failed" in line for line in logs)
    assert seen_signatures(tmp_path) == set()  # unrecorded → next start retries
    _, logs, tips = check(fake_colors("STOCK_A"), tmp_path, snapshot=snapshot)
    assert len(tips) == 1


def test_unreadable_snapshot_skips_the_check(tmp_path: Path) -> None:
    result, logs, tips = check(
        fake_colors(*SNAPSHOT_NAMES), tmp_path, snapshot=tmp_path / "absent.txt"
    )
    assert result is None and tips == []
    assert len(logs) == 1 and "snapshot unreadable" in logs[0]


def test_clean_inventory_writes_nothing(tmp_path: Path) -> None:
    snapshot = write_snapshot(tmp_path, *SNAPSHOT_NAMES)
    result, logs, tips = check(fake_colors("STOCK_A", "STOCK_B"), tmp_path, snapshot=snapshot)
    assert not result and logs == [] and tips == []
    assert list(tmp_path.iterdir()) == [snapshot]
