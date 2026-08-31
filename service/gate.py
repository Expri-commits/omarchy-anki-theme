"""Gate: the service's one decision pass (tickets 11/12/13, built by 20).

At every service start the QML runs this as a ``/usr/bin/python`` subprocess
and acts on the single JSON line it prints — so all delivery decisions live
in fresh, pytest-reachable python next to the payload, not in QML that the
shell may re-instantiate from a stale compiled component
(basecamp/omarchy#6981). The service itself stays glue: run the gate, then
log, spawn Sync, or send one notification, exactly as the decision says.

The decision, in order:

Version  Omarchy 4.0.1+ or inert (ticket 13's floor). ``omarchy-version``
         predates 4.0.0 and is the one surface that can version; a missing,
         failing, or unparseable command gates inert too — fail closed, the
         consent toast's ``--exec`` contract is not worth guessing at.

Consent  No ``consent.json`` granting yet and Anki's data dir exists: ask
         once per service start via an Omarchy notification whose click runs
         the bundled grant helper (ticket 11). Before that grant the service
         writes nothing at all — the gate only reads.

Reinstall  Consent on file, our ``installed.json`` marker in the state dir,
         but no ``addons21/anki_theme``: the add-on was deleted inside Anki
         (ticket 12). Never resurrect silently — one click-to-reinstall
         toast per service start; ignoring it leaves Anki as it is.

Sync     Otherwise: consent already covers installs and updates, so mount
         Sync (``/usr/bin/python sync.py``, fresh code only — ticket 12).
         Also how a plugin update propagates at the next shell restart.

Every ``exec`` the decision carries is a complete argv, so the QML never
computes a path. Like ``sync.main``: exactly one JSON line on stdout, logs
and usage errors on stderr, only a crash exits nonzero.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# Every helper the service spawns runs on the system python — the same
# runtime the add-on targets, never a version manager's.
PYTHON = "/usr/bin/python"

# Ticket 13: 4.0.1 settled the notification click-action contract this
# service depends on; v4.0.0's shell-string form is incompatible.
VERSION_FLOOR: tuple[int, int, int] = (4, 0, 1)
VERSION_CMD = "omarchy-version"
VERSION_TIMEOUT_S = 5.0

# State-dir names. The marker is Sync's (sync.py); re-declared rather than
# imported because plugin-side code must not import payload code — the
# payload is what updates, and the gate has to keep working across a
# half-updated tree.
CONSENT_FILE = "consent.json"
INSTALLED_MARKER = "installed.json"

# The actions, in decision order.
INERT = "inert"
IDLE = "idle"
ASK_CONSENT = "ask_consent"
OFFER_REINSTALL = "offer_reinstall"
SYNC = "sync"

ADDON_SEGMENT = "addons21/anki_theme"

# A version line looks like ``4.0.1-1`` (major.minor.patch-channel); the
# channel suffix and anything after it are ignored. Two components are the
# parse minimum — Omarchy has always shipped three.
_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?")


def python_argv(script: Path, *args: Path | str) -> list[str]:
    """Argv for a helper subprocess: system python, ``-B`` — the service
    never dirties the plugin tree it runs from (a stray ``__pycache__``
    trips the shell's plugin-reload watcher)."""
    return [PYTHON, "-B", str(script), *(str(a) for a in args)]


def parse_version(text: str) -> tuple[int, int, int] | None:
    """The version triple a ``omarchy-version`` line carries, or None.

    None is the fail-closed answer for everything unparsable — the gate
    treats an unknown Omarchy exactly like an old one.
    """
    match = _VERSION_RE.match(text)
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3] or 0))


def run_version_cmd() -> tuple[tuple[int, int, int] | None, str]:
    """Run ``omarchy-version``; the parsed triple and what was seen.

    The second element is a human-readable detail for the inert journal
    line, so a gated machine can tell "old" from "broken" apart.
    """
    try:
        proc = subprocess.run(
            [VERSION_CMD], capture_output=True, text=True, timeout=VERSION_TIMEOUT_S
        )
    except FileNotFoundError:
        return None, f"'{VERSION_CMD}' not found on PATH"
    except subprocess.TimeoutExpired:
        return None, f"'{VERSION_CMD}' timed out after {VERSION_TIMEOUT_S:.0f}s"
    except OSError as exc:
        return None, f"'{VERSION_CMD}' failed to run ({exc.strerror or exc})"
    if proc.returncode != 0:
        return None, f"'{VERSION_CMD}' exited {proc.returncode}"
    raw = next((line.strip() for line in proc.stdout.splitlines() if line.strip()), "")
    version = parse_version(raw)
    if version is None:
        return None, f"unparseable output {raw!r}"
    return version, f"'{raw}'"


def read_consent(state_dir: Path) -> bool:
    """Whether ``consent.json`` records a grant (ticket 11's schema).

    Anything else — absent, unreadable, not JSON, ``granted`` not true —
    counts as unanswered; the ask repeats once per service start.
    """
    try:
        consent = json.loads((state_dir / CONSENT_FILE).read_text())
    # The trailing comma pins the <=3.13-compatible parens: ruff's py314
    # formatter would otherwise emit PEP 758's bare, 3.14-only form.
    except (
        OSError,
        ValueError,
    ):
        return False
    return isinstance(consent, dict) and consent.get("granted") is True


def decide(
    *,
    version: tuple[int, int, int] | None,
    version_detail: str,
    consented: bool,
    anki2_exists: bool,
    marker_exists: bool,
    installed_exists: bool,
    plugin_dir: Path,
    anki2_root: Path,
    state_dir: Path,
) -> dict:
    """The whole service decision over gathered facts — pure, so the
    decision table is pytest-reachable without spawning anything.

    Returns the JSON-serializable decision: ``action``, ``message``, and
    for the acting outcomes a complete ``exec`` argv (and ``toast`` copy
    where the action is a notification).
    """
    grant_helper = plugin_dir / "service" / "grant.py"
    bundled = plugin_dir / "payload" / "anki_theme"
    installed = anki2_root / ADDON_SEGMENT
    sync_argv = python_argv(bundled / "sync.py", bundled, installed, state_dir)
    consent_toast = {
        "headline": "Anki Theme: install its Anki add-on?",
        "body": (
            "Click to allow placing the add-on in "
            f"{installed} — it recolors Anki in your Omarchy theme and keeps "
            "itself updated. Anki stays untouched until then."
        ),
    }
    reinstall_toast = {
        "headline": "Anki Theme: the Anki add-on was removed",
        "body": (f"Click to reinstall it to {installed}. Ignore this and Anki stays as it is."),
    }

    if version is None or version < VERSION_FLOOR:
        return {
            "action": INERT,
            "message": (
                f"Omarchy {'.'.join(map(str, VERSION_FLOOR))}+ required, "
                f"{VERSION_CMD} says {version_detail} — staying inert "
                "(nothing mounted, no toast)"
            ),
        }
    if not anki2_exists:
        return {
            "action": IDLE,
            "message": (
                f"no Anki data dir at {anki2_root} — nothing to deliver into; "
                "re-checked at the next service start"
            ),
        }
    if not consented:
        return {
            "action": ASK_CONSENT,
            "message": "consent unrecorded — asking once via notification",
            "toast": consent_toast,
            "exec": python_argv(grant_helper, anki2_root, state_dir),
        }
    if marker_exists and not installed_exists:
        return {
            "action": OFFER_REINSTALL,
            "message": (
                "the add-on was deleted in Anki — offering one reinstall "
                "notification, never resurrecting silently"
            ),
            "toast": reinstall_toast,
            "exec": sync_argv,
        }
    return {
        "action": SYNC,
        "message": "consent on file — mounting Sync to converge the payload",
        "exec": sync_argv,
    }


def main(argv: list[str]) -> int:
    """``gate.py <anki2_root> <state_dir>`` — one decision JSON line."""
    if len(argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <anki2_root> <state_dir>", file=sys.stderr)
        return 2
    anki2_root, state_dir = Path(argv[0]), Path(argv[1])
    version, version_detail = run_version_cmd()
    decision = decide(
        version=version,
        version_detail=version_detail,
        consented=read_consent(state_dir),
        anki2_exists=anki2_root.is_dir(),
        marker_exists=(state_dir / INSTALLED_MARKER).is_file(),
        installed_exists=(anki2_root / ADDON_SEGMENT).is_dir(),
        plugin_dir=Path(__file__).resolve().parent.parent,
        anki2_root=anki2_root,
        state_dir=state_dir,
    )
    print(json.dumps(decision, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
