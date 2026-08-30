#!/usr/bin/python
"""Regenerate the payload's vendored aqt.colors var-name snapshot.

The Anki-upgrade move from wayfinder tickets 15/21 (documented in
docs/verification.md, tier 1): run this on every Anki version bump, then
repair the mapping (``VAR_RULES`` in ``ankiya/palette.py``) for any
retracted/added names it reports and re-run tier 1 — the tripwire test
fails first if the mapping lags the new inventory.

Must import ``ankiya.drift`` before ``aqt``: importing the package runs the
bootloader, which is inert unless ``aqt`` is already in ``sys.modules``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "payload"))

from ankiya.drift import (  # noqa: E402
    SNAPSHOT_FILE,
    diff_inventory,
    gather_inventory,
    load_snapshot,
)

HEADER = """\
# Anki {version} aqt.colors var-name snapshot — the drift baseline (wayfinder
# tickets 15/21). Tier 1's tripwire asserts the mapping covers every name
# here; the add-on's startup check diffs the live inventory against it.
# Regenerate on every Anki upgrade: /usr/bin/python scripts/regen_var_snapshot.py
"""


def main() -> int:
    import aqt.colors as ak_colors
    from anki.buildinfo import version

    live = gather_inventory(ak_colors)
    if live is None:
        print(
            "aqt.colors carries no recognizable var entries — not writing a snapshot",
            file=sys.stderr,
        )
        return 1
    try:
        old = load_snapshot(SNAPSHOT_FILE)
    except OSError, ValueError:
        old = frozenset()
    body = HEADER.format(version=version) + "".join(f"{name}\n" for name in sorted(live))
    SNAPSHOT_FILE.write_text(body)
    print(f"wrote {len(live)} names to {SNAPSHOT_FILE} (Anki {version})")
    verdict = diff_inventory(live, old)
    if verdict:
        print(f"vs the previous snapshot: {verdict.log_line()}")
        print("repair VAR_RULES for these names, then re-run tier 1")
    else:
        print("identical to the previous snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
