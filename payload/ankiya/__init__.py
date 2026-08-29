"""Ankiya — Anki recolored live in the active Omarchy palette.

Add-on entry point. GUI-free logic lives in `palette`/`cssgen` (tier-1
pytest); the Anki-side runtime (applier + watcher) is ticket 17, the sync
bootloader ticket 19 — until then this package imports nothing from aqt.
"""
