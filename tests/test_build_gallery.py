"""Gallery builder — pure shot-parsing logic (scripts/build_gallery.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def builder():
    spec = importlib.util.spec_from_file_location(
        "build_gallery", REPO / "scripts" / "build_gallery.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def make_run(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).write_bytes(b"")
    return tmp_path


def test_stock_theme_gets_all_five_surfaces_in_step_order(tmp_path):
    run = make_run(
        tmp_path,
        "shot-001-main-catppuccin.png",
        "shot-002-main-catppuccin.png",
        "shot-003-add-catppuccin.png",
        "shot-004-stats-stats-catppuccin.png",
        "shot-005-prefs-prefs-catppuccin.png",
    )
    themes = builder().parse_shots(run)
    assert [kind for kind, _ in themes["catppuccin"]] == [
        "deck",
        "review",
        "add (editor)",
        "stats",
        "prefs",
    ]


def test_special_legs_are_excluded_from_themes(tmp_path):
    run = make_run(
        tmp_path,
        "shot-121-main-p1-faithful.png",
        "shot-122-main-below-floor-absent.png",
        "shot-123-main-below-floor-unreadable.png",
    )
    assert builder().parse_shots(run) == {}


def test_unrecognized_files_are_ignored(tmp_path):
    run = make_run(tmp_path, "menu-catppuccin.png", "legs", "001-hello.done.json")
    assert builder().parse_shots(run) == {}
