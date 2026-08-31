"""Drift: has Anki's color-var inventory moved since the snapshot was cut?

Wayfinder tickets 15 → 21. The vendored ``var_snapshot.txt`` beside this
module is the name-set the mapping was built against; at startup the live
``aqt.colors`` name-set is diffed against it — the same artifact tier 1's
tripwire asserts against, the same routine both enforcement points share.

Name-set only: values never participate (value churn is tier-3 oracle
territory). Severity split:

  retract-class  the live inventory lost names the mapping covers —
                 coverage shrank, so a transient in-Anki tooltip fires once
                 per drift signature (state-dir dedup marker). An unreadable
                 inventory (``aqt.colors`` restructured past recognition)
                 is retract-class: coverage is unknown, assume the worst.
  add-class      the live inventory gained names — log only. A rename is a
                 retract+add pair and the retract half carries the tooltip;
                 an aliased name (the old name survives, a new one joins)
                 leaves the covered name present, so it is add-class at
                 worst — the binding is intact, never a tooltip.

Theming is never gated: the apply stays float-with-tolerant-skip and this
module never writes anywhere outside the plugin state dir. The tooltip copy
is mode-aware — bundled says a plugin update restores coverage, standalone
says a reinstall restores it.

Pure so tier-1 pytest reaches everything (the one cross-module import,
sync's atomic-write helper, is itself stdlib-only): the one live
thing ``run_check`` touches is passed in (the module to introspect, the
log/tooltip callables, the state dir).
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anki_theme.sync import atomic_write_text

# Ships in the payload so the baseline rides ticket 12's propagation: a
# repaired mapping arrives with its own regenerated snapshot.
SNAPSHOT_FILE = Path(__file__).parent / "var_snapshot.txt"

# The dedup marker inside the plugin state dir: signatures already surfaced.
MARKER = "drift_seen.json"

UNREADABLE_SIGNATURE = "<inventory-unreadable>"

RESTORE_BUNDLED = "An Omarchy plugin update will restore them."
RESTORE_STANDALONE = "Reinstalling Anki Theme will restore them."

# Tooltips wrap in a small label; a mass refactor's name list stays readable.
TOOLTIP_NAME_CAP = 5


def _log(message: str) -> None:
    # Same sink as every payload module's own; none imports another to log.
    print(f"[anki_theme] drift: {message}", flush=True)


# -- the snapshot --------------------------------------------------------------


def load_snapshot(path: Path) -> frozenset[str]:
    """The snapshot's var names: non-blank, non-comment lines, stripped.

    Raises OSError when unreadable and ValueError when it carries no names —
    a payload-side defect the caller treats as "check inconclusive", never
    as drift.
    """
    names = set()
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.add(line)
    if not names:
        raise ValueError(f"snapshot at {path} carries no var names")
    return frozenset(names)


# -- the live inventory --------------------------------------------------------


def gather_inventory(colors_module: Any) -> frozenset[str] | None:
    """The live ``aqt.colors`` var name-set, or None when unreadable.

    A var is a public module attribute that is a dict carrying both polarity
    slots — exactly the 51-entry shape of Anki 26.08.1 (verified against the
    installed module; no other public dicts exist there). Zero qualifying
    names or an introspection failure means Anki restructured the module
    past recognition: that is the inventory-unreadable case, not "all
    vars retracted".
    """
    try:
        names = frozenset(
            name
            for name in dir(colors_module)
            if not name.startswith("_")
            and isinstance(entry := getattr(colors_module, name), dict)
            and "light" in entry
            and "dark" in entry
        )
    except Exception:
        return None
    return names or None


# -- the diff ------------------------------------------------------------------


@dataclass(frozen=True)
class Drift:
    """One startup's verdict on the live inventory vs. the snapshot."""

    # Snapshot names the live inventory lost — coverage shrank. Sorted.
    retracted: tuple[str, ...]
    # Live names the snapshot never had — additive churn. Sorted.
    added: tuple[str, ...]
    # The live inventory could not be read at all (retract-class).
    inventory_unreadable: bool = False

    def __bool__(self) -> bool:
        return bool(self.retracted or self.added or self.inventory_unreadable)

    @property
    def needs_tooltip(self) -> bool:
        """Retract-class only — add-class drift never interrupts the user."""
        return self.inventory_unreadable or bool(self.retracted)

    @property
    def signature(self) -> str:
        """The dedup key: one tooltip per retract signature, ever.

        Sorted, so the same retract set always dedups together;
        comma-joined names stay unambiguous (var names are identifiers) and
        human-readable in the marker file.
        """
        if self.inventory_unreadable:
            return UNREADABLE_SIGNATURE
        return ",".join(sorted(self.retracted))

    def log_line(self) -> str:
        if self.inventory_unreadable:
            return "aqt color-var inventory unreadable — treating as retract-class"
        parts = []
        if self.retracted:
            names = ", ".join(self.retracted)
            parts.append(f"retracted {len(self.retracted)} ({names})")
        if self.added:
            names = ", ".join(self.added)
            parts.append(f"added {len(self.added)} ({names})")
        return "aqt color vars drifted from the snapshot — " + "; ".join(parts)

    def tooltip_text(self, *, bundled: bool) -> str:
        restore = RESTORE_BUNDLED if bundled else RESTORE_STANDALONE
        if self.inventory_unreadable:
            return (
                "Anki Theme: this Anki update changed how its color variables are "
                f"defined, so Anki Theme can't tell what it still covers. {restore}"
            )
        names = ", ".join(self.retracted[:TOOLTIP_NAME_CAP])
        if len(self.retracted) > TOOLTIP_NAME_CAP:
            names += ", …"
        plural = "s" if len(self.retracted) > 1 else ""
        return (
            f"Anki Theme: this Anki update renamed or removed {len(self.retracted)} "
            f"color variable{plural} it themes ({names}); affected elements keep "
            f"Anki's own colors for now. {restore}"
        )


def diff_inventory(live: frozenset[str] | None, snapshot: frozenset[str]) -> Drift:
    """Diff the live name-set against the snapshot; ``live is None`` is the
    unreadable inventory (retract-class, no per-name detail)."""
    if live is None:
        return Drift((), (), inventory_unreadable=True)
    return Drift(
        tuple(sorted(snapshot - live)),
        tuple(sorted(live - snapshot)),
    )


# -- the dedup marker ------------------------------------------------------------


def seen_signatures(state_dir: Path) -> set[str]:
    """Signatures already surfaced. An absent/unreadable marker means none
    (the next record replaces whatever was broken)."""
    try:
        marker = json.loads((Path(state_dir) / MARKER).read_text())
    # The trailing comma pins the <=3.13-compatible parens: ruff's py314
    # formatter would otherwise emit PEP 758's bare, 3.14-only form.
    except (
        OSError,
        ValueError,
    ):
        return set()
    signatures = marker.get("signatures") if isinstance(marker, dict) else None
    # Str-only: a hand-mangled marker must fail open to "unseen", not raise
    # past the guard on an unhashable entry.
    if not isinstance(signatures, list):
        return set()
    return {s for s in signatures if isinstance(s, str)}


def record_signature(state_dir: Path, signature: str) -> None:
    """Add a signature to the marker, atomically. Best-effort: a state-dir
    problem logs and never raises — worst case the tooltip fires again."""
    state_dir = Path(state_dir)
    try:
        signatures = seen_signatures(state_dir)
        signatures.add(signature)
        state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            state_dir / MARKER,
            json.dumps({"signatures": sorted(signatures)}, indent=2) + "\n",
        )
    except OSError:
        _log(f"could not record drift signature in {state_dir}")


# -- the one startup check -------------------------------------------------------


def run_check(
    colors_module: Any,
    snapshot_path: Path,
    state_dir: Path,
    *,
    bundled: bool,
    log: Callable[[str], None] = _log,
    tooltip: Callable[[str], None] | None = None,
) -> Drift | None:
    """Gather, diff, surface. None means inconclusive (snapshot unreadable —
    a payload defect, not Anki drift); otherwise the verdict, logged here
    and, for a fresh retract-class signature, shown once via ``tooltip``.

    The order is tooltip-then-record: a failed tooltip call leaves the
    signature unrecorded so the next startup retries it.
    """
    try:
        snapshot = load_snapshot(snapshot_path)
    except (OSError, ValueError) as exc:
        log(f"snapshot unreadable — skipping the drift check ({exc})")
        return None
    drift = diff_inventory(gather_inventory(colors_module), snapshot)
    if not drift:
        return drift
    log(drift.log_line())
    if drift.needs_tooltip:
        if drift.signature in seen_signatures(state_dir):
            log("signature already surfaced — tooltip skipped")
        else:
            if tooltip is not None:
                try:
                    tooltip(drift.tooltip_text(bundled=bundled))
                except Exception:
                    log(f"tooltip failed:\n{traceback.format_exc()}")
                    return drift
            record_signature(state_dir, drift.signature)
    return drift
