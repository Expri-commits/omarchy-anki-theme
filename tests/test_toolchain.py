"""Toolchain guard: tests must run under system python (the Anki runtime)."""

import sys
from pathlib import Path


def test_running_under_system_python():
    exe = Path(sys.executable).resolve()
    assert exe == Path("/usr/bin/python").resolve(), (
        f"tests ran under {exe}; invoke as `pytest` or `/usr/bin/python -m pytest` — "
        "bare `python -m pytest` resolves to mise python, which cannot see aqt"
    )
