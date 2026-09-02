"""Grant: the consent toast's click action (ticket 11's helper, built by 20).

The notification's ``--exec`` runs this detached from the shell — the click
itself is the whole grant ceremony, so it must complete without the service
being alive to notice. Two steps, in the order that matters:

1. Record consent: ``consent.json`` in the plugin state dir, ticket 11's
   schema, written atomically at mode 0600 (mkstemp never creates group- or
   world-readable files). Consent lands *before* anything else is written —
   from this point the install it licenses is a promised write, not a
   surprise.
2. Mount Sync: ``/usr/bin/python sync.py …`` as a subprocess, so the
   install/update runs fresh code straight from the plugin tree (ticket 12's
   rule) and its one JSON result line lands on stdout for the journal.

A click also arrives for a consent that was already granted (a re-shown
toast, a slow finger): recording is idempotent and Sync converges, so the
second run is a no-op install. Failure of the Sync leg leaves consent
recorded — the grant happened; the next service start finishes the job.

The state dir is pinned (H9): ``main`` records consent only into the
canonical ``$HOME/.local/state/omarchy/anki-theme`` — Service.qml's
``stateDir``, the one location the gate ever reads back — and refuses
anything else with a usage error before writing a byte.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from gate import ADDON_SEGMENT, CONSENT_FILE, python_argv, usage_error

SYNC_TIMEOUT_S = 120.0


def record_consent(state_dir: Path, plugin_version: str) -> Path:
    """Write the grant atomically at 0600; returns the consent path.

    Ticket 11's schema: ``{"granted": true, "decidedAt": <ISO>,
    "pluginVersion": <semver>}`` — the version is what shipped when the user
    said yes, for future re-consent questions to reason about.
    """
    consent = state_dir / CONSENT_FILE
    payload = {
        "granted": True,
        "decidedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "pluginVersion": plugin_version,
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    # mkstemp creates 0600 — the consent record is never world-readable,
    # even for the instant it exists under its temp name.
    fd, tmp_name = tempfile.mkstemp(dir=state_dir, prefix=f".{CONSENT_FILE}.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, consent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return consent


def canonical_state_dir() -> Path:
    """The one state dir a grant may write: ``$HOME/.local/state/omarchy/
    anki-theme`` — Service.qml's ``stateDir``, the only consent location
    the service's gate reads back. Resolved, so a symlinked HOME compares
    equal to the path the QML built from it.
    """
    return (Path.home() / ".local/state/omarchy/anki-theme").resolve()


def read_plugin_version(plugin_dir: Path) -> str:
    """The manifest's version — best-effort; "unknown" never blocks a grant."""
    try:
        manifest = json.loads((plugin_dir / "manifest.json").read_text())
    # The trailing comma pins the <=3.13-compatible parens: ruff's py314
    # formatter would otherwise emit PEP 758's bare, 3.14-only form.
    except (
        OSError,
        ValueError,
    ):
        return "unknown"
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return version if isinstance(version, str) and version else "unknown"


def main(argv: list[str]) -> int:
    """``grant.py <anki2_root> <state_dir>`` — record consent, mount Sync.

    ``state_dir`` must be the canonical one (``canonical_state_dir``): the
    QML always passes that, and consent recorded anywhere else would be
    consent the service never sees. Anything else is a usage error — exit
    2, stderr, nothing written.

    Stdout relays Sync's single JSON result line for the journal; logs go to
    stderr, mirroring the gate and the sync CLI.
    """
    if len(argv) != 2:
        return usage_error()
    anki2_root, state_dir = Path(argv[0]), Path(argv[1])
    canonical = canonical_state_dir()
    if state_dir.resolve() != canonical:
        print(
            f"grant: refusing state dir {state_dir} — consent is only recorded "
            f"into the canonical state dir {canonical}",
            file=sys.stderr,
        )
        return 2
    plugin_dir = Path(__file__).resolve().parent.parent
    bundled = plugin_dir / "payload" / "anki_theme"
    installed = anki2_root / ADDON_SEGMENT

    consent = record_consent(state_dir, read_plugin_version(plugin_dir))
    print(f"[anki_theme] grant: consent recorded at {consent}", file=sys.stderr)

    try:
        proc = subprocess.run(
            python_argv(bundled / "sync.py", bundled, installed, state_dir),
            capture_output=True,
            text=True,
            timeout=SYNC_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print(f"[anki_theme] grant: sync did not finish in {SYNC_TIMEOUT_S:.0f}s", file=sys.stderr)
        return 1
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
