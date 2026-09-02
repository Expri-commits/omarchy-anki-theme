"""Sync: keep the installed add-on converged on the plugin's bundled payload.

Ticket 12's protocol, built by ticket 19. The bundled payload tree inside
the Omarchy plugin is the single source of truth; ``addons21/anki_theme/`` is a
stamped cache of it, never an independent copy. One stdlib-only routine
serves both fresh-code drivers — the plugin service (as a
``/usr/bin/python sync.py`` subprocess against the bundled tree) and the
add-on's bootloader (``__init__.py`` imports it before anything else) — so
this module must run both as ``anki_theme.sync`` inside Anki and as a bare
script: no relative imports, no aqt, no caller assumptions.

Stamp   sha256 over the bundled tree's sorted relative paths + file bytes
        (``__pycache__``/``web`` dirs and ``*.pyc`` never hashed), recorded as
        ``payloadHash`` in the *installed* copy's ``payload.json`` — the
        bundled identity file never carries it, so the hash covers the whole
        bundle with no self-reference. Direction-agnostic: a downgrade
        converges backward, because the plugin is the truth. The stamp is a
        claim, not proof: the current leg re-hashes the installed tree modulo
        the files that legitimately differ, so a same-uid writer's edit is
        repaired by the next start (H6) instead of persisting until the next
        payload change.

Swap    stage the fresh tree to a unique dot-prefixed sibling of
        ``addons21`` (under the Anki2 root, outside the one directory Anki
        scans), carry over exactly ``meta.json`` — user config lives inside
        it (Anki's ``writeConfig`` persists edits there), and Anki-owned
        state like ``disabled`` must survive an update — then land by
        double rename: the installed folder is never half-written and
        always lands at the same path (a path change would strand the
        running package's ``__path__``).

Every intermediate crash state converges on the next run. The interrupted
swap is recovered *before* the dot-sibling sweep — sweeping first could
destroy a dot-old holding the user's meta.json and quietly replace their
config with defaults. The sweep never touches a scratch dir whose creating
pid is still alive (that is another sync's in-flight stage, not crash
debris), and re-entrancy is by re-derivation: a rename race that gets past
the pid guard (ENOENT, ENOTEMPTY) re-runs the whole decision from the new
state instead of erroring.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = 1
PRODUCT = "anki_theme"

# Dot-prefixed siblings of addons21 (under the Anki2 root, outside the one
# directory Anki scans); <pid>-<time_ns>-<rand> keeps a service-mount sync
# and an Anki-startup sync from ever colliding on a name.
STAGE_PREFIX = ".anki_theme-stage-"
OLD_PREFIX = ".anki_theme-old-"

# Never hashed: dev-loop bytecode noise must not flap a comparison, and
# ``web`` exists only in the installed tree — the runtime generates
# ``web/anki_theme.css`` there (runtime.WEB_DIR), the bundle never carries
# one, so skipping it leaves every stamp value unchanged and keeps the
# generated CSS from reading as drift in the current leg's re-hash.
HASH_SKIP_DIRS = frozenset({"__pycache__", "web"})
HASH_SKIP_SUFFIXES = (".pyc",)

# The current leg's divergence re-hash (H6) skips these relative paths on
# both sides — ``_landable``'s "the one file that legitimately differs" idiom,
# grown by one: only the installed ``payload.json`` carries the stamped
# payloadHash, and only the installed ``meta.json`` carries the user's config
# edits plus Anki-owned state (``disabled``). ``config.json`` is deliberately
# compared: Anki's add-on docs give it one writer (the install itself) — it is
# the defaults file, and ``writeConfig`` persists user edits into meta.json —
# so a diverging copy is real drift, not a legitimate variant.
CURRENT_SKIP_FILES = frozenset({"payload.json", "meta.json"})

# The we-installed marker in the plugin state dir; the service's
# reinstall-after-delete toast keys on its presence (ticket 12).
INSTALLED_MARKER = "installed.json"

# Statuses: the two write outcomes, the no-op, and the two never-touch legs.
INSTALLED = "installed"
SWAPPED = "swapped"
CURRENT = "current"
REFUSED = "refused"
STANDALONE = "standalone"


def _log(message: str) -> None:
    # Same sink as the runtime's log: stdout is the journal (service
    # subprocess) or the Anki console (bootloader).
    print(f"[anki_theme] sync: {message}", flush=True)


@dataclass(frozen=True)
class SyncResult:
    """What one ``ensure_current`` pass concluded — and, for a deferred
    swap, the superseded tree the caller is responsible for removing."""

    status: str
    detail: str = ""
    payload_hash: str = ""
    old_dir: Path | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "payloadHash": self.payload_hash,
            "oldDir": str(self.old_dir) if self.old_dir else None,
        }


# -- the stamp ---------------------------------------------------------------


def _hashable(rel: Path) -> bool:
    if HASH_SKIP_DIRS.intersection(rel.parts):
        return False
    return rel.suffix not in HASH_SKIP_SUFFIXES


def tree_hash(root: Path, skip: frozenset[str] = frozenset()) -> str:
    """Content hash of a payload tree: sorted relative paths + file bytes.

    Skips the never-shipped (``__pycache__``, ``*.pyc``) and the
    runtime-generated (``web/``, installed tree only) so dev-loop bytecode
    noise and the generated CSS cannot flap a comparison, plus the caller's
    relative POSIX paths in ``skip`` — recovery's stage verification excludes
    the stamped identity file, the one file a stage legitimately rewrites,
    and the current leg's divergence check (H6) excludes it and meta.json,
    the two files an install legitimately rewrites. Computed on the *bundled*
    tree, on candidate stages mid-recovery, and — modulo those two files — on
    the installed tree in the current leg, whose stamp alone no longer
    suffices: a same-uid writer's edit would otherwise persist until the next
    payload change.
    """
    digest = hashlib.sha256()
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and _hashable(p.relative_to(root))
        and p.relative_to(root).as_posix() not in skip
    )
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def read_stamp(payload_dir: Path) -> dict | None:
    """The tree's ``payload.json``, or None when absent/unreadable/not JSON."""
    try:
        stamp = json.loads((payload_dir / "payload.json").read_text())
    # The trailing comma pins the <=3.13-compatible parens: ruff's py314
    # formatter would otherwise emit PEP 758's bare, 3.14-only form.
    except (
        OSError,
        ValueError,
    ):
        return None
    return stamp if isinstance(stamp, dict) else None


# -- staging -----------------------------------------------------------------


def _scratch_dir(scratch_root: Path, prefix: str) -> Path:
    return scratch_root / f"{prefix}{os.getpid()}-{time.time_ns()}-{secrets.token_hex(4)}"


def _scan_scratch(scratch_root: Path) -> tuple[list[Path], list[Path]]:
    """The dot-old and dot-stage siblings left behind by earlier runs."""
    olds: list[Path] = []
    stages: list[Path] = []
    if not scratch_root.is_dir():
        return olds, stages
    for entry in scratch_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith(OLD_PREFIX):
            olds.append(entry)
        elif entry.name.startswith(STAGE_PREFIX):
            stages.append(entry)
    return olds, stages


def _newest(paths: list[Path]) -> Path | None:
    return max(paths, key=lambda p: p.stat().st_mtime_ns) if paths else None


def _live_owner(entry: Path) -> bool:
    """Whether a scratch dir's name names a process that is still running.

    Scratch names carry their creator's pid; a live owner means this is an
    in-flight stage or old of a concurrent sync, not crash debris — sweeping
    it is exactly the race the pid guard exists to prevent. Unparseable pids
    (hand-crafted or legacy names) count as dead.
    """
    body = entry.name
    for prefix in (STAGE_PREFIX, OLD_PREFIX):
        if body.startswith(prefix):
            body = body[len(prefix) :]
            break
    try:
        pid = int(body.split("-", 1)[0])
    except ValueError:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # e.g. EPERM: the process exists, it just is not ours
    return True


def _complete(stage: Path) -> dict | None:
    """The tree's stamp when it reads as ours — schema, product, and a string
    payloadHash, the same gate the installed-tree check applies — else None.
    For a stage this attests the copy finished (the stamp file is written
    last, see ``_build_stage``); for a dot-old it attests the tree is a
    former installed dir rather than planted debris. Attests, not proves: a
    well-formed stamp is writable by the same-uid planter, which is why
    stages must additionally match the bundle's bytes (``_landable``)."""
    stamp = read_stamp(stage)
    if (
        stamp is not None
        and stamp.get("schema") == SCHEMA
        and stamp.get("product") == PRODUCT
        and isinstance(stamp.get("payloadHash"), str)
    ):
        return stamp
    return None


def _landable(stage: Path, bundled_dir: Path) -> bool:
    """Whether recovery may land this staged tree: stamped as ours
    (``_complete``) and byte-equivalent to the bundle apart from the stamped
    identity file, with the stamp claiming the bundle's exact full hash —
    the exact shape an install-leg stage is built in. Deliberately narrower
    than every stage ``_build_stage`` can produce: a swap stage carrying
    user-edited meta.json never matches, sweeps, and recovery rides the
    swap's complete dot-old sibling instead, else the next sync reinstalls.
    Standalone — or a bundle missing its own identity — is the degenerate
    case: there is nothing whose bytes can vouch for the stage, and an
    absent bundle hashes like an empty tree, which would otherwise
    rubber-stamp a planted identity-only stage claiming the empty digest.
    So nothing is landable there. Unreadable candidates count as not
    landable, never as errors: recovery sweeps and converges instead of
    raising past the bootloader's fail-open contract."""
    stamp = _complete(stage)
    if stamp is None:
        return False
    try:
        if read_stamp(bundled_dir) is None:
            return False
        skip = frozenset({"payload.json"})
        if stamp["payloadHash"] != tree_hash(bundled_dir):
            return False
        return tree_hash(stage, skip) == tree_hash(bundled_dir, skip)
    except OSError:
        return False


def _build_stage(bundled_dir: Path, stage: Path, payload_hash: str) -> None:
    shutil.copytree(bundled_dir, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    identity = json.loads((bundled_dir / "payload.json").read_text())
    identity["payloadHash"] = payload_hash
    # Written last: a stage is only ever landed once this file exists.
    (stage / "payload.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")


def _write_marker(state_dir: Path, payload_hash: str) -> None:
    """Record the we-installed marker. Best-effort: a state-dir problem must
    never fail the sync itself."""
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            state_dir / INSTALLED_MARKER,
            json.dumps(
                {
                    "product": PRODUCT,
                    "payloadHash": payload_hash,
                    "installedAt": datetime.now(UTC).isoformat(timespec="seconds"),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    except OSError:
        _log(f"could not write {state_dir / INSTALLED_MARKER}")


# -- atomic writes ------------------------------------------------------------


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` in one atomic, no-follow write.

    mkstemp hands back a secrets-random name in the destination directory,
    so no same-uid attacker can pre-plant the tmp path as a symlink the way
    the predictable ``.<name>.<pid>.tmp`` names this replaces allowed
    (``write_text`` would have truncated straight through their link), and
    the closing ``os.replace`` swaps the finished file over whatever sits at
    ``path`` — replacing a planted link wholesale, never writing through it.
    The temp is unlinked on any failure, so a failed write leaves no litter.
    The parent directory must exist. mkstemp's 0600 mode is the intended
    semantics here: everything written through this helper (the web CSS and
    the state-dir markers) is user-owned with single-user consumers.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# -- the one routine ---------------------------------------------------------


def ensure_current(
    bundled_dir: Path,
    installed_dir: Path,
    state_dir: Path,
    *,
    defer_old_cleanup: bool = False,
) -> SyncResult:
    """Converge the installed add-on on the bundled payload.

    One total decision over the filesystem state — install (missing folder),
    swap (stamp drifted), current (skip), refused (folder not verifiably
    ours — never clobber), or standalone (no bundle: theming continues, no
    updates). Safe at any interruption point; the swap's dot-old is removed
    immediately unless ``defer_old_cleanup`` (the bootloader keeps it for
    this session's tracebacks and removes it at exit).
    """
    try:
        return _ensure_current(bundled_dir, installed_dir, state_dir, defer_old_cleanup)
    except OSError as exc:
        # A rename race with another sync (service mount vs. Anki startup):
        # ENOENT — our source was consumed mid-flight; ENOTEMPTY — another
        # tree landed on the target first (POSIX rename never overwrites a
        # non-empty directory). Re-derive the whole decision from the new
        # state, then give up gracefully — the next run converges.
        if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY):
            raise
        try:
            return _ensure_current(bundled_dir, installed_dir, state_dir, defer_old_cleanup)
        except OSError as again:
            if again.errno not in (errno.ENOENT, errno.ENOTEMPTY):
                raise
            _log("raced twice mid-swap — leaving convergence to the next run")
            raise


def _ensure_current(
    bundled_dir: Path, installed_dir: Path, state_dir: Path, defer_old_cleanup: bool
) -> SyncResult:
    scratch_root = installed_dir.parent.parent
    old_dirs, stage_dirs = _scan_scratch(scratch_root)
    # Crash debris only: scratch dirs with a live creator are another sync's
    # in-flight work — recovery must not land it, the sweep must not eat it.
    old_dirs = [d for d in old_dirs if not _live_owner(d)]
    stage_dirs = [d for d in stage_dirs if not _live_owner(d)]

    # Recover an interrupted swap BEFORE sweeping anything: the installed
    # folder missing while a dot-old or complete stage exists means the
    # previous run died between the two renames. The dot-old wins — it is
    # the user's whole previous tree, meta.json included; landing the stage
    # is only the fallback. Both legs verify before renaming: a dot-old
    # without our stamp, or a stage not proven byte-equivalent to the
    # bundle, is plantable debris that would otherwise be executed by Anki
    # at next start — those stay for the sweep, and the next sync reinstalls.
    if not installed_dir.exists():
        restored = _newest([d for d in old_dirs if _complete(d)])
        if restored is not None:
            restored.rename(installed_dir)
            old_dirs.remove(restored)
            _log(f"crash recovery: restored {restored.name} into place")
        else:
            # Only the stage fallback consults the bundle, and no other leg's
            # cost or failure order may move; a missing or unreadable bundle
            # reads as not landable inside the check — the stage sweeps and
            # the install leg reinstalls, instead of recovery crashing.
            landed = _newest([s for s in stage_dirs if _landable(s, bundled_dir)])
            if landed is not None:
                landed.rename(installed_dir)
                stage_dirs.remove(landed)
                _log(f"crash recovery: landed staged tree {landed.name}")

    # meta.json salvage: a landed stage should already carry it (the copy
    # happens before the renames), but if the swap died before the carry,
    # a dot-old still holds the user's config — restore it before sweeping.
    if installed_dir.is_dir() and not (installed_dir / "meta.json").exists():
        # exists() follows links, so this branch is exactly where a planted
        # dangling symlink sits: unlink it, or copy2 would create the file
        # at the link's target instead of here. A same-uid race can swap the
        # link for a directory between the check and the unlink — any unlink
        # failure abandons the salvage entirely rather than copying through
        # a path we failed to clear.
        meta = installed_dir / "meta.json"
        try:
            if meta.is_symlink():
                meta.unlink()
        except OSError:
            _log(f"could not clear {meta} for salvage — skipping")
        else:
            for old in old_dirs:
                # is_file() follows links too: a planted source link must not
                # duplicate (or disk-fill with) its target's content.
                source = old / "meta.json"
                if source.is_file() and not source.is_symlink():
                    shutil.copy2(source, meta)
                    _log(f"salvaged meta.json from {old.name}")
                    break

    for leftover in old_dirs + stage_dirs:
        shutil.rmtree(leftover, ignore_errors=True)
        _log(f"swept {leftover.name}")

    if not bundled_dir.is_dir():
        _log(f"no bundled payload at {bundled_dir} — standalone, no updates")
        return SyncResult(STANDALONE, f"no bundled payload at {bundled_dir}")

    identity = read_stamp(bundled_dir)
    if identity is None or identity.get("schema") != SCHEMA or identity.get("product") != PRODUCT:
        detail = (
            f"bundled payload.json at {bundled_dir} is not a schema-{SCHEMA} {PRODUCT} identity"
        )
        _log(f"refused: {detail}")
        return SyncResult(REFUSED, detail)

    payload_hash = tree_hash(bundled_dir)

    if not installed_dir.exists():
        return _install(bundled_dir, installed_dir, state_dir, payload_hash)

    stamp = read_stamp(installed_dir)
    if stamp is None:
        detail = f"no readable payload.json in {installed_dir} — not a synced install"
    elif stamp.get("schema") != SCHEMA:
        detail = f"unknown stamp schema {stamp.get('schema')!r}"
    elif stamp.get("product") != PRODUCT:
        detail = "foreign payload.json"
    elif not isinstance(stamp.get("payloadHash"), str):
        detail = f"no payloadHash in {installed_dir / 'payload.json'} — not a synced install"
    else:
        if stamp["payloadHash"] == payload_hash:
            # The stamp is a claim recorded in a writable file, so the tree it
            # describes is re-verified here (H6): a same-uid writer's edit or
            # bit-rot must not ride as CURRENT until the next payload change.
            # The comparison skips the two legitimate variants (see
            # CURRENT_SKIP_FILES) and everything HASH_SKIP_DIRS drops; a
            # divergence takes the same _swap leg as a stamp drift, which
            # converges — one swap rebuilds from the bundle, the next pass is
            # CURRENT — so a tampered tree can never loop the boot.
            if tree_hash(installed_dir, CURRENT_SKIP_FILES) == tree_hash(
                bundled_dir, CURRENT_SKIP_FILES
            ):
                _log(f"payload {payload_hash[:12]}… current")
                return SyncResult(CURRENT, payload_hash=payload_hash)
            _log("installed tree diverges from bundle — swapping")
        return _swap(bundled_dir, installed_dir, state_dir, payload_hash, defer_old_cleanup)
    _log(f"refused: {detail}; leaving the folder alone")
    return SyncResult(REFUSED, detail)


def _install(
    bundled_dir: Path, installed_dir: Path, state_dir: Path, payload_hash: str
) -> SyncResult:
    # The service's post-consent leg. The bootloader can never get here: if
    # it is executing, the installed folder exists by definition — but the
    # same routine serves both drivers, so the leg lives here.
    stage = _stage_and_carry(bundled_dir, installed_dir, payload_hash)
    installed_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        stage.rename(installed_dir)
    except OSError:
        # A racer landed first; our stage is surplus — clean it up so a lost
        # race never litters, then let the caller's re-derive converge.
        shutil.rmtree(stage, ignore_errors=True)
        raise
    _write_marker(state_dir, payload_hash)
    _log(f"installed payload {payload_hash[:12]}… at {installed_dir}")
    return SyncResult(INSTALLED, payload_hash=payload_hash)


def _stage_and_carry(bundled_dir: Path, installed_dir: Path, payload_hash: str) -> Path:
    """Build a complete staged tree — copied bundle, stamped identity, and
    the one carried file (meta.json: the user's config edits and Anki-owned
    state like ``disabled`` must survive an update). A half-built stage is
    never left behind, whatever interrupted the build."""
    scratch_root = installed_dir.parent.parent
    stage = _scratch_dir(scratch_root, STAGE_PREFIX)
    try:
        _build_stage(bundled_dir, stage, payload_hash)
        meta = installed_dir / "meta.json"
        # is_file() follows links: a planted meta.json symlink must not have
        # its target's content duplicated into the fresh stage. The stage
        # side needs no guard — it is ours, built moments ago.
        if meta.is_file() and not meta.is_symlink():
            shutil.copy2(meta, stage / "meta.json")
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return stage


def _swap(
    bundled_dir: Path,
    installed_dir: Path,
    state_dir: Path,
    payload_hash: str,
    defer_old_cleanup: bool,
) -> SyncResult:
    scratch_root = installed_dir.parent.parent
    stage = _stage_and_carry(bundled_dir, installed_dir, payload_hash)
    old = _scratch_dir(scratch_root, OLD_PREFIX)
    try:
        installed_dir.rename(old)
        # Same path, new tree — the package __path__ stays valid.
        stage.rename(installed_dir)
    except OSError:
        # Lost a race. Never leave the installed folder missing: roll our
        # dot-old back if the racer did not land one, drop it if it did, and
        # always take our surplus stage with us — then re-derive.
        shutil.rmtree(stage, ignore_errors=True)
        if old.exists():
            if installed_dir.exists():
                shutil.rmtree(old, ignore_errors=True)
            else:
                old.rename(installed_dir)
        raise
    _write_marker(state_dir, payload_hash)
    if not defer_old_cleanup:
        shutil.rmtree(old, ignore_errors=True)
        _log(f"swapped to payload {payload_hash[:12]}…")
        return SyncResult(SWAPPED, payload_hash=payload_hash)
    _log(f"swapped to payload {payload_hash[:12]}… (old tree {old.name}, removed at exit)")
    return SyncResult(SWAPPED, payload_hash=payload_hash, old_dir=old)


# -- bare-script entry (the service's subprocess mount) ----------------------


def main(argv: list[str]) -> int:
    """``sync.py <bundled_dir> <installed_dir> <state_dir>`` — exactly one
    JSON result line on stdout for the journal (logs go to stderr, so the
    caller can machine-read the outcome). Handled outcomes exit 0; only a
    crash exits nonzero."""
    if len(argv) != 3:
        print(
            f"usage: {Path(sys.argv[0]).name} <bundled_dir> <installed_dir> <state_dir>",
            file=sys.stderr,
        )
        return 2
    with contextlib.redirect_stdout(sys.stderr):
        result = ensure_current(Path(argv[0]), Path(argv[1]), Path(argv[2]))
    print(json.dumps(result.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
