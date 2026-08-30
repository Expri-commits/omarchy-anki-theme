"""Dev-only gate control add-on — tier 2's control channel (ticket 22).

Never shipped: the harness copies this folder into the scratch base's
``addons21/zz_gate_control`` (sorted after ``ankiya``, so it loads once the
theming add-on is up). Pytest drives the running Anki through a command
directory the add-on polls with a QTimer — one JSON ``*.cmd`` file in, one
JSON ``*.done`` file out, then the cmd file is removed.

Commands (spec dict field ``cmd``):
  hello        reply with the Anki version + profile name
  seed         create the Gate deck + one due card (the review fixture)
  show_deck    select the Gate deck, open the deck browser
  show_review  select the Gate deck, enter the reviewer, async-poll
               ``reviewer.card`` (ticket 06's hardened driver pattern)
  open_add     open the Add window (sveltekit) and keep it open
  probe        run DOM probes (JS from the versioned sample map) on the
               target window's webviews and report widget rects in window
               coordinates — everything the pytest side needs to map DOM
               points onto its window screenshots

Every exception lands in the done file instead of crashing Anki; nothing here
writes outside the scratch session dir (GATE_CTL_DIR, set by the harness).
"""

from __future__ import annotations

import json
import os
import pathlib
import traceback

import anki.buildinfo
from aqt import gui_hooks, mw
from aqt.qt import QPoint, QTimer
from aqt.webview import AnkiWebView

CTL_DIR = pathlib.Path(os.environ["GATE_CTL_DIR"])
DECK_NAME = "Gate"
CARD_FACE_BG = "#01ac53"  # the seeded notetype's authored card background
POLL_MS = 100
SETTLE_MS = 600  # webview repaint after a state change, before reporting
JS_TIMEOUT_POLLS = 100  # x100 ms waiting for runJavaScript callbacks

_poll_timer: QTimer | None = None
# cmd stems handed to async handlers (they finish later); without this the
# 100 ms poller would re-execute them until their result lands.
_in_flight: set[str] = set()


def _log(message: str) -> None:
    print(f"[gate] {message}", flush=True)


def _finish(cmd_file: pathlib.Path, payload: dict) -> None:
    _in_flight.discard(cmd_file.stem)
    done = cmd_file.with_suffix(".done")
    try:
        # Atomic: the harness polls for the done file and must never read a
        # half-written result.
        staging = cmd_file.with_suffix(".done.tmp")
        staging.write_text(json.dumps(payload))
        os.replace(staging, done)
        cmd_file.unlink()
    except OSError:
        _log(f"cannot write result for {cmd_file.name}:\n{traceback.format_exc()}")


def _poll() -> None:
    try:
        commands = sorted(CTL_DIR.glob("*.cmd"))
    except OSError:
        return
    for cmd_file in commands:
        if cmd_file.stem in _in_flight:
            continue
        try:
            spec = json.loads(cmd_file.read_text())
        except (OSError, ValueError):
            _finish(cmd_file, {"ok": False, "error": f"unreadable command {cmd_file.name}"})
            continue
        _log(f"command {spec.get('cmd')!r}")
        try:
            result = _execute(cmd_file, spec)
        except Exception:
            _finish(cmd_file, {"ok": False, "error": traceback.format_exc()})
            continue
        if result is None:
            _in_flight.add(cmd_file.stem)
        else:
            _finish(cmd_file, result)


def _execute(cmd_file: pathlib.Path, spec: dict) -> dict | None:
    name = spec.get("cmd")
    if name == "hello":
        return {"ok": True, "version": anki.buildinfo.version, "profile": mw.pm.name}
    if name == "seed":
        return _seed()
    if name == "show_deck":
        return _show_deck(cmd_file)
    if name == "show_review":
        return _show_review(cmd_file)
    if name == "open_add":
        return _open_add(cmd_file)
    if name == "probe":
        return _probe(cmd_file, spec)
    return {"ok": False, "error": f"unknown command {name!r}"}


def _gate_deck_id() -> int:
    return mw.col.decks.id(DECK_NAME)


def _seed() -> dict:
    """The review fixture: Gate deck + one due card on a notetype whose card
    template paints its own background. The authored hex is the oracle for
    the "card face keeps notetype CSS" assert — app theming must never touch
    it (ticket 07 rule 4), so it doubles as the unchanged-ness witness."""
    deck_id = mw.col.decks.id(DECK_NAME)
    if not mw.col.models.by_name("Gate Basic"):
        model = mw.col.models.copy(mw.col.models.by_name("Basic"))
        model["name"] = "Gate Basic"
        template = model["tmpls"][0]
        template["qfmt"] = '<div class="card">{{Front}}</div>'
        template["afmt"] = (
            '<div class="card">{{Front}}</div><hr id="answer">{{Back}}'
        )
        model["css"] = (
            ".card {\n"
            "  font-family: arial;\n"
            f"  background-color: {CARD_FACE_BG};\n"
            "}\n"
        )
        mw.col.models.save(model)
    model = mw.col.models.by_name("Gate Basic")
    note = mw.col.new_note(model)
    note["Front"] = "Gate card face"
    note["Back"] = "Gate back"
    mw.col.add_note(note, deck_id)
    _log(f"seeded deck {deck_id}, note {note.id}")
    return {"ok": True, "deck_id": deck_id, "note_id": note.id, "card_face_bg": CARD_FACE_BG}


def _show_deck(cmd_file: pathlib.Path) -> None:
    mw.col.decks.select(_gate_deck_id())
    mw.moveToState("deckBrowser")
    QTimer.singleShot(SETTLE_MS, lambda: _finish(cmd_file, {"ok": True, "state": mw.state}))
    return None


def _show_review(cmd_file: pathlib.Path) -> None:
    mw.col.decks.select(_gate_deck_id())
    mw.moveToState("review")

    # Two-stage wait (ticket 06's hardened pattern, plus its DOM lesson):
    # first the reviewer's card object, then the card's DOM injection into
    # #qa — the eval lags the python object by a tick, and the surface
    # asserts need the rendered card, not the intent to render one.
    def poll_card(tries: int) -> None:
        card = getattr(mw.reviewer, "card", None)
        if card is None and tries < 60:
            QTimer.singleShot(500, lambda: poll_card(tries + 1))
            return
        poll_dom(0, card)

    def poll_dom(tries: int, card) -> None:
        def dom_ready(html) -> None:
            if (html or "").strip() or tries >= 25:
                _finish(
                    cmd_file,
                    {
                        "ok": bool((html or "").strip()),
                        "card_id": getattr(card, "id", None),
                        "state": mw.state,
                    },
                )
                return
            QTimer.singleShot(200, lambda: poll_dom(tries + 1, card))

        mw.web.page().runJavaScript(
            "(document.getElementById('qa') || {}).innerHTML || ''", dom_ready
        )

    poll_card(0)
    return None


def _add_window():
    for top in mw.app.topLevelWidgets():
        if top is mw or not top.isVisible():
            continue
        if "Add" in (top.windowTitle() or ""):
            return top
    return None


def _biggest_webview(window):
    candidates = [v for v in window.findChildren(AnkiWebView) if v.isVisible()]
    if not candidates:
        return None
    return max(candidates, key=lambda v: v.width() * v.height())


def _open_add(cmd_file: pathlib.Path) -> None:
    mw.onAddCard()

    def poll_window(tries: int) -> None:
        window = _add_window()
        if window is None:
            if tries < 40:
                QTimer.singleShot(500, lambda: poll_window(tries + 1))
            else:
                _finish(cmd_file, {"ok": False, "error": "Add window never appeared"})
            return
        poll_ready(0)

    def poll_ready(tries: int) -> None:
        # The window appears long before the sveltekit editor hydrates; the
        # probe needs rendered fields, not a loading shim.
        def cb(result) -> None:
            state, _, count = (result or "").partition("|")
            ready = state == "complete" and int(count or "0") > 0
            if ready or tries >= 60:
                _finish(cmd_file, {"ok": ready, "ready": state, "fields": int(count or "0")})
                return
            QTimer.singleShot(500, lambda: poll_ready(tries + 1))

        window = _add_window()
        view = _biggest_webview(window) if window is not None else None
        if view is None:
            QTimer.singleShot(500, lambda: poll_ready(tries + 1))
            return
        view.page().runJavaScript(
            "document.readyState + '|' + document.querySelectorAll("
            "\"input, textarea, [contenteditable='true'], iframe\").length",
            cb,
        )

    poll_window(0)
    return None


def _widget_rect(window, widget) -> list[int]:
    origin = widget.mapTo(window, QPoint(0, 0))
    return [origin.x(), origin.y(), widget.width(), widget.height()]


def _qt_rects() -> dict:
    out: dict = {}
    bar = getattr(mw.form, "menubar", None)
    if bar is not None:
        out["menubar"] = _widget_rect(mw, bar)
        actions = []
        for act in bar.actions():
            if not act.text():
                continue
            geo = bar.actionGeometry(act)
            origin = bar.mapTo(mw, geo.topLeft())
            actions.append(
                {"text": act.text(), "rect": [origin.x(), origin.y(), geo.width(), geo.height()]}
            )
        out["menubar_actions"] = actions
    return out


def _visible_webviews(window) -> list:
    return [v for v in window.findChildren(AnkiWebView) if v.isVisible()]


def _probe(cmd_file: pathlib.Path, spec: dict) -> dict | None:
    target = spec.get("target", "main")
    window = _add_window() if target == "add" else mw
    if window is None:
        return {"ok": False, "error": "no Add window open"}

    payload: dict = {"ok": True, "target": target, "window": [window.width(), window.height()]}
    views: dict[str, list[int]] = {}
    if target == "main":
        payload["qt"] = _qt_rects()
        for key, attr in (("main", "web"), ("bottom", "bottomWeb")):
            view = getattr(mw, attr, None)
            if view is not None and view.isVisible():
                views[key] = _widget_rect(mw, view)
    else:
        candidates = _visible_webviews(window)
        if not candidates:
            return {"ok": False, "error": "no visible webview in the Add window"}
        biggest = max(candidates, key=lambda v: v.width() * v.height())
        views["add"] = _widget_rect(window, biggest)
        target_view = biggest

    payload["views"] = views

    js_specs: list[tuple[str, object]] = []
    if target == "add":
        js_specs.append(("add", spec.get("js")))
    else:
        js_specs.append(("main", spec.get("js")))
        js_specs.append(("bottom", spec.get("bottom_js")))
    js_specs = [(key, js) for key, js in js_specs if js]

    if not js_specs:
        _finish(cmd_file, payload)
        return None

    dom: dict[str, dict] = {}

    def run(key: str, js: str) -> None:
        view = target_view if key == "add" else getattr(mw, "web" if key == "main" else "bottomWeb")

        def callback(result) -> None:
            dom[key] = {"ok": True, "result": result}

        view.page().runJavaScript(js, callback)

    for key, js in js_specs:
        run(key, js)

    def flush() -> None:
        payload["dom"] = dom
        _finish(cmd_file, payload)

    def wait(tries: int = 0) -> None:
        if len(dom) == len(js_specs) or tries >= JS_TIMEOUT_POLLS:
            if len(dom) != len(js_specs):
                payload["js_timeout"] = sorted(k for k, _ in js_specs if k not in dom)
            flush()
            return
        QTimer.singleShot(100, lambda: wait(tries + 1))

    wait()
    return None


def _on_profile_open() -> None:
    global _poll_timer
    CTL_DIR.mkdir(parents=True, exist_ok=True)
    timer = QTimer()
    timer.timeout.connect(_poll)
    timer.start(POLL_MS)
    _poll_timer = timer
    _log(f"control channel polling {CTL_DIR}")


gui_hooks.profile_did_open.append(_on_profile_open)
_log(f"gate control loaded (ctl {CTL_DIR})")
