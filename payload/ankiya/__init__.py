"""Ankiya — Anki recolored live in the active Omarchy palette.

Add-on entry as a bootloader (tickets 12/19): the sync check runs before
anything else imports, so a payload update that lands mid-startup bounds the
mixed-version exposure to one hop — already-imported modules keep executing
their old inodes, while everything imported after the swap resolves through
the package ``__path__``, which the swap keeps pointed at this same folder.
Two contracts make that hop safe and must stay stable across payload
versions: the sync protocol (stamp schema + swap steps) and
``runtime.start()``.

GUI-free logic lives in ``palette``/``cssgen``/``theme_clamp``/``sync``/
``drift`` (tier-1 pytest); ``runtime`` is the Anki-side applier + watcher
(ticket 17).

Fail-open by contract: a sync crash is logged and skipped — the running code
themes this session, and Anki's own ``loadAddons`` try/except is the outer
net. Outside Anki (tier-1 pytest), the ``aqt`` guard makes importing this
package inert.
"""

import atexit
import os
import pathlib
import shutil
import sys
import traceback

PLUGIN_ID = "io.github.expri-commits.anki-theme"
# Dev-loop / smoke override for the bundled-payload location; unset in
# production, where the marketplace install is the source.
BUNDLED_PAYLOAD_ENV = "ANKIYA_BUNDLED_PAYLOAD"
DEFAULT_BUNDLED_DIR = pathlib.Path.home() / ".config/omarchy/plugins" / PLUGIN_ID / "payload/ankiya"


def bundled_payload_dir() -> pathlib.Path:
    override = os.environ.get(BUNDLED_PAYLOAD_ENV)
    return pathlib.Path(override) if override else DEFAULT_BUNDLED_DIR


def _log(message: str) -> None:
    # Same one-line sink as sync's and runtime's; each module owns its own so
    # none ever imports another just to log (the standalone-module rule).
    print(f"[ankiya] {message}", flush=True)


def _boot() -> None:
    from ankiya import sync  # imported before runtime: the swap must land first

    try:
        result = sync.ensure_current(
            bundled_payload_dir(),
            pathlib.Path(__file__).parent,
            pathlib.Path.home() / ".local/state/omarchy/anki-theme",
            defer_old_cleanup=True,
        )
    except Exception:
        # Fail-open (ticket 12): skip the update, old code themes this session.
        _log(f"sync crashed — skipping update this session:\n{traceback.format_exc()}")
        return
    if result.old_dir is not None:
        # Keep this session's tracebacks readable; removal rides exit, and a
        # crash before then is swept on the next run.
        atexit.register(shutil.rmtree, result.old_dir, ignore_errors=True)


if "aqt" in sys.modules:  # inside Anki; tier-1 pytest imports stop here
    from aqt import gui_hooks

    _boot()

    from ankiya.runtime import runtime  # must follow the swap: one-hop bound

    gui_hooks.profile_did_open.append(runtime.start)
