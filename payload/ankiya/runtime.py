"""The Anki-side runtime: apply the mapped palette, keep Anki live.

Port of the ticket 09 spike (branch ``prototype/09-live-switch``, pixel-exact
on this exact Anki build) onto the pure core from ticket 16. Delivery legs:

  Qt chrome    both polarity slots of ``aqt.colors`` get identical values
               (ticket 07's total reskin); night_mode follows the palette's
               own ``mode``; the private ``_apply_palette``/``_apply_style``
               are called directly because the public ``apply_style()``
               early-returns when polarity didn't change — same-polarity
               dark→dark switches must recolor too.
  stdHtml      ``web/ankiya.css`` regenerated in the add-on dir, read fresh
               from disk by every page build (``webview_will_set_content`` +
               ``setWebExports``).
  sveltekit    a profile-level QWebEngineScript — sveltekit pages never fire
               ``webview_will_set_content`` — installed by patching
               ``AnkiWebPage._inject_user_script``; a refresh is
               remove-the-inserted-object + insert-fresh, because Qt6 script
               collections have no ``update()``.
  open pages   ``theme_did_change`` flips body classes but never reloads
               content, so the engine script is also eval'd into every live
               AnkiWebView.

The watcher: ``QFileSystemWatcher`` on ``~/.local/state/omarchy/current`` —
a theme swap is rm+mv of ``current/theme`` (a new inode), so the parent dir
is the stable target — with a 150 ms debounce and a palette-digest guard.

A missing palette file at start means Omarchy is absent or below the 4.0.1+
floor (ticket 13): one log line, no watcher, Anki keeps its own theming.
Standalone mode (ticket 12: plugin removed, add-on left behind) works because
the palette is read from disk, nowhere else.
"""

from __future__ import annotations

import json
import pathlib
import time
import traceback

import aqt.colors as ak_colors
import aqt.webview as aqt_webview
from aqt import gui_hooks, mw
from aqt.qt import (
    QFileSystemWatcher,
    QTimer,
    QWebEngineProfile,
    QWebEngineScript,
    sip,
)
from aqt.theme import theme_manager
from aqt.webview import AnkiWebView

from ankiya.cssgen import css_text, engine_script
from ankiya.palette import Mapping, fingerprint, load_raw, map_palette

# Folder identity locked by ticket 11; the add-on is installed as
# addons21/ankiya, so module name, folder, and web-export key all coincide.
ADDON_PACKAGE = "ankiya"

STATE_DIR = pathlib.Path.home() / ".local/state/omarchy/current"
PALETTE_FILE = STATE_DIR / "theme" / "colors.toml"
THEME_NAME_FILE = STATE_DIR / "theme.name"
PLUGIN_STATE_DIR = pathlib.Path.home() / ".local/state/omarchy/anki-theme"
APPLIED_LOG = PLUGIN_STATE_DIR / "applied.jsonl"

WEB_DIR = pathlib.Path(__file__).parent / "web"
CSS_FILE = WEB_DIR / "ankiya.css"
ENGINE_SCRIPT_NAME = "ankiya_style"

DEBOUNCE_MS = 150
RETRY_MS = 200


def _log(message: str) -> None:
    print(f"[ankiya] {message}", flush=True)


class Runtime:
    """Owns the apply pipeline and the watcher that re-triggers it."""

    def __init__(self) -> None:
        self.config: dict = {}
        self.contrast_clamp = True
        self._digest: str | None = None
        self._seq = 0
        self._started = False
        self._hook_installed = False
        self._watcher: QFileSystemWatcher | None = None
        self._debounce: QTimer | None = None
        # (profile, script) pairs; script is None for profiles seen before the
        # first apply wrote the CSS — the next refresh scripts them.
        self._engine_scripts: list[tuple[QWebEngineProfile, QWebEngineScript | None]] = []

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """profile_did_open: wire everything up once, then apply."""
        if not PALETTE_FILE.exists():
            _log(
                f"no palette at {PALETTE_FILE} — Omarchy absent or below the "
                "4.0.1+ floor; Anki keeps its own theming"
            )
            return
        self._read_config()
        try:
            if not self._started:
                mw.addonManager.setWebExports(ADDON_PACKAGE, r"web/.*\.css")
                if not self._hook_installed:
                    gui_hooks.webview_will_set_content.append(self._on_will_set_content)
                    self._hook_installed = True
                self._started = True
                # The main window's webviews are created before add-ons load,
                # so the _inject_user_script patch never saw their profiles —
                # note aqt's two cached profiles directly; every later page
                # (sveltekit included) reuses one of them. Best-effort: the
                # patch covers anything created later.
                for profile in (
                    aqt_webview._profile_with_api_access,
                    aqt_webview._profile_without_api_access,
                ):
                    if profile is not None:
                        self.note_profile(profile)
            # Internal guard makes this a no-op once watching; a failed
            # addPath (e.g. inotify exhaustion) is retried here per open.
            self._install_watcher()
        except Exception:
            # Nothing may escape into the profile-open sequence; the wiring
            # above is idempotent, so the next profile open retries it.
            _log(f"startup wiring failed:\n{traceback.format_exc()}")
            return
        try:
            self.apply("startup")
        except Exception:
            _log(f"startup apply crashed — Anki keeps its own theming:\n{traceback.format_exc()}")

    def _read_config(self) -> None:
        self.config = mw.addonManager.getConfig(ADDON_PACKAGE) or {}
        clamp = self.config.get("contrast_clamp")
        self.contrast_clamp = True if clamp is None else bool(clamp)
        _log(f"config: contrast_clamp={self.contrast_clamp}")

    # -- the apply -----------------------------------------------------------

    def apply(self, reason: str) -> bool:
        """Re-read the palette and run every delivery leg. True if applied.

        The digest is only consumed on a completed apply (record written,
        errors included), so state never advances past an unrecorded failure.
        """
        t0 = time.perf_counter()
        text = PALETTE_FILE.read_text()
        palette, mode = load_raw(text)
        digest = fingerprint(palette, mode)
        if digest == self._digest:
            _log(f"{reason}: palette content unchanged — skip")
            return False

        errors: list[str] = []
        dark = mode == "dark"
        mapping = self._map(palette)
        css = css_text(mapping)
        views = 0

        # Leg 1 — Qt chrome (menubar, toolbar, dialogs) via Anki's own
        # pipeline. A failure here means the apply fundamentally failed:
        # abort unrecorded for the caller's retry rather than record a
        # half-truth.
        self._apply_qt_chrome(mapping, dark)
        # Legs 2–5 are web delivery: a failure in any of them must not abort
        # the others, the record, or Anki — it lands in the record instead.
        try:
            self._write_web_css(css)  # stdHtml page builds read this fresh
        except Exception:
            errors.append("web_css")
            _log(f"web css write failed:\n{traceback.format_exc()}")
        try:
            self._refresh_engine_scripts(css)  # sveltekit pages
        except Exception:
            errors.append("engine_scripts")
            _log(f"engine script refresh failed:\n{traceback.format_exc()}")
        try:
            gui_hooks.theme_did_change()
        except Exception:
            errors.append("theme_did_change")
            _log(f"theme_did_change hook failed:\n{traceback.format_exc()}")
        try:
            views = self._restyle_open_pages(css)
        except Exception:
            errors.append("open_pages")
            _log(f"open-page restyle failed:\n{traceback.format_exc()}")

        self._seq += 1
        self._digest = digest
        self._record_apply(
            {
                "seq": self._seq,
                "theme": self._theme_name(),
                "dark": dark,
                "reason": reason,
                "vars": len(mapping.vars),
                "skipped": len(mapping.skipped),
                "engine_profiles": len(self._engine_scripts),
                "views": views,
                "errors": errors,
                "apply_ms": round((time.perf_counter() - t0) * 1000, 1),
                "applied_at": time.time(),
            }
        )
        return True

    def _map(self, palette: dict[str, str]) -> Mapping:
        """The apply path's palette preparation.

        Ticket 18 wires the ``clamp_palette`` pre-pass here, gated on
        ``self.contrast_clamp``; until then palettes map verbatim.
        """
        return map_palette(palette)

    def _apply_qt_chrome(self, mapping: Mapping, dark: bool) -> None:
        mutated = 0
        for name, value in mapping.vars.items():
            entry = getattr(ak_colors, name, None)
            if not isinstance(entry, dict):
                # Tolerant skip (ticket 15): an unknown name never breaks the
                # apply; the startup drift check is the one that surfaces it.
                _log(f"aqt.colors has no slot {name} — skipped")
                continue
            entry["light"] = value
            entry["dark"] = value
            mutated += 1
        theme_manager.night_mode = dark
        theme_manager._apply_palette(mw.app)
        theme_manager._apply_style(mw.app)
        _log(f"qt chrome: {mutated} vars in both slots, night_mode={dark}")

    def _write_web_css(self, css: str) -> None:
        WEB_DIR.mkdir(exist_ok=True)
        tmp = CSS_FILE.with_suffix(".tmp")
        tmp.write_text(css)
        tmp.replace(CSS_FILE)

    # -- engine scripts (sveltekit pages) ------------------------------------

    def note_profile(self, profile: QWebEngineProfile) -> None:
        """A webview just got its user scripts — remember its profile.

        Inert until start() has run: the below-floor no-op (ticket 13) must
        never deliver a stale runtime-generated CSS. Tracking is otherwise
        unconditional — the shared profile usually exists before the first
        apply writes the CSS, so a profile seen early is recorded scriptless
        and picked up by the next apply's refresh.
        """
        if not self._started:
            return
        if any(p is profile for p, _ in self._engine_scripts):
            return
        if CSS_FILE.exists():
            self._upsert_engine_script(profile, CSS_FILE.read_text())
        else:
            self._engine_scripts.append((profile, None))

    def _upsert_engine_script(self, profile: QWebEngineProfile, css: str) -> None:
        self._drop_engine_scripts(profile)
        script = QWebEngineScript()
        script.setName(ENGINE_SCRIPT_NAME)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(engine_script(css))
        profile.scripts().insert(script)
        self._engine_scripts.append((profile, script))
        label = profile.storageName() or "off-the-record"
        _log(f"engine script upserted on profile {label!r}")

    def _prune_dead_profiles(self) -> None:
        # QWebEngineScript is a value type copied into the collection, so only
        # the profile half of an entry can die.
        for entry in self._engine_scripts[:]:
            profile, _script = entry
            if sip.isdeleted(profile):
                self._engine_scripts.remove(entry)

    def _drop_engine_scripts(self, profile: QWebEngineProfile) -> None:
        # Dead entries first, then exactly the objects we inserted for this
        # profile — remove-by-copy is unreliable in Qt6 script collections.
        self._prune_dead_profiles()
        for entry in self._engine_scripts[:]:
            p, script = entry
            if p is profile:
                if script is not None:
                    profile.scripts().remove(script)
                self._engine_scripts.remove(entry)

    def _refresh_engine_scripts(self, css: str) -> None:
        self._prune_dead_profiles()
        for profile, _script in self._engine_scripts[:]:
            self._upsert_engine_script(profile, css)

    # -- open pages ----------------------------------------------------------

    def _restyle_open_pages(self, css: str) -> int:
        js = engine_script(css)
        restyled = 0
        for top in mw.app.topLevelWidgets():
            for view in top.findChildren(AnkiWebView):
                if sip.isdeleted(view):
                    continue
                view.eval(js)
                restyled += 1
        _log(f"restyled {restyled} open webviews")
        return restyled

    # -- stdHtml pages -------------------------------------------------------

    def _on_will_set_content(self, web_content, context, *args) -> None:
        web_content.css.append(f"/_addons/{ADDON_PACKAGE}/web/ankiya.css")

    # -- the watcher ---------------------------------------------------------

    def _install_watcher(self) -> None:
        if self._watcher is not None:
            return
        watcher = QFileSystemWatcher()
        if not watcher.addPath(str(STATE_DIR)):
            _log(f"cannot watch {STATE_DIR} — live switching disabled, startup palette stands")
            return
        self._watcher = watcher
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._on_debounce)
        _log(f"watching {STATE_DIR}")

    def _on_dir_changed(self, _path: str) -> None:
        assert self._watcher is not None and self._debounce is not None
        # QFileSystemWatcher silently forgets paths it watched if they ever
        # vanish; re-add defensively before re-arming the debounce.
        if str(STATE_DIR) not in self._watcher.directories():
            _log("state dir dropped from watcher — re-adding")
            self._watcher.addPath(str(STATE_DIR))
        self._debounce.start(DEBOUNCE_MS)

    def _on_debounce(self) -> None:
        try:
            self.apply("watcher")
        except Exception:
            _log(f"watcher apply crashed:\n{traceback.format_exc()}")
            # The palette read can race the swap's rm→mv window; one retry
            # after it settles (the mv fires a fresh event anyway).
            QTimer.singleShot(RETRY_MS, self._retry_apply)

    def _retry_apply(self) -> None:
        try:
            self.apply("watcher-retry")
        except Exception:
            _log(f"watcher retry crashed:\n{traceback.format_exc()}")

    # -- observability -------------------------------------------------------

    @staticmethod
    def _theme_name() -> str:
        try:
            return THEME_NAME_FILE.read_text().strip()
        except OSError:
            return ""

    def _record_apply(self, record: dict) -> None:
        _log(
            f"applied seq={record['seq']} theme={record['theme']!r} "
            f"dark={record['dark']} reason={record['reason']} "
            f"vars={record['vars']} views={record['views']} "
            f"apply_ms={record['apply_ms']}"
        )
        try:
            PLUGIN_STATE_DIR.mkdir(parents=True, exist_ok=True)
            with APPLIED_LOG.open("a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            # Observability must never break theming.
            _log("could not append the applied record")


runtime = Runtime()


def _patched_inject_user_script(
    self: aqt_webview.AnkiWebPage, profile: QWebEngineProfile, script: QWebEngineScript
) -> None:
    # Install the engine script on every profile Anki creates. The original
    # call stays untouched; only our addition is wrapped, so a theming add-on
    # can never break page loads.
    _ORIG_INJECT_USER_SCRIPT(self, profile, script)
    try:
        runtime.note_profile(profile)
    except Exception:
        _log(f"engine script insert failed:\n{traceback.format_exc()}")


_ORIG_INJECT_USER_SCRIPT = aqt_webview.AnkiWebPage._inject_user_script
aqt_webview.AnkiWebPage._inject_user_script = _patched_inject_user_script
