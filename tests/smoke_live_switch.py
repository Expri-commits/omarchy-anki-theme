#!/usr/bin/env python3
"""Smoke: one live theme switch against a scratch Anki base (ticket 17).

Not collected by pytest — it drives the real GUI stack end to end. Launches
Anki on a throwaway base with the payload tree dev-linked as
``addons21/ankiya``, flips the LIVE Omarchy theme once via ``omarchy theme
set`` (the shared omarchy-theme-set path behind the menu, CLI, and scheduler
alike — ticket 09 proved all three perform the same state-dir mutation the
watcher observes), then asserts on the applied record the add-on writes:
the switch landed live, every delivery leg ran, and the in-app apply stayed
within the 50 ms budget. Prefers a same-polarity target — the dark→dark
switch that bypasses Anki's own early-return is the critical path. The
original theme is restored and the scratch base removed on the way out.

Run on system python (the base seeding imports aqt, which mise python
lacks): ``/usr/bin/python tests/smoke_live_switch.py
[target-display-name]``. Refuses to start while any Anki instance is
running — the applied log is shared state.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib

REPO = pathlib.Path(__file__).resolve().parent.parent
PAYLOAD = REPO / "payload" / "ankiya"
sys.path.insert(0, str(REPO / "payload"))
from ankiya.palette import VAR_RULES  # noqa: E402

STATE_DIR = pathlib.Path.home() / ".local/state/omarchy/current"
THEME_NAME_FILE = STATE_DIR / "theme.name"
APPLIED_LOG = pathlib.Path.home() / ".local/state/omarchy/anki-theme/applied.jsonl"
THEMES_DIR = pathlib.Path("/usr/share/omarchy/themes")

STARTUP_TIMEOUT_S = 240.0  # scratch-base first run builds Qt caches slowly
SWITCH_TIMEOUT_S = 15.0
APPLY_BUDGET_MS = 50.0


def fail(message: str) -> None:
    print(f"FAIL: {message}")


def applied_records() -> list[dict]:
    try:
        lines = APPLIED_LOG.read_text().splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def wait_for(predicate, timeout_s: float, what: str) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for record in reversed(applied_records()):
            if predicate(record):
                return record
        time.sleep(0.1)
    raise TimeoutError(f"no applied record {what} within {timeout_s:.0f}s")


def theme_colors(dir_name: str) -> dict:
    with (THEMES_DIR / dir_name / "colors.toml").open("rb") as f:
        return tomllib.load(f)


def theme_mode(dir_name: str) -> str:
    return theme_colors(dir_name).get("mode", "light")


def to_dir_name(display_name: str) -> str:
    return display_name.lower().replace(" ", "-")


def seed_base(base: pathlib.Path) -> None:
    """Pre-create prefs21.db so profile open never blocks.

    A fresh base would otherwise sit in the first-run language dialog (a
    modal exec()) forever — no profile_did_open, no add-on start. Seeding
    flips `firstRun` off, sets `defaultLang` (None crashes
    setupLangAndBackend), and creates the User 1 row that setupProfile
    auto-loads. Uses aqt's own ProfileManager so the pickled rows are
    exactly what Anki expects — run the smoke on system python for this.
    """
    from aqt.profiles import ProfileManager

    pm = ProfileManager(base)
    pm.setupMeta()
    pm.meta["firstRun"] = False
    pm.meta["defaultLang"] = "en"
    pm.create("User 1")
    # DB.execute(sql, *a) forwards positionals to sqlite3 — pass the pickle
    # bare, never pre-wrapped (a wrapped tuple binds as one tuple param).
    pm.db.execute("update profiles set data = ? where name = '_global'", pm._pickle(pm.meta))
    pm.db.commit()


def pick_target(current: str, requested: str | None) -> str:
    """A stock theme of the same polarity as `current`, preferring the
    same-mode dark→dark / light→light critical path."""
    wanted = [t for t in list_stock() if to_dir_name(t) != current]
    if requested:
        if to_dir_name(requested) == current:
            raise SystemExit(f"target {requested!r} is the current theme")
        return requested
    same_mode = [t for t in wanted if theme_mode(to_dir_name(t)) == theme_mode(current)]
    return (same_mode or wanted)[0]


def list_stock() -> list[str]:
    out = subprocess.run(
        ["omarchy", "theme", "list"], capture_output=True, text=True, check=True
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    if subprocess.run(["pgrep", "-x", "anki"], capture_output=True).returncode == 0:
        fail("an Anki instance is already running — close it first")
        return 1
    if not STATE_DIR.exists():
        fail(f"no Omarchy state at {STATE_DIR}")
        return 1

    original = THEME_NAME_FILE.read_text().strip()
    target = pick_target(original, sys.argv[1] if len(sys.argv) > 1 else None)
    target_dir = to_dir_name(target)
    print(f"current={original!r} target={target!r} ({theme_mode(target_dir)})")

    scratch = pathlib.Path(tempfile.mkdtemp(prefix="ankiya-smoke-"))
    base = scratch / "base"
    base.mkdir()
    seed_base(base)
    (base / "addons21").mkdir()
    (base / "addons21" / "ankiya").symlink_to(PAYLOAD)
    anki_log = scratch / "anki.log"

    t_launch = time.time()
    try:
        anki = subprocess.Popen(
            ["anki", "-b", str(base)], stdout=anki_log.open("w"), stderr=subprocess.STDOUT
        )
        # Records are matched by launch-relative time, never by seq — seq is
        # a per-instance counter and the applied log is shared across runs.
        startup = wait_for(
            lambda r: r["reason"] == "startup" and r["applied_at"] > t_launch,
            STARTUP_TIMEOUT_S,
            "for the scratch instance's startup apply",
        )
        check(
            startup["theme"] == original,
            f"startup applied theme {startup['theme']!r}, expected {original!r}",
        )
        check(startup["errors"] == [], f"startup leg errors: {startup['errors']}")
        # The startup theme is the user's live one — a user theme missing
        # palette keys legitimately degrades, so require full ACCOUNTING
        # (mapped + skipped), not 51 mapped.
        check(
            startup["vars"] + startup["skipped"] == len(VAR_RULES),
            f"startup accounted {startup['vars']}+{startup['skipped']} vars, "
            f"expected {len(VAR_RULES)}",
        )

        t0 = time.time()
        subprocess.run(["omarchy", "theme", "set", target], capture_output=True, check=True)
        t_set_done = time.time()
        switched = wait_for(
            lambda r: (
                r["applied_at"] > t_launch
                and r["reason"] in ("watcher", "watcher-retry")
                and r["theme"] == target_dir
            ),
            SWITCH_TIMEOUT_S,
            f"for the live switch to {target_dir!r}",
        )
        end_to_end_ms = (switched["applied_at"] - t0) * 1000

        check(switched["errors"] == [], f"switch leg errors: {switched['errors']}")
        # The target is a stock theme: full mapping, nothing skipped.
        check(
            switched["vars"] == len(VAR_RULES) and switched["skipped"] == 0,
            f"switch mapped {switched['vars']}+{switched['skipped']} vars, "
            f"expected {len(VAR_RULES)}+0",
        )
        check(switched["views"] >= 1, "no open webview was restyled")
        check(
            switched["engine_profiles"] >= 1,
            "engine script never reached a profile (sveltekit leg dead)",
        )
        check(
            switched["dark"] == (theme_mode(target_dir) == "dark"),
            "recorded polarity does not match the target theme's mode",
        )
        check(
            switched["apply_ms"] <= APPLY_BUDGET_MS,
            f"in-app apply took {switched['apply_ms']}ms, budget {APPLY_BUDGET_MS}ms",
        )
        # Content-level proof the delivered artifact carries the target
        # palette: the regenerated stdHtml CSS (served per page build) and the
        # engine-script refresh (sveltekit) both derive from this string.
        # Pixel-level rendering proof is tier 2's job (docs/verification.md).
        delivered_css = (PAYLOAD / "web" / "ankiya.css").read_text()
        target_bg = theme_colors(target_dir)["background"].lower()
        check(
            f"--canvas: {target_bg};" in delivered_css,
            f"delivered CSS does not carry the target background {target_bg}",
        )
        print(
            f"PASS: live switch applied in {switched['apply_ms']}ms "
            f"(end-to-end {end_to_end_ms:.0f}ms, `omarchy theme set` returned "
            f"after {(t_set_done - t0) * 1000:.0f}ms)"
        )
        return 0
    except Exception as exc:
        fail(str(exc))
        if anki_log.exists():
            for line in anki_log.read_text().splitlines()[-30:]:
                print(f"  anki| {line}")
        return 1
    finally:
        if "anki" in locals():
            anki.send_signal(signal.SIGTERM)
            try:
                anki.wait(timeout=30)
            except subprocess.TimeoutExpired:
                anki.kill()
                anki.wait()
        subprocess.run(["omarchy", "theme", "set", original], capture_output=True, check=False)
        shutil.rmtree(scratch, ignore_errors=True)
        print(f"restored {original!r}, removed {scratch}")


def check(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


if __name__ == "__main__":
    sys.exit(main())
