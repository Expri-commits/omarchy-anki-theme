"""Sample-map point geometry: DOM/QT/menu points → shot coordinates.

Shared by the tier-2 and tier-3 modules (moved out of test_gate.py when tier
3's surfaces started consuming the same machinery). A sample-map point spec
names a view (a webview rect from the probe, a Qt widget rect, or the gate
add-on's menu geometry) and an anchor inside it; these helpers turn that into
shot coordinates and sample/assert against an expected color.
"""

from __future__ import annotations

from sampling import TOLERANCE, Shot, assert_color, channel_delta, scan_for_color  # noqa: F401


def dom(probe: dict, view: str) -> dict:
    entry = probe.get("dom", {}).get(view)
    if not entry or not entry.get("ok"):
        raise AssertionError(f"DOM probe for view {view!r} missing or failed: {entry}")
    return entry["result"]


def shot_offset(session, probe: dict, shot: Shot) -> tuple[int, int]:
    dx, dy = session.shot_offset(shot, probe["window"])
    if dx < 0 or dy < 0:
        raise AssertionError(
            f"shot {shot.size} smaller than window {probe['window']} — capture mismatch"
        )
    return dx, dy


def _menubar_action_rect(probe: dict, surface: str, point_name: str, wanted: str) -> tuple:
    actions = probe["qt"].get("menubar_actions", [])
    rects = [a["rect"] for a in actions if a["text"].replace("&", "") == wanted]
    if not rects:
        raise AssertionError(
            f"{surface}/{point_name}: no menubar action {wanted!r} in "
            f"{[a['text'] for a in actions]}"
        )
    return rects[0]


def _menu_anchor_rect(probe: dict, surface: str, point_name: str, spec: dict) -> tuple:
    """The menu-local rect a menu point anchors to: the whole popup, the
    highlighted action, or a named action."""
    menu = probe["menu"]
    anchor = spec.get("anchor")
    if anchor == "menu.active":
        if not 0 <= menu["active"] < len(menu["actions"]):
            raise AssertionError(
                f"{surface}/{point_name}: no highlighted menu action "
                f"(active={menu['active']}) — the open_menu highlight never landed"
            )
        return tuple(menu["actions"][menu["active"]]["rect"])
    if anchor == "menu.action":
        acts = [a for a in menu["actions"] if a["text"].replace("&", "") == spec["action"]]
        if not acts:
            raise AssertionError(f"{surface}/{point_name}: menu action {spec['action']!r} missing")
        return tuple(acts[0]["rect"])
    return 0, 0, menu["rect"][2], menu["rect"][3]


def _qt_anchor_rect(probe: dict, surface: str, point_name: str, spec: dict) -> tuple:
    """The rect a Qt-anchored point resolves to: the menubar, a named menubar
    action, or a generic probe["qt"][<key>] rect (e.g. the native prefs
    dialog's tab widget — _prefs_qt_rects)."""
    anchor = spec["anchor"]
    if anchor == "qt.menubar":
        return tuple(probe["qt"]["menubar"])
    if anchor == "qt.action":
        return tuple(_menubar_action_rect(probe, surface, point_name, spec["action"]))
    key = anchor.removeprefix("qt.")
    try:
        x, y, w, h = probe["qt"][key]
    except KeyError, ValueError, TypeError:
        raise AssertionError(f"{surface}/{point_name}: no qt rect {anchor!r} in probe") from None
    return x, y, w, h


def window_xy(session, probe: dict, surface: str, point_name: str) -> tuple[float, float]:
    spec = session.map["points"][surface][point_name]
    if spec["view"] == "qt":
        x, y, w, h = _qt_anchor_rect(probe, surface, point_name, spec)
    elif spec["view"] == "menu":
        # The gate add-on's open-menu report: the popup's own geometry, captured
        # as a global rect shot — so menu points are menu-local with no window
        # offset applied (the shot *is* the menu).
        x, y, w, h = _menu_anchor_rect(probe, surface, point_name, spec)
    else:
        view = spec["view"]
        d = dom(probe, view)
        dpr = d.get("dpr") or 1.0
        vx, vy, vw, vh = probe["views"][view]
        anchor = spec.get("anchor")
        if anchor:
            rect = d[anchor.removeprefix("dom.")]
            x = vx + (rect["x"] + rect["w"] * spec.get("fx", 0.5) + spec.get("dx", 0)) * dpr
            y = vy + (rect["y"] + rect["h"] * spec.get("fy", 0.5) + spec.get("dy", 0)) * dpr
        else:
            x = vx + (vw / dpr) * spec.get("fx", 0.5)
            y = vy + (vh / dpr) * spec.get("fy", 0.5)
        return x, y
    return (
        x + w * spec.get("fx", 0.5) + spec.get("dx", 0),
        y + h * spec.get("fy", 0.5) + spec.get("dy", 0),
    )


def point(session, probe: dict, surface: str, point_name: str, shot: Shot) -> tuple[int, int]:
    x, y = window_xy(session, probe, surface, point_name)
    if session.map["points"][surface][point_name]["view"] == "menu":
        return int(round(x)), int(round(y))
    dx, dy = shot_offset(session, probe, shot)
    return int(round(x)) + dx, int(round(y)) + dy


def scan_region(
    session, probe: dict, surface: str, point_name: str, shot: Shot
) -> tuple[int, int, int, int]:
    """The shot-coordinates region a scan point searches: the anchor rect
    (DOM or menubar action) for glyph scans, the 6px strip just left of the
    anchor for focus rings (the ring is the element's border)."""
    spec = session.map["points"][surface][point_name]
    if spec["view"] == "qt":
        x, y, w, h = _qt_anchor_rect(probe, surface, point_name, spec)
        dx, dy = shot_offset(session, probe, shot)
    elif spec["view"] == "menu":
        # Menu-local action rect inside the popup's own region shot.
        x, y, w, h = _menu_anchor_rect(probe, surface, point_name, spec)
        dx = dy = 0
    else:
        view = spec["view"]
        d = dom(probe, view)
        dpr = d.get("dpr") or 1.0
        vx, vy, _vw, _vh = probe["views"][view]
        rect = d[spec["anchor"].removeprefix("dom.")]
        x = vx + rect["x"] * dpr
        y = vy + rect["y"] * dpr
        w = rect["w"] * dpr
        h = rect["h"] * dpr
        dx, dy = shot_offset(session, probe, shot)
    x, y, w, h = x + dx, y + dy, w, h
    if spec.get("scan_ring"):
        # The focused field's ring (a CSS outline) paints in the 1-2 px
        # around the rect's edge — cover x-3..x so the ring column is in.
        return int(x - 3), int(y + 2), 4, int(h - 4)
    return int(x) + 1, int(y) + 1, int(w) - 2, int(h) - 2


def sample(session, probe, surface, point_name, shot, expected, scan=False):
    """One sample-map point → (xy, sampled rgb), asserted against `expected`."""
    if scan:
        region = scan_region(session, probe, surface, point_name, shot)
        xy, got = scan_for_color(shot, region, expected)
        delta = channel_delta(got, expected)
        if delta > TOLERANCE:
            raise AssertionError(
                f"{surface}/{point_name}: closest pixel rgb{got} at {xy} still "
                f"{delta} > {TOLERANCE}/channel from expected rgb{expected} ({shot.path})"
            )
        return xy, got
    xy = point(session, probe, surface, point_name, shot)
    return xy, assert_color(surface, point_name, shot, xy, expected)
