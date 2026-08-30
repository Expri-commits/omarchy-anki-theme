"""Make the bundled payload importable as the add-on would be (`ankiya.…`).

payload/ mirrors the add-ons dir layout: inside Anki the payload tree is
installed as `addons21/ankiya/`, so its modules import as `ankiya.palette`;
tests get the same view by putting payload/ on sys.path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "payload"))


def pytest_addoption(parser):
    """The gate's escape hatch (docs/verification.md infrastructure notes):
    keep the flipped theme and the scratch base for debugging a failed run."""
    parser.addoption(
        "--no-restore",
        action="store_true",
        help="gate: skip theme restore and scratch-base cleanup after the run",
    )
