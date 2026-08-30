"""Tier-1 suite for the sync routine and bootloader shape (ticket 19).

The protocol itself is ticket 12's: stamp = content hash of the bundled
tree, swap = dot-sibling stage + double rename with meta.json the one
carried file, recovery before sweep, refuse anything not verifiably ours.
These tests exercise the routine as pure functions over tmp paths — no Anki,
no GUI — plus the real repo payload tree once, so the shipped bundle is
proven stampable end to end.
"""

import errno
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from ankiya import sync
from ankiya.sync import OLD_PREFIX, STAGE_PREFIX

REPO = Path(__file__).resolve().parent.parent
REAL_PAYLOAD = REPO / "payload" / "ankiya"

IDENTITY = {
    "schema": 1,
    "product": "ankiya",
    "pluginId": "io.github.expri-commits.anki-theme",
    "version": "0.1.0",
}

# A minimal but honest bundle: identity + defaults + code. The routine is
# content-agnostic, so only the identity file's shape matters.
V1_FILES = {
    "payload.json": json.dumps(IDENTITY, indent=2) + "\n",
    "meta.json": '{"name": "Ankiya", "homepage": "https://example.com"}\n',
    "config.json": '{"contrast_clamp": true}\n',
    "__init__.py": "# v1\n",
    "runtime.py": "# v1\n",
}
V2_FILES = {**V1_FILES, "__init__.py": "# v2\n", "cssgen.py": "# new in v2\n"}

# Scratch names a dead owner: hand-crafted crash debris the sweep may touch.
DEAD_STAGE = STAGE_PREFIX + "deadbeef-0-0"
DEAD_OLD = OLD_PREFIX + "deadbeef-0-0"


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def layout(tmp_path: Path) -> dict:
    """An Anki2-shaped scratch: bundled payload, empty addons21, state dir."""
    bundled = write_tree(tmp_path / "bundled", V1_FILES)
    addons = tmp_path / "Anki2" / "addons21"
    addons.mkdir(parents=True)
    state = tmp_path / "state"
    return {
        "bundled": bundled,
        "installed": addons / "ankiya",
        "addons": addons,
        "scratch": tmp_path / "Anki2",
        "state": state,
    }


def run(layout: dict, **kwargs) -> sync.SyncResult:
    return sync.ensure_current(layout["bundled"], layout["installed"], layout["state"], **kwargs)


def stamp_of(layout: dict) -> dict:
    return json.loads((layout["installed"] / "payload.json").read_text())


def scratch_names(layout: dict) -> list[str]:
    return [p.name for p in layout["scratch"].iterdir() if p.name[0] == "."]


def seed_drift(layout: dict) -> None:
    """Ensure the installed copy exists, then advance the bundle to v2."""
    assert run(layout).status in ("installed", "current")
    write_tree(layout["bundled"], V2_FILES)


# -- the stamp ---------------------------------------------------------------


def test_tree_hash_deterministic_order_independent(tmp_path: Path):
    one = write_tree(tmp_path / "a", V1_FILES)
    two = write_tree(tmp_path / "b", dict(reversed(list(V1_FILES.items()))))
    assert sync.tree_hash(one) == sync.tree_hash(two)

    drifted = write_tree(tmp_path / "c", {**V1_FILES, "runtime.py": "# v2\n"})
    assert sync.tree_hash(drifted) != sync.tree_hash(one)


def test_tree_hash_skips_pycache_and_pyc(tmp_path: Path):
    clean = write_tree(tmp_path / "clean", V1_FILES)
    dirty = write_tree(tmp_path / "dirty", V1_FILES)
    (dirty / "__pycache__").mkdir()
    (dirty / "__pycache__" / "runtime.cpython-314.pyc").write_bytes(b"\x00junk")
    (dirty / "web" / "__pycache__").mkdir(parents=True)
    (dirty / "web" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (dirty / "stray.pyc").write_bytes(b"\x00")
    assert sync.tree_hash(clean) == sync.tree_hash(dirty)


def test_tree_hash_on_the_real_repo_payload_is_stable():
    """The shipped bundle hashes cleanly with dev bytecode present (the
    repo's own ``__pycache__`` from running the suite)."""
    assert sync.tree_hash(REAL_PAYLOAD) == sync.tree_hash(REAL_PAYLOAD)


# -- install / skip / swap / downgrade ---------------------------------------


def test_install_on_missing(layout: dict):
    result = run(layout)
    assert result.status == "installed"
    assert result.payload_hash == sync.tree_hash(layout["bundled"])
    assert stamp_of(layout)["payloadHash"] == result.payload_hash
    # The whole bundled tree landed, content for content — payload.json is
    # the one file that legitimately differs (the stamp was added to it).
    installed = snapshot(layout["installed"])
    bundled = snapshot(layout["bundled"])
    assert {k: v for k, v in installed.items() if k != "payload.json"} == {
        k: v for k, v in bundled.items() if k != "payload.json"
    }
    assert json.loads((layout["installed"] / "payload.json").read_text()) == json.loads(
        (layout["bundled"] / "payload.json").read_text()
    ) | {"payloadHash": result.payload_hash}
    # The we-installed marker records the same hash.
    marker = json.loads((layout["state"] / "installed.json").read_text())
    assert marker["payloadHash"] == result.payload_hash


def test_current_is_a_no_write_skip(layout: dict):
    assert run(layout).status == "installed"
    before = (layout["installed"].stat().st_ino, snapshot(layout["installed"]))
    assert run(layout).status == "current"
    assert (layout["installed"].stat().st_ino, snapshot(layout["installed"])) == before
    assert scratch_names(layout) == []


def test_swap_on_drift_carries_meta_ships_config_fresh(layout: dict):
    seed_drift(layout)
    # The user edited config in Anki (writeConfig persists into meta.json)
    # and something corrupted the installed defaults file.
    write_tree(
        layout["installed"],
        {
            "meta.json": '{"name": "Ankiya", "config": {"contrast_clamp": false}}\n',
            "config.json": '{"contrast_clamp": "corrupted"}\n',
        },
    )
    (layout["installed"] / "__pycache__").mkdir()
    (layout["installed"] / "__pycache__" / "runtime.pyc").write_bytes(b"\x00")

    result = run(layout)
    assert result.status == "swapped"
    assert result.payload_hash == sync.tree_hash(layout["bundled"])
    assert stamp_of(layout)["payloadHash"] == result.payload_hash
    # meta.json is the one carried file — user config intact.
    meta = json.loads((layout["installed"] / "meta.json").read_text())
    assert meta["config"] == {"contrast_clamp": False}
    # config.json ships fresh from the bundle; bytecode never lands.
    assert (layout["installed"] / "config.json").read_text() == V2_FILES["config.json"]
    assert not (layout["installed"] / "__pycache__").exists()
    assert (layout["installed"] / "cssgen.py").read_text() == "# new in v2\n"
    # Immediate cleanup (the default) leaves no dot-siblings behind.
    assert scratch_names(layout) == []
    # Idempotent: the follow-up pass skips.
    assert run(layout).status == "current"


def test_disabled_state_survives_a_swap(layout: dict):
    """`disabled` lives in meta.json (aqt's own state): an update must never
    re-enable an add-on the user turned off."""
    seed_drift(layout)
    write_tree(layout["installed"], {"meta.json": '{"disabled": true}\n'})
    assert run(layout).status == "swapped"
    assert json.loads((layout["installed"] / "meta.json").read_text()) == {"disabled": True}


def test_downgrade_converges_backward(layout: dict):
    seed_drift(layout)
    assert run(layout).status == "swapped"  # installed is now v2 (stamp h2)
    # The plugin got downgraded: the bundle went back to v1 (fewer files).
    (layout["bundled"] / "cssgen.py").unlink()
    write_tree(layout["bundled"], V1_FILES)
    result = run(layout)
    assert result.status == "swapped"
    assert stamp_of(layout)["payloadHash"] == sync.tree_hash(layout["bundled"])
    assert not (layout["installed"] / "cssgen.py").exists()
    assert (layout["installed"] / "__init__.py").read_text() == "# v1\n"


def test_deferred_cleanup_hands_the_old_tree_to_the_caller(layout: dict):
    seed_drift(layout)
    result = run(layout, defer_old_cleanup=True)
    assert result.status == "swapped" and result.old_dir is not None
    assert result.old_dir.is_dir() and result.old_dir.name.startswith(OLD_PREFIX)
    # While this process lives, the sweep treats the old tree as the
    # caller's (the bootloader removes it at exit). Once the owner is gone,
    # the next pass sweeps it — crash-before-atexit recovery.
    assert run(layout).status == "current"
    assert result.old_dir.is_dir()
    result.old_dir.rename(result.old_dir.with_name(DEAD_OLD))
    assert run(layout).status == "current"
    assert scratch_names(layout) == []


def test_bundle_is_never_written(layout: dict):
    run(layout)
    seed_drift(layout)
    before = snapshot(layout["bundled"])
    assert run(layout).status == "swapped"
    assert snapshot(layout["bundled"]) == before


# -- refusal legs ------------------------------------------------------------


@pytest.mark.parametrize(
    "installed_files",
    [
        {k: v for k, v in V1_FILES.items() if k != "payload.json"},  # no stamp file
        {**V1_FILES, "payload.json": json.dumps(IDENTITY) + "\n"},  # no payloadHash
        {**V1_FILES, "payload.json": json.dumps({**IDENTITY, "schema": 99}) + "\n"},
        {**V1_FILES, "payload.json": json.dumps({**IDENTITY, "product": "other"}) + "\n"},
    ],
    ids=["missing-file", "missing-hash", "unknown-schema", "foreign-product"],
)
def test_refuses_an_unstamped_installed_folder(layout: dict, installed_files):
    write_tree(layout["installed"], installed_files)
    before = snapshot(layout["installed"])
    result = run(layout)
    assert result.status == "refused"
    assert snapshot(layout["installed"]) == before
    assert scratch_names(layout) == []
    assert not (layout["state"] / "installed.json").exists()


def test_refuses_an_invalid_bundle(layout: dict):
    assert run(layout).status == "installed"
    before = snapshot(layout["installed"])
    for broken in (
        {k: v for k, v in V1_FILES.items() if k != "payload.json"},
        {**V1_FILES, "payload.json": '{"product": "other", "schema": 1}\n'},
    ):
        shutil.rmtree(layout["bundled"])
        write_tree(layout["bundled"], broken)
        assert run(layout).status == "refused"
    assert snapshot(layout["installed"]) == before


def test_standalone_writes_nothing(layout: dict):
    assert run(layout).status == "installed"
    before = snapshot(layout["installed"])
    shutil.rmtree(layout["bundled"])
    layout["bundled"] = layout["scratch"].parent / "gone"  # no bundle at all
    result = run(layout)
    assert result.status == "standalone"
    assert snapshot(layout["installed"]) == before
    assert scratch_names(layout) == []


def test_dev_symlink_is_refused_not_clobbered(layout: dict, tmp_path: Path):
    """The dev-install loop links the repo payload in as `ankiya`; sync must
    refuse (no stamp through the link) without ever writing the working
    tree."""
    link_target = write_tree(tmp_path / "worktree", V1_FILES)
    (layout["addons"] / "ankiya").symlink_to(link_target)
    before = snapshot(link_target)
    result = run(layout)
    assert result.status == "refused"
    assert snapshot(link_target) == before


# -- crash recovery ----------------------------------------------------------


def test_recovery_restores_dot_old_before_sweeping(layout: dict):
    """The order ticket 12 locked: an interrupted swap is recovered first,
    then swept — never the reverse, or the user's meta.json dies with the
    dot-old and a fresh-defaults tree takes its place."""
    assert run(layout).status == "installed"
    user_meta = '{"name": "Ankiya", "config": {"contrast_clamp": false}}\n'
    write_tree(layout["installed"], {"meta.json": user_meta})
    # Crash right between the two renames: installed moved to a dot-old,
    # the complete stage is still staged, and the bundle drifted further.
    seed_drift(layout)
    stage = layout["scratch"] / DEAD_STAGE
    sync._build_stage(layout["bundled"], stage, sync.tree_hash(layout["bundled"]))
    layout["installed"].rename(layout["scratch"] / DEAD_OLD)

    result = run(layout)
    # The dot-old was restored (user meta intact), the stage was swept, and
    # the normal stamp compare then converged onto the drifted bundle —
    # carrying that meta through the fresh swap.
    assert result.status == "swapped"
    meta = json.loads((layout["installed"] / "meta.json").read_text())
    assert meta["config"] == {"contrast_clamp": False}
    assert scratch_names(layout) == []


def test_recovery_prefers_dot_old_over_a_complete_stage(layout: dict):
    assert run(layout).status == "installed"
    bundled_hash = sync.tree_hash(layout["bundled"])
    stage = layout["scratch"] / DEAD_STAGE
    sync._build_stage(layout["bundled"], stage, bundled_hash)
    layout["installed"].rename(layout["scratch"] / DEAD_OLD)

    assert run(layout).status == "current"  # restored old already matches
    assert stamp_of(layout)["payloadHash"] == bundled_hash
    assert scratch_names(layout) == []


def test_recovery_lands_a_complete_stage(layout: dict):
    bundled_hash = sync.tree_hash(layout["bundled"])
    stage = layout["scratch"] / DEAD_STAGE
    sync._build_stage(layout["bundled"], stage, bundled_hash)
    result = run(layout)
    assert result.status == "current"  # the landed tree already matches
    assert stamp_of(layout)["payloadHash"] == bundled_hash
    assert scratch_names(layout) == []


def test_incomplete_stage_is_swept_not_landed(layout: dict):
    stage = layout["scratch"] / DEAD_STAGE
    write_tree(stage, V1_FILES)  # no stamped payload.json: copy never finished
    assert run(layout).status == "installed"
    assert scratch_names(layout) == []
    assert (layout["installed"] / "__init__.py").read_text() == "# v1\n"


def test_meta_json_salvage_from_a_leftover_old(layout: dict):
    assert run(layout).status == "installed"
    # The swap died after landing but before the carry: the landed tree has
    # no meta.json, the dot-old still holds the user's one.
    (layout["installed"] / "meta.json").unlink()
    user_meta = '{"config": {"contrast_clamp": false}}\n'
    write_tree(layout["scratch"] / DEAD_OLD, {"meta.json": user_meta})
    assert run(layout).status == "current"  # nothing else to do…
    assert (layout["installed"] / "meta.json").read_text() == user_meta
    assert scratch_names(layout) == []


def test_live_owner_scratch_is_never_touched(layout: dict):
    """A concurrent sync's in-flight stage/old is not crash debris: recovery
    ignores it and the sweep leaves it for its owner."""
    assert run(layout).status == "installed"
    live_stage = layout["scratch"] / f"{STAGE_PREFIX}{os.getpid()}-0-live"
    live_old = layout["scratch"] / f"{OLD_PREFIX}{os.getpid()}-0-live"
    write_tree(live_stage, V1_FILES)
    write_tree(live_old, {"meta.json": "{}\n"})

    # Installed missing while only live-owned scratch exists: recovery must
    # hold off (the owner is mid-swap), so this pass installs fresh…
    shutil.rmtree(layout["installed"])
    assert run(layout).status == "installed"
    # …and both live dirs survive untouched for their owner.
    assert live_stage.is_dir() and live_old.is_dir()
    assert set(scratch_names(layout)) == {live_stage.name, live_old.name}


def test_live_owner_pid_parsing():
    assert sync._live_owner(Path(f"{STAGE_PREFIX}{os.getpid()}-0-0"))  # us: alive
    assert not sync._live_owner(Path(DEAD_STAGE))  # unparseable pid: dead
    proc = subprocess.Popen([sys.executable, "-c", ""])  # exits immediately
    proc.wait()
    dead = Path(f"{STAGE_PREFIX}{proc.pid}-0-0")
    assert not sync._live_owner(dead)  # a real, now-dead pid


# -- re-entrancy and concurrency ---------------------------------------------


def test_enoent_mid_swap_reruns_and_converges(layout: dict, monkeypatch):
    """A FileNotFoundError mid-swap (a race got past the pid guard) must
    re-derive the decision, not error out."""
    calls = {"n": 0}
    real_swap = sync._swap

    def racing_swap(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError(errno.ENOENT, "stage vanished mid-swap")
        return real_swap(*args, **kwargs)

    monkeypatch.setattr(sync, "_swap", racing_swap)
    seed_drift(layout)
    result = run(layout)
    assert calls["n"] == 2
    assert result.status == "swapped"
    assert stamp_of(layout)["payloadHash"] == sync.tree_hash(layout["bundled"])


def test_scratch_names_unique_under_simulated_concurrency():
    names = {sync._scratch_dir(Path("/x"), STAGE_PREFIX).name for _ in range(500)}
    assert len(names) == 500
    assert all(n.startswith(STAGE_PREFIX) for n in names)


def test_concurrent_syncs_converge(layout: dict):
    """Threads racing the routine (service mount vs. Anki start, simulated
    in-process): no crashes, a valid stamped tree, and a final pass leaves
    no dot-siblings."""
    errors: list[BaseException] = []

    def worker(results: list):
        try:
            results.append(run(layout))
        except BaseException as exc:
            errors.append(exc)

    def race(count: int = 8) -> list[sync.SyncResult]:
        results: list[sync.SyncResult] = []
        threads = [threading.Thread(target=worker, args=(results,)) for _ in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        return results

    # Fresh install under contention.
    results = race()
    assert all(r.status in ("installed", "current", "swapped") for r in results)
    assert any(r.status == "installed" for r in results)
    assert run(layout).status == "current"
    assert stamp_of(layout)["payloadHash"] == sync.tree_hash(layout["bundled"])
    assert scratch_names(layout) == []

    # Drift under contention: every thread sees the stale stamp, all writes
    # converge, still clean.
    seed_drift(layout)
    race()
    assert run(layout).status == "current"
    assert stamp_of(layout)["payloadHash"] == sync.tree_hash(layout["bundled"])
    assert scratch_names(layout) == []


# -- the two drivers ---------------------------------------------------------


def test_bare_script_mode_needs_no_package_context(layout: dict):
    """The service mounts this file as a bare-script subprocess; it must run
    with a clean environment — no PYTHONPATH, no aqt, no package imports."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "payload/ankiya/sync.py"),
            str(layout["bundled"]),
            str(layout["installed"]),
            str(layout["state"]),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert json.loads(proc.stdout) == {
        "status": "installed",
        "detail": "",
        "payloadHash": sync.tree_hash(layout["bundled"]),
        "oldDir": None,
    }
    assert stamp_of(layout)["payloadHash"] == sync.tree_hash(layout["bundled"])


def test_bootloader_resolves_the_bundled_payload(monkeypatch):
    import ankiya

    monkeypatch.setenv(ankiya.BUNDLED_PAYLOAD_ENV, "/tmp/somewhere")
    assert str(ankiya.bundled_payload_dir()) == "/tmp/somewhere"
    monkeypatch.delenv(ankiya.BUNDLED_PAYLOAD_ENV, raising=False)
    assert ankiya.PLUGIN_ID in str(ankiya.DEFAULT_BUNDLED_DIR)
    assert "omarchy/plugins" in str(ankiya.bundled_payload_dir())


def test_real_payload_installs_and_goes_current(tmp_path: Path):
    bundled = tmp_path / "bundled"
    shutil.copytree(REAL_PAYLOAD, bundled)  # incl. the repo's dev __pycache__
    addons = tmp_path / "Anki2" / "addons21"
    addons.mkdir(parents=True)
    result = sync.ensure_current(bundled, addons / "ankiya", tmp_path / "state")
    assert result.status == "installed"
    assert result.payload_hash == sync.tree_hash(REAL_PAYLOAD)
    # Dev bytecode from the repo tree never lands in an install.
    assert not (addons / "ankiya" / "__pycache__").exists()
    assert sync.ensure_current(bundled, addons / "ankiya", tmp_path / "state").status == "current"
