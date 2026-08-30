"""Ankiya — Anki recolored live in the active Omarchy palette.

Add-on entry. GUI-free logic lives in ``palette``/``cssgen`` (tier-1
pytest); ``runtime`` is the Anki-side applier + watcher (ticket 17). Ticket
19 reshapes this entry into a bootloader — sync check first, then runtime
start.
"""

import sys

if "aqt" in sys.modules:  # inside Anki; tier-1 pytest imports stop here
    from aqt import gui_hooks

    from ankiya.runtime import runtime

    gui_hooks.profile_did_open.append(runtime.start)
