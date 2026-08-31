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
        (``__pycache__`` dirs and ``*.pyc`` never hashed), recorded as
        ``payloadHash`` in the *installed* copy's ``payload.json`` — the
        bundled identity file never carries it, so the hash covers the whole
        bundle with no self-reference. Direction-agnostic: a downgrade
        converges backward, because the plugin is the truth.

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

HASH_SKIP_DIRS = frozenset({"__pycache__"})
HASH_SKIP_SUFFIXES = (".pyc",)

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


def tree_hash(root: Path) -> str:
    """Content hash of a payload tree: sorted relative paths + file bytes.

    Skips what must never ship (``__pycache__``, ``*.pyc``) so dev-loop
    bytecode noise cannot flap the stamp. Only ever computed on the
    *bundled* tree — the installed one is trusted via its stamp.
    """
    digest = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file() and _hashable(p.relative_to(root)))
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
    except OSError, ValueError:
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


def _complete(stage: Path) -> bool:
    """A stage whose stamped payload.json landed. The stamp file is written
    last (see ``_build_stage``), so its presence means the copy finished."""
    stamp = read_stamp(stage)
    return (
        stamp is not None
        and stamp.get("product") == PRODUCT
        and isinstance(stamp.get("payloadHash"), str)
    )


def _build_stage(bundled_dir: Path, stage: Path, payload_hash: str) -> None:
    shutil.copytree(bundled_dir, stage, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    identity = json.loads((bundled_dir / "payload.json").read_text())
    identity["payloadHash"] = payload_hash
    # Written last: a stage is only ever landed once this file exists.
    (stage / "payload.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")


def _write_marker(state_dir: Path, payload_hash: str) -> None:
    """Record the we-installed marker. Best-effort: a state-dir problem must
    never fail the sync itself. The tmp name carries our pid so concurrent
    syncs never fight over one tmp file."""
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        marker = state_dir / INSTALLED_MARKER
        tmp = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "product": PRODUCT,
                    "payloadHash": payload_hash,
                    "installedAt": datetime.now(UTC).isoformat(timespec="seconds"),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        tmp.replace(marker)
    except OSError:
        _log(f"could not write {state_dir / INSTALLED_MARKER}")


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
    # is only the fallback.
    if not installed_dir.exists():
        restored = _newest(old_dirs)
        if restored is not None:
            restored.rename(installed_dir)
            old_dirs.remove(restored)
            _log(f"crash recovery: restored {restored.name} into place")
        else:
            landed = _newest([s for s in stage_dirs if _complete(s)])
            if landed is not None:
                landed.rename(installed_dir)
                stage_dirs.remove(landed)
                _log(f"crash recovery: landed staged tree {landed.name}")

    # meta.json salvage: a landed stage should already carry it (the copy
    # happens before the renames), but if the swap died before the carry,
    # a dot-old still holds the user's config — restore it before sweeping.
    if installed_dir.is_dir() and not (installed_dir / "meta.json").exists():
        for old in old_dirs:
            if (old / "meta.json").is_file():
                shutil.copy2(old / "meta.json", installed_dir / "meta.json")
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
            _log(f"payload {payload_hash[:12]}… current")
            return SyncResult(CURRENT, payload_hash=payload_hash)
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
        if meta.is_file():
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
