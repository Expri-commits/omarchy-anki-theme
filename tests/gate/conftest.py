"""Gate fixtures: paths, vendored PIL, the session, the escape hatch.

The sys.path setup for the whole gate lives here and only here: the vendored
Pillow dir (python-pillow, same cp314 ABI as the system python — bootstrap
`python -m pip install --target tests/gate/vendor pillow`), the payload tree
(`anki_theme.…` imports), and tests/ (`smoke_live_switch.seed_base`). A missing
vendor dir must not break collection of the (deselected) gate modules in
unmarked runs; the PIL import itself stays lazy in sampling.py until a gate
test actually runs.
"""

from __future__ import annotations

import pathlib
import sys

GATE_DIR = pathlib.Path(__file__).resolve().parent
for path in (GATE_DIR / "vendor", GATE_DIR.parent.parent / "payload", GATE_DIR.parent):
    sys.path.insert(0, str(path))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def gate_session(request):
    from gate_harness import GateSession

    session = GateSession(no_restore=request.config.getoption("--no-restore"))
    try:
        session.preflight()
        session.launch()
        yield session
    finally:
        # Also on a failed launch (hello timeout, seed failure, …): a leaked
        # Anki process would block every later run's preflight, and a theme
        # left switched breaks the hygiene contract docs/verification.md sets.
        session.teardown()


@pytest.fixture(scope="session")
def gate3_session(request):
    """Tier 3's matrix instance — same shape as tier 2's, plus user-theme
    forks and the mid-session stop (tier 3's down legs launch their own
    instances; see gate3_down)."""
    from gate_harness import GateSession

    session = GateSession(no_restore=request.config.getoption("--no-restore"))
    try:
        session.preflight()
        session.launch()
        yield session
    finally:
        session.teardown()


@pytest.fixture(scope="session")
def gate3_down(gate3_session):
    """Gateway to the Anki-down legs: stop the matrix instance and restore
    the desktop theme early, so the legs can launch scratch instances of
    their own (the preflight contract forbids concurrent Ankis)."""
    gate3_session.stop_anki()
    gate3_session.restore_theme()
    return gate3_session
