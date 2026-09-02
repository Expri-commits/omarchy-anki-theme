"""Tier 1 — the runtime's untrusted-input hardening (ticket 27: H7, H8).

The helpers under test are module-level functions on ``anki_theme.runtime``
(the capped palette read, the applied-log rotation) so pytest reaches them
without touching the Qt glue. The module imports aqt at its top, so every
test imports it lazily inside the function — the ``tests/test_drift.py``
pattern; ``import aqt`` works headless on the dev machine's system python.
"""

from pathlib import Path

import pytest


def runtime():
    """Lazy headless import of the runtime module (test_drift.py:63)."""
    import anki_theme.runtime as runtime_module

    return runtime_module


def point_record_at(module, monkeypatch: pytest.MonkeyPatch, state_dir: Path) -> Path:
    """Aim the record seam at a scratch state dir instead of the live
    ``~/.local/state/omarchy/anki-theme`` the globals point at."""
    log = state_dir / "applied.jsonl"
    monkeypatch.setattr(module, "PLUGIN_STATE_DIR", state_dir)
    monkeypatch.setattr(module, "APPLIED_LOG", log)
    return log


def record(seq: int) -> dict:
    """A minimal record carrying every key the applied log line formats."""
    return {
        "seq": seq,
        "theme": "test-theme",
        "dark": True,
        "reason": "test",
        "vars": 0,
        "views": 0,
        "apply_ms": 0.0,
    }


# -- the pins --------------------------------------------------------------------


def test_caps_are_pinned() -> None:
    """Silent loosening must trip tier 1: a real palette is 26 keys ≈ 1–2 KB,
    so 64 KiB is three orders of magnitude above it; 1 MiB of applied log is
    far above anything a gate session produces."""
    module = runtime()
    assert module.PALETTE_CAP_BYTES == 64 * 1024
    assert module.APPLIED_ROTATE_BYTES == 1024 * 1024


# -- H7: the capped palette read ---------------------------------------------------


def test_palette_refusal_is_an_unreadable_palette_to_callers() -> None:
    """PaletteTooLarge subclasses OSError, so apply()'s existing
    catch-and-keep-last-theming guards absorb it with no caller changes."""
    assert issubclass(runtime().PaletteTooLarge, OSError)


def test_palette_read_accepts_a_normal_palette(tmp_path: Path) -> None:
    module = runtime()
    path = tmp_path / "colors.toml"
    text = 'mode = "dark"\nbg = "#1d2021"\nfg = "#fbf1c7"\n'
    path.write_text(text)
    assert module.read_palette_capped(path) == text


def test_palette_read_accepts_exactly_the_cap(tmp_path: Path) -> None:
    """The boundary: cap bytes read fine; the read refuses only past it."""
    module = runtime()
    body = 'mode = "dark"\n'
    text = body + "#" * (module.PALETTE_CAP_BYTES - len(body))
    path = tmp_path / "colors.toml"
    path.write_bytes(text.encode("utf-8"))
    assert path.stat().st_size == module.PALETTE_CAP_BYTES
    assert module.read_palette_capped(path) == text


def test_palette_read_refuses_over_the_cap(tmp_path: Path) -> None:
    """The refusal is the exact outcome: PaletteTooLarge, and none of the
    hostile content is decoded, held, or handed to the caller."""
    module = runtime()
    path = tmp_path / "colors.toml"
    path.write_bytes(b"x" * (module.PALETTE_CAP_BYTES + 1))
    with pytest.raises(module.PaletteTooLarge):
        module.read_palette_capped(path)


def test_palette_decode_matches_read_text(tmp_path: Path) -> None:
    module = runtime()
    path = tmp_path / "colors.toml"
    path.write_bytes('fg = "#fbf1c7"  # größe\n'.encode())
    assert module.read_palette_capped(path) == path.read_text()


def test_apply_refuses_oversize_on_the_unreadable_palette_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over-cap palette: the read's refusal escapes apply() exactly as any
    unreadable palette would — the callers' guards log the traceback (whose
    message names the cap) and keep the last theming; nothing reaches CSS."""
    module = runtime()
    over = tmp_path / "colors.toml"
    over.write_bytes(b"x" * (module.PALETTE_CAP_BYTES + 1))
    monkeypatch.setattr(module, "PALETTE_FILE", over)
    with pytest.raises(module.PaletteTooLarge):
        module.runtime.apply("test")


# -- H8: the applied-log rotation --------------------------------------------------


def test_rotation_past_the_threshold_keeps_one_generation(tmp_path: Path) -> None:
    module = runtime()
    log = tmp_path / "applied.jsonl"
    grown = "x" * (module.APPLIED_ROTATE_BYTES + 1)
    log.write_text(grown)
    assert module.rotate_applied_log(log) is True
    assert not log.exists()
    assert (tmp_path / "applied.jsonl.1").read_text() == grown
    assert not (tmp_path / "applied.jsonl.2").exists()


def test_rotation_replaces_a_preexisting_generation(tmp_path: Path) -> None:
    module = runtime()
    log = tmp_path / "applied.jsonl"
    (tmp_path / "applied.jsonl.1").write_text("old generation\n")
    fresh = "new generation\n" + "x" * module.APPLIED_ROTATE_BYTES
    log.write_text(fresh)
    assert module.rotate_applied_log(log) is True
    assert (tmp_path / "applied.jsonl.1").read_text() == fresh


def test_rotation_skips_absent_at_and_under_the_threshold(tmp_path: Path) -> None:
    module = runtime()
    log = tmp_path / "applied.jsonl"
    assert module.rotate_applied_log(log) is False
    log.write_text("x" * module.APPLIED_ROTATE_BYTES)  # exactly at: exceeds is strict
    assert module.rotate_applied_log(log) is False
    assert log.exists() and not (tmp_path / "applied.jsonl.1").exists()


def test_rotation_failure_raises_in_the_helper_for_the_guard(tmp_path: Path) -> None:
    """Portable Linux injection: a directory parked on the .1 slot makes
    os.replace fail. The helper stays honest and raises — _record_apply's
    existing OSError guard is what keeps that away from theming."""
    module = runtime()
    log = tmp_path / "applied.jsonl"
    log.write_text("x" * (module.APPLIED_ROTATE_BYTES + 1))
    (tmp_path / "applied.jsonl.1").mkdir()
    with pytest.raises(OSError):
        module.rotate_applied_log(log)


def test_record_apply_rotates_then_appends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real seam: a grown log rotates once, the fresh record lands in a
    new applied.jsonl, and only a single generation exists."""
    module = runtime()
    log = point_record_at(module, monkeypatch, tmp_path)
    grown = "x" * (module.APPLIED_ROTATE_BYTES + 1)
    log.write_text(grown)
    module.runtime._record_apply(record(7))
    assert (tmp_path / "applied.jsonl.1").read_text() == grown
    assert not (tmp_path / "applied.jsonl.2").exists()
    fresh = log.read_text()
    assert fresh.count("\n") == 1 and '"seq": 7' in fresh


def test_record_apply_survives_a_failed_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rotation sits inside the record's existing OSError guard: a failed
    rotation logs the guard line and never raises past the caller — theming
    is untouched."""
    module = runtime()
    log = point_record_at(module, monkeypatch, tmp_path)
    log.write_text("x" * (module.APPLIED_ROTATE_BYTES + 1))
    (tmp_path / "applied.jsonl.1").mkdir()
    module.runtime._record_apply(record(1))
    assert "could not append the applied record" in capsys.readouterr().out
    assert (tmp_path / "applied.jsonl.1").is_dir()


def test_record_apply_appends_below_the_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At real sizes rotation is invisible: the record appends, no .1."""
    module = runtime()
    log = point_record_at(module, monkeypatch, tmp_path)
    module.runtime._record_apply(record(1))
    text = log.read_text()
    assert text.count("\n") == 1 and '"seq": 1' in text
    assert not (tmp_path / "applied.jsonl.1").exists()
