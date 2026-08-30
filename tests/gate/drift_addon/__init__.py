"""Dev-only drift injector add-on — tier 3's drift-smoke actor (ticket 23).

Never shipped. Sorted **before** ``ankiya`` (folder name ``aa_gate_drift``),
so it loads first: it wraps ``aqt.utils.tooltip`` with a recorder (Ankiya's
runtime imports the symbol at module import — after this — so the wrap
binds), then mutates the live ``aqt.colors`` inventory per
``ANKIYA_DRIFT_MODE``:

  retract   delete a real snapshot var — coverage shrank → the tooltip path
  add       add a fake var — additive churn → log-only

The recorder appends one JSON line per tooltip call to ``ANKIYA_DRIFT_LOG``,
then calls the original: the real transient widget render still happens.

With no mode set the add-on is inert — every other gate leg runs through it
harmlessly, which is itself the baseline (no drift lines in their logs).
"""

from __future__ import annotations

import json
import os

MODE = os.environ.get("ANKIYA_DRIFT_MODE")
LOG = os.environ.get("ANKIYA_DRIFT_LOG")

if MODE:
    import aqt.colors as ak_colors
    import aqt.utils

    if MODE == "retract":
        # A real snapshot name aqt never dereferences at apply time: a mock
        # that deleted, say, CANVAS_CODE would crash theme_manager's own
        # _apply_palette (theme.py dereferences it), aborting the startup
        # apply unrecorded — an unfaithful mock, since real retract-class
        # drift means aqt's code stopped referencing the var. CANVAS_INSET
        # is only consumed by generated QSS after the palette exists, and
        # the runtime's tolerant skip handles the missing slot gracefully
        # for the rest of the session.
        if hasattr(ak_colors, "CANVAS_INSET"):
            del ak_colors.CANVAS_INSET
    elif MODE == "add":
        # The gather shape: a public dict with both polarity slots.
        ak_colors.ANKIYA_GATE_EXTRA = {"light": "#000000", "dark": "#000000"}

    _original_tooltip = aqt.utils.tooltip

    def _recording_tooltip(message, *args, **kwargs):
        if LOG:
            with open(LOG, "a") as f:
                f.write(json.dumps({"text": str(message)}) + "\n")
        return _original_tooltip(message, *args, **kwargs)

    aqt.utils.tooltip = _recording_tooltip
