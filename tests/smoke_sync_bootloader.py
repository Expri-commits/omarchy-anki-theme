#!/usr/bin/env python3
"""Smoke: the bootloader's update propagation against a scratch Anki base
(ticket 19; the demo leg ticket 12 promised verification).

Not collected by pytest — it drives the real GUI stack. Three legs:

  A. drift demo    install the real payload via the sync routine (the
                   service's post-consent leg), seed a user config edit in
                   the installed meta.json, drift the *bundled* tree to a
                   marked v2 (its runtime logs ``[anki_theme-v2]``), then start
                   Anki against the drifted bundle — no shell restart, no
                   service involved. Assert the boot converged: stamp equals
                   the bundled hash, the v2 code is what themed (the marker
                   in the log), the user's meta.json survived the swap, and
                   no stage dirs linger.
  B. standalone    relaunch with the bundled dir pointed nowhere: theming
                   continues from the installed copy, the stamp does not
                   move, and the standalone decision is logged.
  C. startup cost  the standing metric: a probe add-on (``zz_probe``,
                   sorted after ``anki_theme`` so its profile_did_open hook
                   fires after Anki Theme's start completed) timestamps
                   profile-open; 6 launches with the add-on enabled vs 6
                   disabled via meta.json (first of each dropped as Qt-cache
                   warmup), mean of the remaining 5. Both series pin
                   ANKI_THEME_BUNDLED_PAYLOAD to the v2 bundle so the sync-check
                   cost is measured against a real, current bundle.

Run on system python (the base seeding imports aqt): ``/usr/bin/python
tests/smoke_sync_bootloader.py``. Refuses while any Anki instance is
running. Writes only to the scratch base; the add-on's applied records and
the sync marker land in the real plugin state dir like every smoke's (the
marker is rewritten by any real install).
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
PAYLOAD = REPO / "payload" / "anki_theme"
sys.path.insert(0, str(REPO / "payload"))
sys.path.insert(0, str(REPO / "tests"))
from anki_theme import sync  # noqa: E402
from smoke_live_switch import check, fail, seed_base, wait_for  # noqa: E402

STATE_DIR = pathlib.Path.home() / ".local/state/omarchy/anki-theme"
STARTUP_TIMEOUT_S = 240.0
LAUNCH_TIMEOUT_S = 120.0
USER_META = {
    "name": "Anki Theme for Omarchy",
    "homepage": "https://github.com/Expri-commits/omarchy-anki-theme",
    "config": {"contrast_clamp": True},
}
# The probe timestamps profile_did_open after Anki Theme's hook (add-ons load in
# sorted order), so its launch→mark time contains the full add-on cost.
PROBE = (
    "import os, time\n"
    "path = os.environ.get('ANKI_THEME_PROBE_LOG')\n"
    "if path:\n"
    "    from aqt import gui_hooks\n"
    "    def _mark():\n"
    "        with open(path, 'a') as f:\n"
    "            f.write(f'{time.time()}\\n')\n"
    "    gui_hooks.profile_did_open.append(_mark)\n"
)


def make_bundled(scratch: pathlib.Path, marker: str | None) -> pathlib.Path:
    """A copy of the real payload as the plugin would bundle it (web/ is
    runtime-generated and gitignored, so a clone never carries it); a
    log-prefix marker makes the new code observable in the instance's log."""
    bundled = scratch / ("bundled-v2" if marker else "bundled-v1")
    shutil.copytree(PAYLOAD, bundled, ignore=shutil.ignore_patterns("__pycache__", "web"))
    if marker:
        runtime = bundled / "runtime.py"
        old = 'print(f"[anki_theme] {message}", flush=True)'
        new = f'print(f"[{marker}] {{message}}", flush=True)'
        text = runtime.read_text()
        check(old in text, "the runtime _log anchor moved — update the marker patch")
        runtime.write_text(text.replace(old, new, 1))
    return bundled


def launch(base: pathlib.Path, log: pathlib.Path, env_extra: dict | None = None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.Popen(
        ["anki", "-b", str(base)], stdout=log.open("w"), stderr=subprocess.STDOUT, env=env
    )


def stop(proc) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def phase_a(scratch: pathlib.Path, base: pathlib.Path) -> pathlib.Path:
    print("-- A: drift demo (bootloader converges at Anki start, no shell)")
    bundled_v1 = make_bundled(scratch, None)
    installed = base / "addons21" / "anki_theme"
    result = sync.ensure_current(bundled_v1, installed, STATE_DIR)
    check(result.status == "installed", f"install leg returned {result.status}")
    installed_meta = installed / "meta.json"
    installed_meta.write_text(json.dumps(USER_META, indent=2) + "\n")

    bundled_v2 = make_bundled(scratch, "anki_theme-v2")
    anki_log = scratch / "a.log"
    t_launch = time.time()
    anki = launch(base, anki_log, {"ANKI_THEME_BUNDLED_PAYLOAD": str(bundled_v2)})
    try:
        startup = wait_for(
            lambda r: r["reason"] == "startup" and r["applied_at"] > t_launch,
            STARTUP_TIMEOUT_S,
            "for the drifted boot's startup apply",
        )
        check(startup["errors"] == [], f"startup leg errors: {startup['errors']}")
        log_text = anki_log.read_text()
        check("[anki_theme-v2]" in log_text, "the v2 marker never ran — old code themed")
        check("sync: swapped to payload" in log_text, "the boot swap never happened")
        stamp = json.loads((installed / "payload.json").read_text())
        check(
            stamp["payloadHash"] == sync.tree_hash(bundled_v2),
            "installed stamp does not equal the drifted bundled hash",
        )
        check(
            json.loads(installed_meta.read_text()) == USER_META,
            "the user's meta.json did not survive the boot swap",
        )
        check(
            "[anki_theme-v2]" in (installed / "runtime.py").read_text(),
            "installed tree is not the drifted content",
        )
        # The swap's dot-siblings live under the base (the Anki2 root): no
        # stage may linger, and the deferred dot-old must be present until
        # this Anki exits (the bootloader's atexit removes it).
        names = [p.name for p in base.iterdir() if p.name.startswith(".anki_theme")]
        check(
            not [n for n in names if n.startswith(".anki_theme-stage-")],
            f"stage dirs linger under the Anki2 root: {names}",
        )
        check(
            any(n.startswith(".anki_theme-old-") for n in names),
            "the deferred dot-old is missing at runtime",
        )
        print(f"PASS: converged at boot, v2 live (apply {startup['apply_ms']}ms)")
    finally:
        stop(anki)
        # SIGTERM skips atexit: a follow-up pass still sees a consistent
        # tree (the dot-old's removal falls to the next run's sweep).
        follow = sync.ensure_current(bundled_v2, installed, STATE_DIR)
        check(follow.status == "current", f"post-kill pass: {follow.status}")
    return bundled_v2


def phase_b(scratch: pathlib.Path, base: pathlib.Path) -> None:
    print("-- B: standalone (plugin payload gone: theming on, no updates)")
    installed = base / "addons21" / "anki_theme"
    before = json.loads((installed / "payload.json").read_text())["payloadHash"]
    anki_log = scratch / "b.log"
    t_launch = time.time()
    anki = launch(base, anki_log, {"ANKI_THEME_BUNDLED_PAYLOAD": str(scratch / "gone")})
    try:
        startup = wait_for(
            lambda r: r["reason"] == "startup" and r["applied_at"] > t_launch,
            LAUNCH_TIMEOUT_S,
            "for the standalone boot's startup apply",
        )
        check(startup["errors"] == [], f"standalone leg errors: {startup['errors']}")
        log_text = anki_log.read_text()
        check("standalone" in log_text, "the standalone decision was never logged")
        after = json.loads((installed / "payload.json").read_text())["payloadHash"]
        check(after == before, "standalone moved the stamp — it must not update")
        print(f"PASS: standalone themed ({startup['apply_ms']}ms), stamp unchanged")
    finally:
        stop(anki)


def phase_c(scratch: pathlib.Path, base: pathlib.Path, bundled_v2: pathlib.Path) -> None:
    print("-- C: add-on startup cost (standing metric, mean of 5 pairs)")
    probe_log = scratch / "probe.log"
    probe_init = base / "addons21" / "zz_probe" / "__init__.py"
    probe_init.parent.mkdir(parents=True)
    probe_init.write_text(PROBE)
    installed_meta = base / "addons21" / "anki_theme" / "meta.json"
    meta_backup = installed_meta.read_text()
    env = {
        "ANKI_THEME_PROBE_LOG": str(probe_log),
        "ANKI_THEME_BUNDLED_PAYLOAD": str(bundled_v2),
    }

    def timed_launch() -> float:
        baseline = len(probe_log.read_text().splitlines()) if probe_log.exists() else 0
        t0 = time.time()
        anki = launch(base, scratch / "c.log", env)
        try:
            deadline = time.monotonic() + LAUNCH_TIMEOUT_S
            while time.monotonic() < deadline:
                if probe_log.exists() and len(probe_log.read_text().splitlines()) > baseline:
                    return float(probe_log.read_text().splitlines()[-1]) - t0
                if anki.poll() is not None:
                    raise AssertionError("Anki exited before the probe fired")
                time.sleep(0.02)
            raise TimeoutError("probe never fired")
        finally:
            stop(anki)

    def series() -> list[float]:
        return [timed_launch() for _ in range(6)][1:]  # drop Qt-cache warmup

    enabled = series()
    meta = json.loads(meta_backup)
    meta["disabled"] = True
    installed_meta.write_text(json.dumps(meta, indent=2) + "\n")
    try:
        disabled = series()
    finally:
        installed_meta.write_text(meta_backup)  # restore for any later use

    delta = [e - d for e, d in zip(enabled, disabled, strict=True)]
    print(
        f"     launch→open enabled {statistics.mean(enabled) * 1000:.0f}ms vs disabled "
        f"{statistics.mean(disabled) * 1000:.0f}ms; per-run deltas "
        f"{[round(d * 1000) for d in sorted(delta)]}ms"
    )
    print(f"RESULT startup-cost: mean add-on cost {statistics.mean(delta) * 1000:.1f} ms")


def main() -> int:
    if subprocess.run(["pgrep", "-x", "anki"], capture_output=True).returncode == 0:
        fail("an Anki instance is already running — close it first")
        return 1
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="anki_theme-sync-smoke-"))
    base = scratch / "base"
    base.mkdir()
    seed_base(base)
    (base / "addons21").mkdir()
    try:
        bundled_v2 = phase_a(scratch, base)
        phase_b(scratch, base)
        phase_c(scratch, base, bundled_v2)
        return 0
    except Exception as exc:
        fail(str(exc))
        for name in ("a.log", "b.log", "c.log"):
            log = scratch / name
            if log.exists():
                for line in log.read_text().splitlines()[-25:]:
                    print(f"  anki| {line}")
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        print(f"removed {scratch}")


if __name__ == "__main__":
    sys.exit(main())
