"""Tier-3 gate, Anki-down phase: everything that needs Anki stopped (ticket
23, docs/verification.md §Tier 3). Runs after test_gate_full.py stopped the
matrix instance (the ``gate3_down`` fixture) — the preflight contract forbids
concurrent Ankis, so these legs launch scratch instances of their own, each
on an isolated ``HOME`` (the runtime derives its state dir from it, so the
applied log, drift markers and sync markers never touch the real ones).

  propagation (ticket 12)  drifted bundle → boot convergence without the
                           shell; a service-mount sync racing the launch
                           stays convergent; a downgrade converges backward
  below floor (ticket 13)  the service gate under old/failing/missing
                           ``omarchy-version`` stays inert; the add-on with
                           the palette file absent or unreadable applies
                           nothing — surfaces assert against Anki's own
                           defaults, read live from aqt.colors
  consent (tickets 11/12)  gate → ask_consent; the grant helper installs;
                           no re-ask; delete in Anki → one reinstall offer;
                           standalone: theming continues, stamp frozen
  drift (ticket 15)        mocked retract-class drift → log + one tooltip,
                           second start silent; add-class log-only
  perf session             add-on startup cost (≥ 5-pair A/B mean incl. the
                           sync check) and the sync swap cost
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import statistics
import subprocess
import sys
import time

import pytest

GATE_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(GATE_DIR))
sys.path.insert(0, str(GATE_DIR.parent))

from ankiya import sync  # noqa: E402
from points import sample as _sample  # noqa: E402
from smoke_live_switch import seed_base  # noqa: E402
from smoke_sync_bootloader import make_bundled, stop  # noqa: E402

pytestmark = pytest.mark.gate_full

REPO = GATE_DIR.parent.parent
PAYLOAD = REPO / "payload" / "ankiya"
SERVICE_GATE = REPO / "service" / "gate.py"

STARTUP_TIMEOUT_S = 240.0  # scratch-home first runs build Qt/web caches
SWAP_TIMEOUT_S = 15.0


# -- shared leg machinery ---------------------------------------------------------


@pytest.fixture(scope="session")
def leg_home(gate3_down):
    """One scratch HOME shared by every down leg — only caches persist
    across legs (fontconfig, Qt), which keeps a dozen launches sane; every
    leg still gets its own base and its own service/state dirs. Removed
    with the session.

    The home carries a healthy Omarchy palette (the runtime reads its
    palette and writes its applied log under $HOME); the below-floor legs,
    whose premise is exactly a missing/broken palette, move it aside and
    restore faithfully."""
    home = gate3_down.run_dir / "legs" / "home"
    current = home / ".local/state/omarchy/current"
    (current / "theme").mkdir(parents=True)
    shutil.copy2(
        GATE_DIR.parent / "fixtures" / "themes" / "catppuccin" / "colors.toml",
        current / "theme" / "colors.toml",
    )
    (current / "theme.name").write_text("catppuccin\n")
    yield home


def leg_dir(gate3_session, name: str):
    d = gate3_session.run_dir / "legs" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def launch(base: pathlib.Path, log: pathlib.Path, env_extra: dict):
    env = {**os.environ, **env_extra}
    return subprocess.Popen(
        ["anki", "-b", str(base)],
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        env=env,
    )


def applied_records(home: pathlib.Path) -> list[dict]:
    path = home / ".local/state/omarchy/anki-theme/applied.jsonl"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def wait_applied(home: pathlib.Path, t_launch: float, timeout_s: float, what: str) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for record in reversed(applied_records(home)):
            if record["reason"] == "startup" and record["applied_at"] > t_launch:
                return record
        time.sleep(0.1)
    raise TimeoutError(f"no applied record for {what} within {timeout_s:.0f}s")


def log_text(log: pathlib.Path) -> str:
    return log.read_text() if log.exists() else ""


def wait_log_line(log: pathlib.Path, needle: str, proc, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        text = log_text(log)
        if needle in text:
            return text
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.2)
    raise AssertionError(
        f"{needle!r} never appeared in {log.name}:\n" + "\n".join(log_text(log).splitlines()[-25:])
    )


def run_service(argv: list[str], env_extra: dict | None = None, timeout: float = 60.0):
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(env_extra or {})},
        check=False,
    )


def version_shim(directory: pathlib.Path, behavior: str) -> pathlib.Path:
    """A PATH shim whose ``omarchy-version`` prints a version, fails, or is
    absent — the three below-floor shapes the gate must fail closed on."""
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "omarchy-version"
    if behavior == "old":
        binary.write_text("#!/bin/sh\necho 3.9.0\n")
    elif behavior == "current":
        binary.write_text("#!/bin/sh\necho 4.0.1-1\n")
    elif behavior == "failing":
        binary.write_text("#!/bin/sh\nexit 7\n")
    elif behavior == "missing":
        return directory
    else:
        raise ValueError(behavior)
    binary.chmod(0o755)
    return directory


# -- propagation legs (ticket 12) ---------------------------------------------------


def test_propagation_drift_converges_at_boot(gate3_down, leg_home):
    """Drift the bundled payload; start Anki **without touching the shell**;
    the bootloader's sync check must converge the install before theming —
    new code runs, the stamp equals the drifted hash, the user's meta.json
    survives, and no stage dir lingers."""
    scratch = leg_dir(gate3_down, "propagation-drift")
    base = scratch / "base"
    base.mkdir()
    seed_base(base)
    addons = base / "addons21"
    addons.mkdir()
    state = scratch / "state"

    bundled_v1 = make_bundled(scratch, None)
    installed = addons / "ankiya"
    result = sync.ensure_current(bundled_v1, installed, state)
    assert result.status == sync.INSTALLED, f"install leg: {result}"
    meta = installed / "meta.json"
    meta.write_text(json.dumps({"name": "Ankiya", "config": {"contrast_clamp": True}}))

    bundled_v2 = make_bundled(scratch, "ankiya-v2")
    anki_log = scratch / "anki.log"
    t_launch = time.time()
    proc = launch(
        base,
        anki_log,
        {"HOME": str(leg_home), "ANKIYA_BUNDLED_PAYLOAD": str(bundled_v2)},
    )
    try:
        record = wait_applied(leg_home, t_launch, STARTUP_TIMEOUT_S, "the drifted boot")
        assert record["errors"] == [], f"drifted boot apply errors: {record['errors']}"
        text = log_text(anki_log)
        assert "[ankiya-v2]" in text, "the v2 marker never ran — old code themed"
        assert "sync: swapped to payload" in text, "the boot swap never happened"
        stamp = json.loads((installed / "payload.json").read_text())
        assert stamp["payloadHash"] == sync.tree_hash(bundled_v2), (
            "installed stamp does not equal the drifted bundled hash"
        )
        assert json.loads(meta.read_text())["config"] == {"contrast_clamp": True}, (
            "the user's meta.json did not survive the boot swap"
        )
        assert "[ankiya-v2]" in (installed / "runtime.py").read_text(), (
            "the installed tree is not the drifted content"
        )
        names = [p.name for p in base.iterdir() if p.name.startswith(".ankiya")]
        assert not [n for n in names if n.startswith(".ankiya-stage-")], (
            f"stage dirs linger under the Anki2 root: {names}"
        )
        assert any(n.startswith(".ankiya-old-") for n in names), (
            "the deferred dot-old is missing at runtime"
        )
    finally:
        stop(proc)
    follow = sync.ensure_current(bundled_v2, installed, state)
    assert follow.status == sync.CURRENT, f"post-kill pass: {follow.status}"


def test_propagation_race_stays_convergent(gate3_down, leg_home):
    """The race the pid-guard and re-derive logic exist for: a service-mount
    sync (fresh subprocess, the gate's own argv shape) lands while Anki is
    mid-launch running its own bootloader sync. Whatever interleaving happens,
    the tree must end convergent and the session must theme."""
    scratch = leg_dir(gate3_down, "propagation-race")
    base = scratch / "base"
    base.mkdir()
    seed_base(base)
    addons = base / "addons21"
    addons.mkdir()
    state = scratch / "state"

    bundled_v1 = make_bundled(scratch, None)
    installed = addons / "ankiya"
    assert sync.ensure_current(bundled_v1, installed, state).status == sync.INSTALLED
    bundled_v2 = make_bundled(scratch, "ankiya-v2")

    anki_log = scratch / "anki.log"
    t_launch = time.time()
    proc = launch(
        base,
        anki_log,
        {"HOME": str(leg_home), "ANKIYA_BUNDLED_PAYLOAD": str(bundled_v2)},
    )
    try:
        # The service-mount shape: /usr/bin/python -B sync.py, the plugin's
        # own payload tree as the bundled dir — during Anki's startup.
        mount = run_service(
            [
                "/usr/bin/python",
                "-B",
                str(PAYLOAD / "sync.py"),
                str(bundled_v2),
                str(installed),
                str(state),
            ],
            timeout=120.0,
        )
        assert mount.returncode == 0, f"service-mount sync crashed: {mount.stderr[-400:]}"
        decision = json.loads(mount.stdout.strip().splitlines()[-1])
        assert decision["status"] in (sync.SWAPPED, sync.CURRENT, sync.INSTALLED), decision

        record = wait_applied(leg_home, t_launch, STARTUP_TIMEOUT_S, "the raced boot")
        assert record["errors"] == [], f"raced boot apply errors: {record['errors']}"
    finally:
        stop(proc)

    follow = sync.ensure_current(bundled_v2, installed, state)
    assert follow.status == sync.CURRENT, f"the tree did not converge: {follow}"
    stamp = json.loads((installed / "payload.json").read_text())
    assert stamp["payloadHash"] == sync.tree_hash(bundled_v2)
    leftovers = [p.name for p in base.iterdir() if p.name.startswith(".ankiya")]
    assert not leftovers, f"scratch dirs survived the settled tree: {leftovers}"


def test_propagation_downgrade_converges_backward(gate3_down, leg_home):
    """The plugin is the truth in both directions: an older bundled payload
    pulls the installed tree back (deliberate, ticket 12) — v1 code themes."""
    scratch = leg_dir(gate3_down, "propagation-downgrade")
    base = scratch / "base"
    base.mkdir()
    seed_base(base)
    addons = base / "addons21"
    addons.mkdir()
    state = scratch / "state"

    bundled_v1 = make_bundled(scratch, None)
    bundled_v2 = make_bundled(scratch, "ankiya-v2")
    installed = addons / "ankiya"
    assert sync.ensure_current(bundled_v2, installed, state).status == sync.INSTALLED

    anki_log = scratch / "anki.log"
    t_launch = time.time()
    proc = launch(
        base,
        anki_log,
        {"HOME": str(leg_home), "ANKIYA_BUNDLED_PAYLOAD": str(bundled_v1)},
    )
    try:
        record = wait_applied(leg_home, t_launch, STARTUP_TIMEOUT_S, "the downgrade boot")
        assert record["errors"] == [], f"downgrade boot apply errors: {record['errors']}"
        text = log_text(anki_log)
        assert "sync: swapped to payload" in text, "the downgrade swap never happened"
        assert "[ankiya-v2]" not in text, "v2 code ran after a backward convergence"
        stamp = json.loads((installed / "payload.json").read_text())
        assert stamp["payloadHash"] == sync.tree_hash(bundled_v1), (
            "the installed stamp did not converge backward to v1"
        )
    finally:
        stop(proc)


# -- below-floor legs (ticket 13) -----------------------------------------------------


def test_below_floor_service_gates_inert(gate3_down, tmp_path):
    """Old, failing, and missing ``omarchy-version`` all gate the service
    inert: one decision, no exec, no toast — fail closed (the QML acts only
    on the decision's exec/toast, the contract tier 1 pins)."""
    anki2 = tmp_path / "Anki2"
    anki2.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    for behavior in ("old", "failing", "missing"):
        shim = version_shim(tmp_path / f"shim-{behavior}", behavior)
        gate_path = str(shim) if behavior == "missing" else f"{shim}:{os.environ['PATH']}"
        proc = run_service(
            ["/usr/bin/python", "-B", str(SERVICE_GATE), str(anki2), str(state)],
            env_extra={"PATH": gate_path},
        )
        assert proc.returncode == 0, f"[{behavior}] gate crashed: {proc.stderr[-400:]}"
        decision = json.loads(proc.stdout.strip().splitlines()[-1])
        assert decision["action"] == "inert", f"[{behavior}] {decision}"
        assert "exec" not in decision and "toast" not in decision, f"[{behavior}] {decision}"
        assert "4.0.1" in decision["message"], f"[{behavior}] inert message lacks the floor"


def _inert_addon_instance(scratch: pathlib.Path, leg_home: pathlib.Path):
    """A scratch Anki whose palette file is absent or unreadable — the add-on
    must apply nothing either way. The theme preference pins dark (the
    polarity the below-floor sample points characterize), and the caller
    sabotages/restores the palette around the launch. Returns the launch env
    and the ctl dir."""
    base = scratch / "base"
    base.mkdir(parents=True)
    from aqt.theme import Theme

    seed_base(base, theme=Theme.DARK)
    addons = base / "addons21"
    addons.mkdir()
    (addons / "ankiya").symlink_to(PAYLOAD)
    shutil.copytree(
        GATE_DIR / "gate_addon",
        addons / "zz_gate_control",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    ctl = scratch / "ctl"
    ctl.mkdir()
    return {
        "HOME": str(leg_home),
        "ANKIYA_BUNDLED_PAYLOAD": str(PAYLOAD),
        "GATE_CTL_DIR": str(ctl),
    }, ctl


@pytest.mark.parametrize("palette", ["absent", "unreadable"])
def test_below_floor_addon_applies_nothing(gate3_down, gate3_session, leg_home, palette):
    """No palette (Omarchy absent or below the floor) and an unreadable one
    both leave Anki on its own defaults: the guard/crash log line fires, no
    applied record lands, and the deck canvas samples against aqt.colors' own
    dark slot. (The matrix instance is stopped; its session object only lends
    the artifact dir, the capture method and the offset math.)"""
    from gate_harness import DATA_DIR

    scratch = leg_dir(gate3_down, f"below-floor-{palette}")
    env, ctl = _inert_addon_instance(scratch, leg_home)
    palette_file = leg_home / ".local/state/omarchy/current/theme/colors.toml"
    aside = scratch / "palette-aside.toml"
    if palette == "absent":
        shutil.move(str(palette_file), aside)
    else:
        palette_file.chmod(0o000)

    anki_log = scratch / "anki.log"
    t_launch = time.time()
    proc = launch(scratch / "base", anki_log, env)
    try:
        hello = ctl / "000-hello.cmd"
        hello.write_text(json.dumps({"cmd": "hello"}))
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if (ctl / "000-hello.done").exists():
                break
            if proc.poll() is not None:
                raise AssertionError("Anki exited before hello")
            time.sleep(0.2)
        else:
            raise AssertionError("the gate add-on never answered hello")

        needle = (
            "no palette at"
            if palette == "absent"
            else "startup apply crashed — Anki keeps its own theming"
        )
        wait_log_line(anki_log, needle, proc, 60.0)
        assert not [r for r in applied_records(leg_home) if r["applied_at"] > t_launch], (
            "an applied record landed despite the missing palette"
        )

        # Deck canvas against Anki's own defaults — read live from aqt.colors
        # in this process, never remembered hexes (the inert add-on leaves
        # Anki's own theming on screen).
        sample_map = json.loads((DATA_DIR / "26.08.1" / "sample_points.json").read_text())
        deck_spec = sample_map["probes"]["deck"]
        js = (DATA_DIR / "26.08.1" / deck_spec["js"]).read_text()
        probe_cmd = ctl / "001-probe.cmd"
        probe_cmd.write_text(json.dumps({"cmd": "probe", "target": "main", "js": js}))
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline and not (ctl / "001-probe.done").exists():
            time.sleep(0.2)
        result = json.loads((ctl / "001-probe.done").read_text())
        assert result["ok"], result
        (gate3_session.run_dir / f"probe-below-floor-{palette}.json").write_text(
            json.dumps(result, indent=1)
        )

        from oracles import anki_default as _default
        from sampling import Shot

        probe = {"window": result["window"], "views": result["views"], "dom": result["dom"]}
        shot = Shot(gate3_session.capture("main", f"below-floor-{palette}"))
        night = sample_map["below_floor"]["night_mode"]
        expected = _default(sample_map["below_floor"]["deck_canvas"], night)
        xy, sampled = _sample(gate3_session, probe, "deck", "canvas", shot, expected)
        print(
            f"below-floor[{palette}]: deck canvas rgb{sampled} matches Anki's own "
            f"{sample_map['below_floor']['deck_canvas']} dark slot at {xy}"
        )
    finally:
        stop(proc)
        # The shared leg home's palette goes back exactly as it was — later
        # legs (standalone, drift, startup cost) launch against it.
        if palette == "absent":
            shutil.move(str(aside), str(palette_file))
        else:
            palette_file.chmod(0o644)


# -- consent / reinstall / standalone smoke (tickets 11/12) ----------------------------


def test_consent_reinstall_standalone(gate3_down):
    """The whole consent lifecycle through the real helpers: gate asks once,
    the toast's click (grant.py) records consent then installs, the next
    service start mounts Sync without re-asking, an Anki-side delete earns
    exactly one reinstall offer, and a missing bundle leaves the installed
    tree standalone (stamp frozen, theming is the runtime's business)."""
    scratch = leg_dir(gate3_down, "consent")
    anki2 = scratch / "Anki2"
    (anki2 / "addons21").mkdir(parents=True)
    state = scratch / "state"
    installed = anki2 / "addons21" / "ankiya"
    shim = version_shim(scratch / "shim", "current")
    gate_env = {"PATH": f"{shim}:{os.environ['PATH']}"}

    def gate() -> dict:
        proc = run_service(
            ["/usr/bin/python", "-B", str(SERVICE_GATE), str(anki2), str(state)],
            env_extra=gate_env,
        )
        assert proc.returncode == 0, f"gate crashed: {proc.stderr[-400:]}"
        return json.loads(proc.stdout.strip().splitlines()[-1])

    # 1 — no consent recorded: ask, and the exec is the grant helper itself.
    decision = gate()
    assert decision["action"] == "ask_consent", decision
    assert "addons21/ankiya" in decision["toast"]["body"], decision
    argv = decision["exec"]
    assert argv[0] == "/usr/bin/python" and argv[1] == "-B" and argv[2].endswith("grant.py")

    # 2 — the click: consent lands first (0600), then the install.
    grant = run_service(argv)
    assert grant.returncode == 0, f"grant failed: {grant.stderr[-400:]}"
    consent = json.loads((state / "consent.json").read_text())
    assert consent["granted"] is True and "pluginVersion" in consent
    assert (state / "consent.json").stat().st_mode & 0o777 == 0o600, "consent not 0600"
    assert installed.is_dir(), "the grant never installed the add-on"
    stamp = json.loads((installed / "payload.json").read_text())
    assert stamp["payloadHash"] == sync.tree_hash(PAYLOAD), "installed a foreign tree"
    assert json.loads((installed / "meta.json").read_text()) == json.loads(
        (PAYLOAD / "meta.json").read_text()
    ), "meta.json is not the minimal shipped one"
    assert (state / "installed.json").is_file(), "the we-installed marker is missing"

    # 3 — next service start: no re-ask, straight to Sync.
    decision = gate()
    assert decision["action"] == "sync", decision
    assert decision["exec"][2].endswith("sync.py")

    # 4 — deleted inside Anki (folder gone, marker present): one offer.
    shutil.rmtree(installed)
    decision = gate()
    assert decision["action"] == "offer_reinstall", decision
    assert "addons21/ankiya" in decision["toast"]["body"], decision
    reinstall = run_service(decision["exec"])
    assert reinstall.returncode == 0, f"reinstall sync failed: {reinstall.stderr[-400:]}"
    assert installed.is_dir(), "the reinstall never landed"
    assert gate()["action"] == "sync", "the reinstall did not settle the decision"

    # 5 — standalone: the bundle gone, the installed tree never touched.
    result = sync.ensure_current(scratch / "no-bundle", installed, state)
    assert result.status == sync.STANDALONE, f"standalone decision: {result}"
    assert json.loads((installed / "payload.json").read_text())["payloadHash"] == sync.tree_hash(
        PAYLOAD
    )


def test_standalone_theming_continues(gate3_down, leg_home):
    """Standalone at runtime: with the bundle pointed nowhere, the installed
    copy still themes (palette from disk, nowhere else) and the stamp never
    moves — the plugin's absence only stops updates."""
    scratch = leg_dir(gate3_down, "standalone")
    base = scratch / "base"
    base.mkdir()
    seed_base(base)
    addons = base / "addons21"
    addons.mkdir()
    state = scratch / "state"
    installed = addons / "ankiya"
    bundled_v1 = make_bundled(scratch, None)
    assert sync.ensure_current(bundled_v1, installed, state).status == sync.INSTALLED
    before = json.loads((installed / "payload.json").read_text())["payloadHash"]

    anki_log = scratch / "anki.log"
    t_launch = time.time()
    proc = launch(
        base,
        anki_log,
        {"HOME": str(leg_home), "ANKIYA_BUNDLED_PAYLOAD": str(scratch / "gone")},
    )
    try:
        record = wait_applied(leg_home, t_launch, STARTUP_TIMEOUT_S, "the standalone boot")
        assert record["errors"] == [], f"standalone apply errors: {record['errors']}"
        assert "standalone" in log_text(anki_log), "the standalone decision was never logged"
        after = json.loads((installed / "payload.json").read_text())["payloadHash"]
        assert after == before, "standalone moved the stamp — it must not update"
    finally:
        stop(proc)


# -- the drift smoke (ticket 15) --------------------------------------------------------


def test_drift_retract_surfaces_once(gate3_down, leg_home):
    """Retract-class inventory drift: one log line + one transient tooltip
    on the first start; the state-dir signature dedup keeps the second start
    silent. The tooltip copy is the bundled one (a bundle dir exists)."""
    from ankiya.drift import MARKER, RESTORE_BUNDLED

    scratch = leg_dir(gate3_down, "drift-retract")
    base = scratch / "base"
    base.mkdir()
    seed_base(base)
    addons = base / "addons21"
    addons.mkdir()
    (addons / "ankiya").symlink_to(PAYLOAD)
    shutil.copytree(GATE_DIR / "drift_addon", addons / "aa_gate_drift")
    drift_log = scratch / "tooltips.jsonl"

    env = {
        "HOME": str(leg_home),
        "ANKIYA_BUNDLED_PAYLOAD": str(PAYLOAD),
        "ANKIYA_DRIFT_MODE": "retract",
        "ANKIYA_DRIFT_LOG": str(drift_log),
    }
    anki_log = scratch / "first.log"
    t_launch = time.time()
    proc = launch(base, anki_log, env)
    try:
        wait_applied(leg_home, t_launch, STARTUP_TIMEOUT_S, "the drifted start")
        text = log_text(anki_log)
        assert "aqt color vars drifted from the snapshot" in text, text[-2000:]
        assert "retracted 1 (CANVAS_CODE)" in text, text[-2000:]
    finally:
        stop(proc)

    lines = [json.loads(line) for line in drift_log.read_text().splitlines()]
    assert len(lines) == 1, f"expected exactly one tooltip, got {lines}"
    assert "renamed or removed" in lines[0]["text"] and RESTORE_BUNDLED in lines[0]["text"]
    marker = json.loads((leg_home / ".local/state/omarchy/anki-theme" / MARKER).read_text())
    assert marker["signatures"] == ["CANVAS_CODE"], marker

    anki_log = scratch / "second.log"
    t_launch = time.time()
    proc = launch(base, anki_log, env)
    try:
        wait_applied(leg_home, t_launch, STARTUP_TIMEOUT_S, "the dedup start")
        text = log_text(anki_log)
        assert "signature already surfaced — tooltip skipped" in text, text[-2000:]
    finally:
        stop(proc)
    lines = [json.loads(line) for line in drift_log.read_text().splitlines()]
    assert len(lines) == 1, "the second start tooltiped again — the dedup marker failed"


def test_drift_add_is_log_only(gate3_down, leg_home):
    """Additive churn nags nobody: the log line fires, no tooltip, no marker."""
    scratch = leg_dir(gate3_down, "drift-add")
    base = scratch / "base"
    base.mkdir()
    seed_base(base)
    addons = base / "addons21"
    addons.mkdir()
    (addons / "ankiya").symlink_to(PAYLOAD)
    shutil.copytree(GATE_DIR / "drift_addon", addons / "aa_gate_drift")
    drift_log = scratch / "tooltips.jsonl"

    env = {
        "HOME": str(leg_home),
        "ANKIYA_BUNDLED_PAYLOAD": str(PAYLOAD),
        "ANKIYA_DRIFT_MODE": "add",
        "ANKIYA_DRIFT_LOG": str(drift_log),
    }
    anki_log = scratch / "anki.log"
    t_launch = time.time()
    proc = launch(base, anki_log, env)
    try:
        wait_applied(leg_home, t_launch, STARTUP_TIMEOUT_S, "the add-drift start")
        text = log_text(anki_log)
        assert "aqt color vars drifted from the snapshot" in text, text[-2000:]
        assert "added 1 (ANKIYA_GATE_EXTRA)" in text, text[-2000:]
        assert not drift_log.exists(), "add-class drift must never tooltip"
        assert not (leg_home / ".local/state/omarchy/anki-theme/drift_seen.json").exists(), (
            "add-class drift must never record a signature"
        )
    finally:
        stop(proc)


# -- the perf session's non-live legs ------------------------------------------------


def test_addon_startup_cost_standing(gate3_down, leg_home):
    """The standing startup metric's session: launch→profile-open with the
    add-on enabled vs disabled (meta.json ``disabled``), 6 launches each,
    first of every series dropped as Qt-cache warmup, mean of the remaining
    5 pairs. The probe add-on loads after ``ankiya`` (sorted ``zz_``), so its
    hook timestamps a profile open that already carried the whole add-on
    cost — imports, bootloader sync check, hook wiring, startup apply."""
    scratch = leg_dir(gate3_down, "startup-cost")
    base = scratch / "base"
    base.mkdir()
    seed_base(base)
    addons = base / "addons21"
    addons.mkdir()
    installed = addons / "ankiya"
    bundled = make_bundled(scratch, None)
    assert sync.ensure_current(bundled, installed, scratch / "state").status == sync.INSTALLED

    probe_log = scratch / "probe.log"
    probe_init = addons / "zz_probe" / "__init__.py"
    probe_init.parent.mkdir()
    probe_init.write_text(
        "import os, time\n"
        "path = os.environ.get('ANKIYA_PROBE_LOG')\n"
        "if path:\n"
        "    from aqt import gui_hooks\n"
        "    def _mark():\n"
        "        with open(path, 'a') as f:\n"
        "            f.write(f'{time.time()}\\n')\n"
        "    gui_hooks.profile_did_open.append(_mark)\n"
    )

    meta_path = installed / "meta.json"
    meta_backup = meta_path.read_text()
    env = {
        "HOME": str(leg_home),
        "ANKIYA_BUNDLED_PAYLOAD": str(bundled),
        "ANKIYA_PROBE_LOG": str(probe_log),
    }

    def timed_launch() -> float:
        baseline = len(probe_log.read_text().splitlines()) if probe_log.exists() else 0
        t0 = time.time()
        proc = launch(base, scratch / "anki.log", env)
        try:
            deadline = time.monotonic() + STARTUP_TIMEOUT_S
            while time.monotonic() < deadline:
                if probe_log.exists() and len(probe_log.read_text().splitlines()) > baseline:
                    return float(probe_log.read_text().splitlines()[-1]) - t0
                if proc.poll() is not None:
                    raise AssertionError("Anki exited before the probe fired")
                time.sleep(0.02)
            raise TimeoutError("the probe never fired")
        finally:
            stop(proc)

    def series() -> list[float]:
        return [timed_launch() for _ in range(6)][1:]

    enabled = series()
    meta = json.loads(meta_backup)
    meta["disabled"] = True
    meta_path.write_text(json.dumps(meta, indent=2))
    try:
        disabled = series()
    finally:
        meta_path.write_text(meta_backup)

    deltas = [e - d for e, d in zip(enabled, disabled, strict=True)]
    mean_ms = statistics.mean(deltas) * 1000
    assert mean_ms < 500, (
        f"gross startup regression: add-on cost {mean_ms:.0f} ms mean "
        "(the standing metric is tens of ms — investigate, never tune away)"
    )
    (gate3_down.run_dir / "perf-startup-cost.json").write_text(
        json.dumps(
            {
                "enabled_launch_ms": [round(v * 1000, 1) for v in enabled],
                "disabled_launch_ms": [round(v * 1000, 1) for v in disabled],
                "per_pair_deltas_ms": [round(d * 1000, 1) for d in deltas],
                "mean_addon_cost_ms": mean_ms,
            },
            indent=1,
        )
    )
    print(f"perf: add-on startup cost {mean_ms:.1f} ms mean (5 pairs)")


def test_sync_swap_cost(gate3_down):
    """The sync half's duty (ticket 12): install and swap costs on tmp-disk
    trees, the ticket-19 method — install as a 20-run mean, swap single-shot
    (it fires only on drift in production)."""
    scratch = leg_dir(gate3_down, "swap-cost")
    bundled_v1 = make_bundled(scratch, None)
    bundled_v2 = make_bundled(scratch, "ankiya-v2")
    state = scratch / "state"

    installs = []
    for index in range(20):
        target = scratch / f"addons{index}" / "ankiya"
        target.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        result = sync.ensure_current(bundled_v1, target, state)
        installs.append((time.perf_counter() - t0) * 1000)
        assert result.status == sync.INSTALLED

    installed = scratch / "addons0" / "ankiya"
    t0 = time.perf_counter()
    result = sync.ensure_current(bundled_v2, installed, state)
    swap_ms = (time.perf_counter() - t0) * 1000
    assert result.status == sync.SWAPPED

    current_ms = None
    runs = []
    for _ in range(50):
        t0 = time.perf_counter()
        sync.ensure_current(bundled_v2, installed, state)
        runs.append((time.perf_counter() - t0) * 1000)
    current_ms = statistics.mean(runs)

    (gate3_down.run_dir / "perf-sync-costs.json").write_text(
        json.dumps(
            {
                "install_mean_ms": statistics.mean(installs),
                "swap_ms": swap_ms,
                "current_check_mean_ms": current_ms,
            },
            indent=1,
        )
    )
    print(
        f"perf: sync costs — install {statistics.mean(installs):.2f} ms mean, "
        f"swap {swap_ms:.2f} ms, current-check {current_ms:.2f} ms mean"
    )
