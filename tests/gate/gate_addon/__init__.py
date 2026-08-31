"""Dev-only gate control add-on — the tiers' control channel (tickets 22/23).

Never shipped: the harness copies this folder into the scratch base's
``addons21/zz_gate_control`` (sorted after ``anki_theme``, so it loads once the
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
  open_stats   open the DeckStats dialog (sveltekit graphs) and keep it open
  close_stats  close it
  open_prefs   open the Preferences dialog and keep it open
  close_prefs  close it
  open_menu    popup one menubar menu non-blocking, optionally with a
               pre-highlighted action (the QSS ``:item:selected`` render)
  close_menu   close the popup
  set_clamp    flip the anki_theme ``contrast_clamp`` config through the real
               writeConfig + configUpdatedAction path the config dialog uses
  probe        report widget/webview rects (and DOM probe results from the
               versioned sample map's JS) for a target surface — everything
               the pytest side needs to map sample points onto screenshots

Every exception lands in the done file instead of crashing Anki; nothing here
writes outside the scratch session dir (GATE_CTL_DIR, set by the harness).
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import traceback

import anki.buildinfo
from aqt import dialogs, gui_hooks, mw
from aqt.qt import (
    QApplication,
    QBuffer,
    QEvent,
    QMenu,
    QMouseEvent,
    QPoint,
    QPointF,
    Qt,
    QTabWidget,
    QTimer,
    QWidget,
)
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

# Dialog and popup kept open across commands, by kind.
_dialogs: dict[str, QWidget] = {}
_menu: QMenu | None = None


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
        except (
            OSError,
            ValueError,
        ):
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
    if name == "open_stats":
        return _open_dialog(cmd_file, "stats", "DeckStats")
    if name == "close_stats":
        return _close_dialog("stats")
    if name == "open_prefs":
        return _open_dialog(cmd_file, "prefs", "Preferences")
    if name == "close_prefs":
        return _close_dialog("prefs")
    if name == "open_menu":
        return _open_menu(cmd_file, spec)
    if name == "close_menu":
        return _close_menu()
    if name == "set_clamp":
        return _set_clamp(spec)
    if name == "qss":
        return _qss()
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
        template["afmt"] = '<div class="card">{{Front}}</div><hr id="answer">{{Back}}'
        model["css"] = f".card {{\n  font-family: arial;\n  background-color: {CARD_FACE_BG};\n}}\n"
        mw.col.models.save(model)
    model = mw.col.models.by_name("Gate Basic")
    note = mw.col.new_note(model)
    note["Front"] = "Gate card face"
    note["Back"] = "Gate back"
    mw.col.add_note(note, deck_id)
    # Answer it once (Good): the "Added" stats graph needs a bar for today.
    # 26.08.1's stats are the legacy flot page; its Added graph draws today's
    # additions as colLearn = STATE_NEW (theme_manager._update_stat_colors,
    # fed by the mapped aqt.colors) with flot's fill=0.7 over the canvas.
    answered = False
    mw.col.decks.select(deck_id)
    card = mw.col.sched.getCard()
    if card is not None:
        mw.col.sched.answerCard(card, 3)  # GOOD: new → learning
        answered = True
    _log(f"seeded deck {deck_id}, note {note.id}, answered={answered}")
    return {
        "ok": True,
        "deck_id": deck_id,
        "note_id": note.id,
        "card_face_bg": CARD_FACE_BG,
        "answered": answered,
    }


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


def _poll_web_ready(view, js: str, tries: int, on_done) -> None:
    """Poll one webview until its ready-js returns ``complete|<n>`` with n>0
    (the window appears long before sveltekit hydrates; the callers need the
    rendered page, not the intent to render one)."""

    def cb(result) -> None:
        state, _, count = (result or "").partition("|")
        ready = state == "complete" and int(count or "0") > 0
        if ready or tries >= 60:
            on_done(ready, state, int(count or "0"))
            return
        QTimer.singleShot(500, lambda: _poll_web_ready(view, js, tries + 1, on_done))

    view.page().runJavaScript(js, cb)


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
        view = _biggest_webview(window)
        if view is None:
            QTimer.singleShot(500, lambda: poll_window(tries + 1))
            return

        def on_done(ready: bool, state: str, count: int) -> None:
            _finish(cmd_file, {"ok": ready, "ready": state, "fields": count})

        _poll_web_ready(
            view,
            "document.readyState + '|' + document.querySelectorAll("
            "\"input, textarea, [contenteditable='true'], iframe\").length",
            0,
            on_done,
        )

    poll_window(0)
    return None


# -- dialog surfaces (stats, prefs) ------------------------------------------


def _open_dialog(cmd_file: pathlib.Path, kind: str, dialog_name: str) -> None:
    """Open a registered aqt dialog and wait for its web view to hydrate.

    ``aqt.dialogs.open`` returns the live dialog (opening it if needed); the
    gate keeps the reference for probe/close and raises it so its pixels sit
    on top for the harness's window capture. The ready-js is per kind: the
    graphs page completes long before svelte renders its charts, so stats
    waits for actual ``<svg>`` elements, not just a loaded document."""

    def poll_window(tries: int) -> None:
        dialog = _dialogs.get(kind)
        if kind == "prefs":
            # 26.08.1's Preferences is the NATIVE Qt dialog (radio buttons,
            # tabs); its sveltekit page hides on a non-current Labs tab whose
            # webview never becomes visible. Ready = the window is on screen.
            if dialog is None or not dialog.isVisible():
                if tries < 40:
                    QTimer.singleShot(250, lambda: poll_window(tries + 1))
                else:
                    _finish(
                        cmd_file,
                        {"ok": False, "error": f"{dialog_name} never appeared"},
                    )
                return
            dialog.activateWindow()
            dialog.raise_()
            QTimer.singleShot(
                SETTLE_MS,
                lambda: _finish(
                    cmd_file,
                    {"ok": True, "title": dialog.windowTitle(), "native": True},
                ),
            )
            return

        view = _biggest_webview(dialog) if dialog is not None else None
        if view is None:
            if tries < 40:
                QTimer.singleShot(250, lambda: poll_window(tries + 1))
            else:
                views = (
                    [
                        f"{v.__class__.__name__} visible={v.isVisible()} "
                        f"size={v.width()}x{v.height()}"
                        for v in dialog.findChildren(AnkiWebView)
                    ]
                    if dialog is not None
                    else ["no dialog instance"]
                )
                _finish(
                    cmd_file,
                    {
                        "ok": False,
                        "error": f"{dialog_name} never appeared",
                        "dialog": type(dialog).__name__ if dialog is not None else None,
                        "dialog_state": (
                            {
                                "visible": dialog.isVisible(),
                                "minimized": bool(
                                    dialog.windowState() & dialog.windowState().WindowMinimized
                                ),
                                "size": f"{dialog.width()}x{dialog.height()}",
                                "title": dialog.windowTitle(),
                            }
                            if dialog is not None
                            else None
                        ),
                        "webviews": views,
                    },
                )
            return

        if kind == "stats":
            ready_js = "document.readyState + '|' + document.querySelectorAll('svg, canvas').length"
        else:
            ready_js = "document.readyState + '|' + document.body.children.length"

        def on_done(ready: bool, state: str, count: int) -> None:
            dialog.activateWindow()
            dialog.raise_()
            QTimer.singleShot(
                SETTLE_MS,
                lambda: _finish(
                    cmd_file,
                    {
                        "ok": ready,
                        "ready": state,
                        "elements": count,
                        "title": dialog.windowTitle(),
                    },
                ),
            )

        _poll_web_ready(view, ready_js, 0, on_done)

    _dialogs[kind] = dialogs.open(dialog_name, mw)
    poll_window(0)
    return None


def _close_dialog(kind: str) -> dict:
    dialog = _dialogs.pop(kind, None)
    if dialog is None:
        return {"ok": True, "closed": False}
    dialog.close()
    return {"ok": True, "closed": True}


# -- the menu popup ----------------------------------------------------------


def _qss() -> dict:
    """The effective Qt chrome facts: what the app stylesheet tells QMenu to
    paint, the widget style, and the palette window color (characterization
    aid for popup surfaces)."""
    from aqt.theme import theme_manager

    sheet = mw.app.styleSheet()
    menu_rules = [
        line.strip() for line in sheet.splitlines() if "QMenu" in line or "background-color" in line
    ]
    window = mw.app.palette().color(mw.app.palette().ColorRole.Window)
    return {
        "ok": True,
        "widget_style": str(mw.pm.get_widget_style()),
        "night_mode": theme_manager.night_mode,
        "palette_window": [window.red(), window.green(), window.blue()],
        "menu_rules": menu_rules[:40],
        "sheet_len": len(sheet),
    }


def _menu_payload() -> dict:
    menu = _menu
    assert menu is not None
    top_left = menu.mapToGlobal(QPoint(0, 0))
    # The popup's own render buffer (QSS + palette through Qt's paint engine).
    # Wayland clients cannot know a popup's real compositor position
    # (mapToGlobal is a guess), and the compositor dims the window behind an
    # open popup — so popup surfaces are sampled from this buffer, while
    # window surfaces stay proven through grim screen captures.
    grab = menu.grab().toImage()
    actions = []
    active_index = -1
    active = menu.activeAction()
    for _index, act in enumerate(menu.actions()):
        if not act.text():
            continue
        geo = menu.actionGeometry(act)
        actions.append({"text": act.text(), "rect": [geo.x(), geo.y(), geo.width(), geo.height()]})
        if act is active:
            active_index = len(actions) - 1
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    grab.save(buffer, "PNG")
    return {
        "rect": [top_left.x(), top_left.y(), menu.width(), menu.height()],
        "grab_png": base64.b64encode(buffer.data().data()).decode(),
        "actions": actions,
        "active": active_index,
    }


def _open_menu(cmd_file: pathlib.Path, spec: dict) -> None:
    """Popup one menubar menu non-blocking (the popup must outlive this
    command so the harness can grim its global geometry), optionally with an
    action pre-highlighted — the QSS ``QMenu::item:selected`` render."""
    global _menu

    wanted = spec.get("menu", "File")
    menu = None
    for act in mw.form.menubar.actions():
        if act.text().replace("&", "") == wanted:
            menu = act.menu()
            break
    if menu is None:
        _finish(cmd_file, {"ok": False, "error": f"no menubar menu {wanted!r}"})
        return None

    active = None
    highlight = spec.get("highlight")
    if highlight:
        for act in menu.actions():
            if act.text().replace("&", "") == highlight:
                active = act
                break
        if active is None:
            _finish(cmd_file, {"ok": False, "error": f"no menu action {highlight!r}"})
            return None

    bar = mw.form.menubar
    geo = bar.actionGeometry(menu.menuAction())
    menu.popup(bar.mapToGlobal(geo.bottomLeft()))
    _menu = menu
    if active is not None:
        # setActiveAction alone does not render the highlight on a Wayland
        # popup (no real mouse ever moves over it) — drive QMenu's own
        # mouse-move handling with a synthetic event at the action's center.
        # The popup's late show can reset the active action after it stuck,
        # so a guard keeps re-driving whenever it is lost, right up to the
        # report moment (bounded well inside SETTLE_MS).
        def _ensure_hover(tries: int) -> None:
            if _menu is not menu:
                return  # closed/replaced — stop
            if menu.activeAction() is not active:
                menu.setActiveAction(active)
                act_geo = menu.actionGeometry(active)
                local = QPointF(act_geo.center())
                global_ = QPointF(menu.mapToGlobal(act_geo.center()))
                event = QMouseEvent(
                    QEvent.Type.MouseMove,
                    local,
                    global_,
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                QApplication.sendEvent(menu, event)
            if tries < 8:
                QTimer.singleShot(70, lambda: _ensure_hover(tries + 1))

        _ensure_hover(0)

    def report() -> None:
        _finish(cmd_file, {"ok": True, "menu": _menu_payload()})

    QTimer.singleShot(SETTLE_MS, report)
    return None


def _close_menu() -> dict:
    global _menu
    if _menu is not None:
        _menu.close()
        _menu = None
    return {"ok": True}


# -- the clamp config flip -----------------------------------------------------


def _set_clamp(spec: dict) -> dict:
    """Flip ``contrast_clamp`` exactly the way the add-on config dialog does:
    ``writeConfig``, then fire the registered updated action with the new
    conf (aqt fires it only from the dialog's accept()). The runtime's
    re-apply runs synchronously inside the action, so the reply follows the
    new applied record."""
    enabled = bool(spec.get("enabled", True))
    mgr = mw.addonManager
    conf = mgr.getConfig("anki_theme") or {}
    if bool(conf.get("contrast_clamp", True)) == enabled:
        return {"ok": True, "changed": False, "contrast_clamp": enabled}
    conf["contrast_clamp"] = enabled
    mgr.writeConfig("anki_theme", conf)
    action = mgr.configUpdatedAction("anki_theme")
    if action is None:
        return {"ok": False, "error": "anki_theme registered no configUpdatedAction"}
    action(conf)
    return {"ok": True, "changed": True, "contrast_clamp": enabled}


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


def _prefs_qt_rects(dialog) -> dict:
    """Qt-widget geometry for the native Preferences dialog (dialog-local):
    the whole content area, the tab widget with its current tab, and the
    bottom button row — the sample map's prefs points anchor on these."""
    out: dict = {"dialog": [0, 0, dialog.width(), dialog.height()]}
    tabs = dialog.findChild(QTabWidget)
    if tabs is not None:
        out["tabs"] = _widget_rect(dialog, tabs)
        out["tabs_current"] = tabs.tabText(tabs.currentIndex())
    button_box = dialog.form.buttonBox if hasattr(dialog, "form") else None
    if button_box is not None:
        out["buttons"] = _widget_rect(dialog, button_box)
    return out


def _probe(cmd_file: pathlib.Path, spec: dict) -> dict | None:
    target = spec.get("target", "main")
    payload: dict = {"ok": True, "target": target}

    if target == "menu":
        if _menu is None:
            return {"ok": False, "error": "no menu popup is open"}
        payload["menu"] = _menu_payload()
        # The harness captures exactly this region; window size = menu size
        # makes the shot-offset math degenerate (no chrome, no borders).
        payload["window"] = list(payload["menu"]["rect"][2:])
        _finish(cmd_file, payload)
        return None

    if target in ("stats", "prefs"):
        dialog = _dialogs.get(target)
        if dialog is None:
            return {"ok": False, "error": f"{target} dialog is not open"}
        dialog.activateWindow()
        dialog.raise_()
        payload["window"] = [dialog.width(), dialog.height()]
        origin = dialog.mapToGlobal(dialog.rect().topLeft())
        # The harness grim-captures exactly this global region.
        payload["geometry"] = [origin.x(), origin.y(), dialog.width(), dialog.height()]
        if target == "prefs":
            # The native dialog: Qt-widget rects, no webview to probe —
            # 26.08.1's preferences page is QTabWidget + group boxes (the
            # sveltekit webview sits on a non-current Labs tab).
            payload["qt"] = _prefs_qt_rects(dialog)
        else:
            candidates = _visible_webviews(dialog)
            if not candidates:
                return {"ok": False, "error": f"no visible webview in the {target} dialog"}
            biggest = max(candidates, key=lambda v: v.width() * v.height())
            payload["views"] = {target: _widget_rect(dialog, biggest)}
    elif target == "add":
        window = _add_window()
        if window is None:
            return {"ok": False, "error": "no Add window open"}
        payload["window"] = [window.width(), window.height()]
        candidates = _visible_webviews(window)
        if not candidates:
            return {"ok": False, "error": "no visible webview in the Add window"}
        biggest = max(candidates, key=lambda v: v.width() * v.height())
        payload["views"] = {"add": _widget_rect(window, biggest)}
    else:
        target = "main"
        payload["window"] = [mw.width(), mw.height()]
        payload["qt"] = _qt_rects()
        views: dict[str, list[int]] = {}
        for key, attr in (("main", "web"), ("bottom", "bottomWeb")):
            view = getattr(mw, attr, None)
            if view is not None and view.isVisible():
                views[key] = _widget_rect(mw, view)
        payload["views"] = views

    js_specs: list[tuple[str, object]] = []
    if target == "main":
        js_specs.append(("main", spec.get("js")))
        js_specs.append(("bottom", spec.get("bottom_js")))
    else:
        js_specs.append((target, spec.get("js")))
    js_specs = [(key, js) for key, js in js_specs if js]

    if not js_specs:
        _finish(cmd_file, payload)
        return None

    # Optional pre-action: run one script (e.g. scroll a graph into view),
    # let the page settle, and only then measure — the dump must see the
    # post-action layout, not the intent.
    pre_js = spec.get("pre_js")
    settle_ms = int(spec.get("settle_ms", 0))

    dom: dict[str, dict] = {}

    def view_for(key: str):
        if key == "main":
            return mw.web
        if key == "bottom":
            return mw.bottomWeb
        window = _add_window() if target == "add" else _dialogs[target]
        return max(_visible_webviews(window), key=lambda v: v.width() * v.height())

    def run(key: str, js: str) -> None:
        view = view_for(key)

        def callback(result) -> None:
            dom[key] = {"ok": True, "result": result}

        view.page().runJavaScript(js, callback)

    def start_dump() -> None:
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

    if pre_js:
        view_for(js_specs[0][0]).page().runJavaScript(pre_js)
        QTimer.singleShot(settle_ms, start_dump)
        QTimer.singleShot(settle_ms + JS_TIMEOUT_POLLS * 100, lambda: wait(0))
        return None

    start_dump()
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
