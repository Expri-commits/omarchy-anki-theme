#!/usr/bin/env python
"""Throwaway characterization driver: probe the seeded stats page with the
real stats.js, capture it, and scan the Added-graph canvas for the mapped
STATE_NEW bar at flot's 0.7 fill (system python, repo root)."""

import json
import pathlib
import sys

GATE_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(GATE_DIR / "vendor"))
sys.path.insert(0, str(GATE_DIR))
sys.path.insert(0, str(GATE_DIR.parent))

from gate_harness import GateSession  # noqa: E402
from oracles import ThemeOracle  # noqa: E402
from points import dom, shot_offset  # noqa: E402
from sampling import TOLERANCE, Shot, channel_delta  # noqa: E402

session = GateSession(no_restore=False)
session.preflight()
session.launch()
try:
    # The oracle must describe the theme actually on screen — switch first,
    # exactly like the matrix legs (the first stats characterization ran on
    # whatever theme the desktop happened to carry and chased a ghost).
    oracle = ThemeOracle("catppuccin")
    session.switch("catppuccin")
    reply = session.cmd("open_stats")
    print("open_stats:", json.dumps(reply)[:160])
    probe = session.probe("stats")
    print("probe:", json.dumps(probe.get("dom", {}).get("stats", {}))[:300])
    shot = Shot(session.capture("stats", "char"))
    expected = oracle.stats_added_bar
    d = dom(probe, "stats")
    dpr = d.get("dpr") or 1.0
    vx, vy, _vw, _vh = probe["views"]["stats"]
    rect = d["intro_canvas"]
    dx, dy = shot_offset(session, probe, shot)
    x0 = int(vx + rect["x"] * dpr) + dx
    y0 = int(vy + rect["y"] * dpr) + dy
    w, h = int(rect["w"] * dpr), int(rect["h"] * dpr)
    best, best_xy = None, None
    for py in range(y0 + 1, y0 + h - 1):
        for px in range(x0 + 1, x0 + w - 1):
            s = shot.px(px, py)
            delta = channel_delta(s, expected)
            if best is None or delta < best:
                best, best_xy = delta, (px, py, s)
    print(f"expected STATE_NEW@0.7 rgb{expected}; closest {best_xy} delta {best} (tol {TOLERANCE})")
finally:
    session.teardown()
