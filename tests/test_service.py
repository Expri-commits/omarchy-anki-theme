"""Tier-1 suite for the service half: gate and grant (ticket 20).

The locked behavior under test: the version floor fails closed (ticket 13),
consent is asked once and only when Anki's data dir exists (ticket 11), the
pre-consent service writes nothing at all, a granted click records consent
atomically at 0600 and mounts Sync as a /usr/bin/python subprocess (tickets
11/12), and a deleted-in-Anki add-on is offered one reinstall toast instead
of a silent resurrection (ticket 12).

The decision table runs against the pure ``decide()``/``parse_version()``/
``read_consent()`` functions; the legs that spawn things run gate.py and
grant.py as real subprocesses against a scratch Anki2 root with a PATH-
shimmed ``omarchy-version`` — the same shape the QML service drives.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from anki_theme import sync

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "service"))
import gate  # noqa: E402

GATE_PY = REPO / "service" / "gate.py"
GRANT_PY = REPO / "service" / "grant.py"
BUNDLED = REPO / "payload" / "anki_theme"
MANIFEST_VERSION = json.loads((REPO / "manifest.json").read_text())["version"]

# The gate subprocess runs on the same interpreter the service would.
RUN_PY = "/usr/bin/python"


# -- version parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4.0.1-1\n", (4, 0, 1)),
        ("4.0.1", (4, 0, 1)),
        ("4.2.0-rc", (4, 2, 0)),
        ("v5.0.0", (5, 0, 0)),
        ("10.20.30", (10, 20, 30)),
        ("4.1", (4, 1, 0)),
        ("", None),
        ("banana", None),
        ("4", None),
        ("-1.2.3", None),
    ],
)
def test_parse_version(text, expected):
    assert gate.parse_version(text) == expected


# -- the decision table ------------------------------------------------------


def decided(**overrides) -> dict:
    facts = {
        "version": (4, 0, 1),
        "version_detail": "'4.0.1'",
        "consented": True,
        "anki2_exists": True,
        "marker_exists": True,
        "installed_exists": True,
        "plugin_dir": REPO,
        "anki2_root": Path("/home/u/.local/share/Anki2"),
        "state_dir": Path("/home/u/.local/state/omarchy/anki-theme"),
    }
    facts.update(overrides)
    return gate.decide(**facts)


def test_floor_is_inclusive_at_4_0_1():
    assert decided(version=(4, 0, 1))["action"] == gate.SYNC


@pytest.mark.parametrize("version", [(4, 0, 0), (3, 8, 4), (0, 0, 1)])
def test_below_floor_is_inert(version):
    decision = decided(version=version)
    assert decision["action"] == gate.INERT
    assert "4.0.1" in decision["message"]
    assert "exec" not in decision


@pytest.mark.parametrize(
    "version_detail",
    ["'omarchy-version' not found on PATH", "'omarchy-version' exited 1", "unparseable output 'x'"],
)
def test_failed_version_command_is_inert(version_detail):
    decision = decided(version=None, version_detail=version_detail)
    assert decision["action"] == gate.INERT
    assert version_detail in decision["message"]


def test_no_anki_data_dir_is_idle_even_when_consented():
    decision = decided(anki2_exists=False)
    assert decision["action"] == gate.IDLE
    assert "exec" not in decision


def test_unconsented_asks_before_anything_else():
    # Even with marker and installed folder present (a wiped state dir on a
    # machine that had consented): consent is the first question.
    decision = decided(consented=False)
    assert decision["action"] == gate.ASK_CONSENT
    assert decision["toast"]["body"].find("addons21/anki_theme") != -1


def test_consent_grant_missing_install_still_syncs():
    # Consent landed but the grant's Sync leg never finished: the next
    # service start completes the install.
    decision = decided(marker_exists=False, installed_exists=False)
    assert decision["action"] == gate.SYNC


def test_state_wiped_addon_present_syncs():
    decision = decided(marker_exists=False, installed_exists=True)
    assert decision["action"] == gate.SYNC


def test_deleted_in_anki_offers_reinstall_never_resurrects():
    decision = decided(installed_exists=False)
    assert decision["action"] == gate.OFFER_REINSTALL
    assert decision["toast"]["body"].find("addons21/anki_theme") != -1
    assert decision["exec"] == decided()["exec"]  # the click is plain Sync


def test_execs_are_complete_prod_shaped_argv():
    anki2, state = Path("/home/u/.local/share/Anki2"), Path("/home/u/.local/state/x")
    ask = decided(
        consented=False,
        marker_exists=False,
        installed_exists=False,
        anki2_root=anki2,
        state_dir=state,
    )
    # -B: the service never writes bytecode into the plugin tree it runs
    # from (a __pycache__ there trips the shell's plugin-reload watcher).
    assert ask["exec"] == [gate.PYTHON, "-B", str(GRANT_PY), str(anki2), str(state)]

    converge = decided(anki2_root=anki2, state_dir=state)
    assert converge["exec"] == [
        gate.PYTHON,
        "-B",
        str(BUNDLED / "sync.py"),
        str(BUNDLED),
        str(anki2 / "addons21" / "anki_theme"),
        str(state),
    ]


# -- consent reading ---------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, False),  # absent
        ("not json", False),
        ("[]", False),
        ('{"granted": false}', False),
        ('{"granted": "yes"}', False),
        ('{"granted": true}', True),
        ('{"granted": true, "decidedAt": "t", "pluginVersion": "0.1.0"}', True),
    ],
)
def test_read_consent(tmp_path: Path, content, expected):
    if content is not None:
        (tmp_path / "consent.json").write_text(content)
    assert gate.read_consent(tmp_path) is expected


# -- the QML seam --------------------------------------------------------------


def test_qml_pins_every_gate_action_it_must_act_on():
    """The gate↔QML action strings are a cross-language contract with no
    compiler behind it: a renamed or added gate action under a stale
    compiled QML (basecamp/omarchy#6981 — the reason the logic lives in
    gate.py) would log and silently do nothing. This pins the acted-on
    actions into Service.qml's source, plus the two shape facts of the
    notification call: it sends via the CLI and ``--exec`` comes last
    (Omarchy 4.0.1's argv contract)."""
    qml = (REPO / "Service.qml").read_text()
    for action in (gate.SYNC, gate.ASK_CONSENT, gate.OFFER_REINSTALL):
        assert f'"{action}"' in qml, f"Service.qml no longer handles gate action '{action}'"
    assert '"omarchy", "notification", "send"' in qml
    assert qml.index('"--exec"') > qml.index('"notification", "send"')


# -- the gate as the QML drives it ---------------------------------------------


def shimmed_path(tmp_path: Path, body: str | None, *, fail: bool = False) -> str:
    """A PATH with an ``omarchy-version`` shim prepended.

    ``body=None`` with ``fail=True`` exits 1; both None means the command
    is missing entirely — a PATH with no ``omarchy-version`` anywhere.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if body is not None or fail:
        exe = bin_dir / "omarchy-version"
        script = "exit 1\n" if fail else f"echo '{body}'\n"
        exe.write_text("#!/bin/sh\n" + script)
        exe.chmod(0o755)
        return f"{bin_dir}:{os.environ['PATH']}"
    return str(bin_dir)


def run_gate(anki2: Path, state: Path, path_env: str) -> dict:
    proc = subprocess.run(
        [RUN_PY, str(GATE_PY), str(anki2), str(state)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": path_env},
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def layout(tmp_path: Path) -> dict:
    anki2 = tmp_path / "Anki2"
    (anki2 / "addons21").mkdir(parents=True)
    return {
        "anki2": anki2,
        "addons": anki2 / "addons21",
        "installed": anki2 / "addons21" / "anki_theme",
        "state": tmp_path / "state",
    }


@pytest.mark.parametrize("shim", ["4.0.1-1", "4.2.0", "v9.9.9-1"])
def test_gate_asks_for_consent_above_floor(tmp_path: Path, layout: dict, shim: str):
    decision = run_gate(layout["anki2"], layout["state"], shimmed_path(tmp_path, shim))
    assert decision["action"] == gate.ASK_CONSENT
    # The pre-consent promise, literally: nothing written anywhere.
    assert not layout["installed"].exists()
    assert not layout["state"].exists()
    assert list(layout["anki2"].iterdir()) == [layout["addons"]]


@pytest.mark.parametrize(
    ("body", "fail"),
    [("3.8.4", False), ("4.0.0", False), ("garbage", False), (None, True), (None, None)],
)
def test_gate_inert_below_floor_or_broken_version_command(tmp_path: Path, layout: dict, body, fail):
    decision = run_gate(layout["anki2"], layout["state"], shimmed_path(tmp_path, body, fail=fail))
    assert decision["action"] == gate.INERT
    assert "toast" not in decision
    assert not layout["state"].exists()


def test_gate_idle_without_anki_data_dir(tmp_path: Path):
    decision = run_gate(tmp_path / "Anki2", tmp_path / "state", shimmed_path(tmp_path, "4.0.1"))
    assert decision["action"] == gate.IDLE


# -- the click-through flow ----------------------------------------------------


def run_exec(argv: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc


def test_click_through_flow(tmp_path: Path, layout: dict):
    path_env = shimmed_path(tmp_path, "4.0.1-1")

    # Fresh flow: ask → click (the toast's exec, run exactly as the
    # notification would) → payload installed, stamped, marked.
    ask = run_gate(layout["anki2"], layout["state"], path_env)
    assert ask["action"] == gate.ASK_CONSENT
    grant = run_exec(ask["exec"])

    consent_path = layout["state"] / "consent.json"
    consent = json.loads(consent_path.read_text())
    assert consent["granted"] is True
    assert consent["pluginVersion"] == MANIFEST_VERSION
    assert consent["decidedAt"]
    assert stat.S_IMODE(consent_path.stat().st_mode) == 0o600

    assert (layout["installed"] / "__init__.py").is_file()
    stamp = json.loads((layout["installed"] / "payload.json").read_text())
    assert stamp["payloadHash"] == sync.tree_hash(BUNDLED)
    assert (layout["state"] / "installed.json").is_file()
    assert json.loads(grant.stdout)["status"] == sync.INSTALLED

    # Granted: the next service start converges instead of re-asking.
    mounted = run_gate(layout["anki2"], layout["state"], path_env)
    assert mounted["action"] == gate.SYNC
    assert json.loads(run_exec(mounted["exec"]).stdout)["status"] == sync.CURRENT

    # Deleted in Anki with the plugin kept: one reinstall offer, whose
    # click is plain Sync — never a silent resurrection.
    shutil.rmtree(layout["installed"])
    offered = run_gate(layout["anki2"], layout["state"], path_env)
    assert offered["action"] == gate.OFFER_REINSTALL
    assert json.loads(run_exec(offered["exec"]).stdout)["status"] == sync.INSTALLED
    assert (layout["installed"] / "__init__.py").is_file()
    assert run_gate(layout["anki2"], layout["state"], path_env)["action"] == gate.SYNC


def test_grant_is_idempotent_on_a_re_clicked_toast(tmp_path: Path, layout: dict):
    path_env = shimmed_path(tmp_path, "4.0.1-1")
    ask = run_gate(layout["anki2"], layout["state"], path_env)
    run_exec(ask["exec"])
    second_click = run_exec(ask["exec"])
    # A second click re-records consent (a click is a click) and Sync
    # converges on current — no second install, no damage.
    assert json.loads(second_click.stdout)["status"] == sync.CURRENT
    assert gate.read_consent(layout["state"]) is True


def test_grant_runs_sync_on_the_real_bundled_payload(tmp_path: Path, layout: dict):
    # The grant's subprocess must run the payload the plugin actually
    # ships — the bundled tree in this repo, verbatim. The oracle skips
    # what never ships independently of Sync's own rule: dev-loop bytecode.
    ask = run_gate(layout["anki2"], layout["state"], shimmed_path(tmp_path, "4.0.1"))
    run_exec(ask["exec"])

    def shippable(root: Path) -> set[str]:
        return {
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.relative_to(root).parts and p.suffix != ".pyc"
        }

    assert shippable(layout["installed"]) == shippable(BUNDLED)
