"""Tier-1 suite for the sync routine and bootloader shape (ticket 19).

The protocol itself is ticket 12's: stamp = content hash of the bundled
tree, swap = dot-sibling stage + double rename with meta.json the one
carried file, recovery before sweep, refuse anything not verifiably ours.
These tests exercise the routine as pure functions over tmp paths — no Anki,
no GUI — plus the real repo payload tree once, so the shipped bundle is
proven stampable end to end.
"""

import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from anki_theme import sync
from anki_theme.sync import OLD_PREFIX, STAGE_PREFIX

REPO = Path(__file__).resolve().parent.parent
REAL_PAYLOAD = REPO / "payload" / "anki_theme"

IDENTITY = {
    "schema": 1,
    "product": "anki_theme",
    "version": "0.1.0",
}

# A minimal but honest bundle: identity + defaults + code. The routine is
# content-agnostic, so only the identity file's shape matters.
V1_FILES = {
    "payload.json": json.dumps(IDENTITY, indent=2) + "\n",
    "meta.json": '{"name": "Anki Theme for Omarchy", "homepage": "https://example.com"}\n',
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
        "installed": addons / "anki_theme",
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


def test_tree_hash_ignores_a_stray_web_dir(tmp_path: Path):
    """``web`` joining HASH_SKIP_DIRS is stamp-neutral (H6 groundwork): the
    bundle never ships one, so a tree hashes identically with and without the
    generated CSS the runtime leaves behind in a dev checkout — existing
    installs keep their CURRENT stamp across the change."""
    clean = write_tree(tmp_path / "clean", V1_FILES)
    with_web = write_tree(tmp_path / "withweb", V1_FILES)
    (with_web / "web").mkdir()
    (with_web / "web" / "anki_theme.css").write_text("body { background: black }\n")
    assert sync.tree_hash(clean) == sync.tree_hash(with_web)


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
            "meta.json": (
                '{"name": "Anki Theme for Omarchy", "config": {"contrast_clamp": false}}\n'
            ),
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


# -- the current leg re-verifies the installed tree (H6) -----------------------


def test_tampered_installed_tree_swaps_and_converges(layout: dict):
    """A matching stamp is a claim about the tree, not proof of it: a same-uid
    writer's edit (or bit-rot) must not ride as CURRENT until the next
    payload change. The divergence takes the same swap leg a stamp drift
    would — which converges (one swap, then CURRENT), so tampering can never
    loop the boot."""
    assert run(layout).status == "installed"
    write_tree(layout["installed"], {"__init__.py": "# tampered\n"})
    result = run(layout)
    assert result.status == "swapped"
    assert (layout["installed"] / "__init__.py").read_text() == "# v1\n"
    assert stamp_of(layout)["payloadHash"] == sync.tree_hash(layout["bundled"])
    assert run(layout).status == "current"


def test_diverging_installed_config_json_swaps_and_converges(layout: dict):
    """config.json is compared, not skipped: Anki's add-on docs give the file
    exactly one writer — the install itself (it is the defaults file;
    ``writeConfig`` persists user edits into meta.json) — so a diverging copy
    is real drift, never a legitimate variant."""
    assert run(layout).status == "installed"
    write_tree(layout["installed"], {"config.json": '{"contrast_clamp": "corrupted"}\n'})
    assert run(layout).status == "swapped"
    assert (layout["installed"] / "config.json").read_text() == V1_FILES["config.json"]
    assert run(layout).status == "current"


def test_user_edited_meta_json_stays_current(layout: dict):
    """The thrash trap: meta.json is the one carried file, so user edits and
    Anki-owned state (``disabled``) must read as a legitimate variant —
    comparing its content would swap on every boot."""
    assert run(layout).status == "installed"
    write_tree(
        layout["installed"],
        {"meta.json": '{"config": {"contrast_clamp": false}, "disabled": true}\n'},
    )
    assert run(layout).status == "current"


def test_runtime_generated_web_and_pycache_stay_current(layout: dict):
    """The runtime generates ``web/anki_theme.css`` inside the installed tree
    only; with dev bytecode, neither may read as divergence."""
    assert run(layout).status == "installed"
    (layout["installed"] / "web").mkdir()
    (layout["installed"] / "web" / "anki_theme.css").write_text("body {}\n")
    (layout["installed"] / "__pycache__").mkdir()
    (layout["installed"] / "__pycache__" / "runtime.pyc").write_bytes(b"\x00")
    assert run(layout).status == "current"


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
    """The dev-install loop links the repo payload in as `anki_theme`; sync must
    refuse (no stamp through the link) without ever writing the working
    tree."""
    link_target = write_tree(tmp_path / "worktree", V1_FILES)
    (layout["addons"] / "anki_theme").symlink_to(link_target)
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
    user_meta = '{"name": "Anki Theme for Omarchy", "config": {"contrast_clamp": false}}\n'
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


# -- recovery verifies what it recovers ---------------------------------------


@pytest.mark.parametrize(
    "stamp",
    [
        None,  # no payload.json at all
        {"product": "other", "payloadHash": "0" * 64},  # foreign product
        {"product": "anki_theme", "payloadHash": None},  # non-string hash
        {"schema": 99, "product": "anki_theme", "payloadHash": "0" * 64},  # foreign schema
    ],
    ids=["no-stamp", "foreign-product", "non-string-hash", "unknown-schema"],
)
def test_planted_unverified_dot_old_is_swept_not_restored(layout: dict, stamp):
    """A real dot-old is a former installed dir, which always carried a stamp;
    an unstampable 'dot-old' is plantable debris, and renaming it into place
    would hand its code to Anki at next start. Unverified plants stay for the
    sweep: no add-on until the next sync reinstalls, never stranger bytes."""
    planted = layout["scratch"] / DEAD_OLD
    files = {k: v for k, v in V1_FILES.items() if k != "payload.json"}
    files["__init__.py"] = "# planted\n"
    if stamp is not None:
        files["payload.json"] = json.dumps(stamp) + "\n"
    write_tree(planted, files)

    assert run(layout).status == "installed"  # fresh from the bundle, not the plant
    assert (layout["installed"] / "__init__.py").read_text() == "# v1\n"
    assert scratch_names(layout) == []


def test_planted_forged_stamp_stage_is_swept_not_landed(layout: dict):
    """A stage stamp is only a claim; landing requires the bytes to hash back
    to the bundled payload. This plant claims the current bundled hash but
    carries different bytes — exactly what the old product-name-only check
    landed."""
    forged = {**IDENTITY, "payloadHash": sync.tree_hash(layout["bundled"])}
    write_tree(
        layout["scratch"] / DEAD_STAGE,
        {
            **V1_FILES,
            "__init__.py": "# planted\n",
            "payload.json": json.dumps(forged, indent=2) + "\n",
        },
    )

    assert run(layout).status == "installed"
    assert (layout["installed"] / "__init__.py").read_text() == "# v1\n"
    assert scratch_names(layout) == []


def test_standalone_never_lands_a_stage(layout: dict):
    """No bundle, nothing landable: a stage's stamp is planter-controlled
    bytes, so there is nothing to verify it against — a fully-formed forged
    stage sweeps like any other plant, and the add-on stays absent until a
    reinstall (fail toward no add-on, never toward planted code)."""
    assert run(layout).status == "installed"
    shutil.rmtree(layout["bundled"])
    layout["bundled"] = layout["scratch"].parent / "gone"
    shutil.rmtree(layout["installed"])
    forged = {**IDENTITY, "payloadHash": "0" * 64}
    write_tree(
        layout["scratch"] / DEAD_STAGE,
        {
            **V1_FILES,
            "__init__.py": "# planted\n",
            "payload.json": json.dumps(forged, indent=2) + "\n",
        },
    )

    assert run(layout).status == "standalone"
    assert not layout["installed"].exists()
    assert scratch_names(layout) == []


def test_planted_empty_digest_stage_is_swept_standalone(layout: dict):
    """The sharpest standalone plant: a stage of nothing but a payload.json
    claiming the empty-tree digest. With the bundle absent, every hash
    comparison here collapses to sha256(b"") — only the bundle carrying its
    own identity stops this from landing (review W1, reproduced)."""
    assert run(layout).status == "installed"
    shutil.rmtree(layout["bundled"])
    layout["bundled"] = layout["scratch"].parent / "gone"
    shutil.rmtree(layout["installed"])
    empty = {**IDENTITY, "payloadHash": hashlib.sha256(b"").hexdigest()}
    stage = layout["scratch"] / DEAD_STAGE
    stage.mkdir()
    (stage / "payload.json").write_text(json.dumps(empty, indent=2) + "\n")

    assert run(layout).status == "standalone"
    assert not layout["installed"].exists()
    assert scratch_names(layout) == []


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


# -- meta.json is carried no-follow --------------------------------------------


def test_carry_skips_a_planted_meta_symlink(layout: dict, tmp_path: Path):
    """``is_file()`` follows links: a planted meta.json symlink in the
    installed tree would duplicate — or disk-fill with — its target's content
    into the freshly built stage. The carry gates on the link, so the swap
    ships the bundle's meta and the target is never read."""
    assert run(layout).status == "installed"
    secret = tmp_path / "outside-the-tree.txt"
    secret.write_text("do not copy\n")
    (layout["installed"] / "meta.json").unlink()
    (layout["installed"] / "meta.json").symlink_to(secret)
    seed_drift(layout)

    assert run(layout).status == "swapped"
    meta = json.loads((layout["installed"] / "meta.json").read_text())
    assert meta["name"] == "Anki Theme for Omarchy"  # the bundle's, not the link's
    assert secret.read_text() == "do not copy\n"


def test_salvage_skips_a_planted_symlink_in_the_dot_old(layout: dict, tmp_path: Path):
    assert run(layout).status == "installed"
    secret = tmp_path / "outside-the-tree.txt"
    secret.write_text("do not copy\n")
    (layout["installed"] / "meta.json").unlink()
    old = layout["scratch"] / DEAD_OLD
    old.mkdir()
    (old / "meta.json").symlink_to(secret)

    assert run(layout).status == "current"
    # The planted link is skipped, its target never read, and the sweep takes
    # the dot-old (link included) with it.
    assert not (layout["installed"] / "meta.json").exists()
    assert secret.read_text() == "do not copy\n"
    assert scratch_names(layout) == []


def test_salvage_replaces_a_dangling_meta_link_not_its_target(layout: dict):
    """A dangling meta.json link at the destination is the write-through hole:
    exists() follows the link and finds nothing, so salvage runs — and copy2
    must replace the link itself, never create the file at the attacker's
    target path."""
    assert run(layout).status == "installed"
    user_meta = '{"config": {"contrast_clamp": false}}\n'
    (layout["installed"] / "meta.json").unlink()
    planted = layout["scratch"] / "attacker-chosen.json"
    (layout["installed"] / "meta.json").symlink_to(planted)  # dangling
    write_tree(layout["scratch"] / DEAD_OLD, {"meta.json": user_meta})

    assert run(layout).status == "current"
    assert (layout["installed"] / "meta.json").read_text() == user_meta
    assert not (layout["installed"] / "meta.json").is_symlink()
    assert not planted.exists()
    assert scratch_names(layout) == []


def test_salvage_is_abandoned_when_the_link_cannot_be_cleared(layout: dict, monkeypatch):
    """A same-uid race can swap the planted link for a directory between
    is_symlink() and unlink() — the unlink then fails, and the salvage must
    be abandoned (logged), never copy through a path we failed to clear."""
    assert run(layout).status == "installed"
    (layout["installed"] / "meta.json").unlink()
    (layout["installed"] / "meta.json").symlink_to(layout["scratch"] / "dangling")
    write_tree(layout["scratch"] / DEAD_OLD, {"meta.json": '{"config": {}}\n'})

    real_unlink = Path.unlink

    def racing_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == "meta.json" and self.parent == layout["installed"]:
            raise OSError("swapped for a directory mid-race")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", racing_unlink)
    assert run(layout).status == "current"
    assert not (layout["installed"] / "meta.json").exists()
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


# -- atomic writes ------------------------------------------------------------


def test_atomic_write_text_lands_content_and_leaves_no_temp(tmp_path: Path):
    target = tmp_path / "marker.json"
    sync.atomic_write_text(target, '{"a": 1}\n')
    assert target.read_text() == '{"a": 1}\n'
    assert [p.name for p in tmp_path.iterdir()] == ["marker.json"]


def test_atomic_write_text_replaces_a_planted_symlink_not_its_target(tmp_path: Path):
    """The predictable tmp names this helper replaces were plantable: a
    same-uid attacker pre-creates the tmp path as a symlink and write_text's
    truncate writes straight through it. A mkstemp name cannot pre-exist,
    and the closing replace swaps the link at the destination out wholesale."""
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched\n")
    target = tmp_path / "marker.json"
    target.symlink_to(victim)
    sync.atomic_write_text(target, "fresh\n")
    assert target.read_text() == "fresh\n" and not target.is_symlink()
    assert victim.read_text() == "untouched\n"


def test_atomic_write_text_unlinks_its_temp_when_the_write_fails(tmp_path: Path, monkeypatch):
    target = tmp_path / "marker.json"
    target.write_text("old\n")

    def exploding_replace(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", exploding_replace)
    with pytest.raises(OSError, match="disk full"):
        sync.atomic_write_text(target, "fresh\n")
    assert target.read_text() == "old\n"
    # A failed write never litters the directory with its temp.
    assert [p.name for p in tmp_path.iterdir()] == ["marker.json"]


# -- the two drivers ---------------------------------------------------------


def test_bare_script_mode_needs_no_package_context(layout: dict):
    """The service mounts this file as a bare-script subprocess; it must run
    with a clean environment — no PYTHONPATH, no aqt, no package imports."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "payload/anki_theme/sync.py"),
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
    import anki_theme

    monkeypatch.setenv(anki_theme.BUNDLED_PAYLOAD_ENV, "/tmp/somewhere")
    assert str(anki_theme.bundled_payload_dir()) == "/tmp/somewhere"
    monkeypatch.delenv(anki_theme.BUNDLED_PAYLOAD_ENV, raising=False)
    assert anki_theme.PLUGIN_ID in str(anki_theme.DEFAULT_BUNDLED_DIR)
    assert "omarchy/plugins" in str(anki_theme.bundled_payload_dir())


def test_real_payload_installs_and_goes_current(tmp_path: Path):
    bundled = tmp_path / "bundled"
    shutil.copytree(REAL_PAYLOAD, bundled)  # incl. the repo's dev __pycache__
    addons = tmp_path / "Anki2" / "addons21"
    addons.mkdir(parents=True)
    result = sync.ensure_current(bundled, addons / "anki_theme", tmp_path / "state")
    assert result.status == "installed"
    assert result.payload_hash == sync.tree_hash(REAL_PAYLOAD)
    # Dev bytecode from the repo tree never lands in an install.
    assert not (addons / "anki_theme" / "__pycache__").exists()
    assert (
        sync.ensure_current(bundled, addons / "anki_theme", tmp_path / "state").status == "current"
    )
