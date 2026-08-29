# omarchy-anki-theme (provisional name)

Anki decked out in whatever Omarchy theme is active — live, no restart, full surface.
Ships as an Omarchy plugin (marketplace listing planned); MIT throughout.

Status: wayfinder map charted 2026-08-29 — the plan and every decision live in
[`.scratch/anki-theme/map.md`](.scratch/anki-theme/map.md) and its tickets; research
findings under `.scratch/anki-theme/research/`. This README is the internal dev face;
it gets restructured for the public marketplace listing late in the map.

## Architecture

Corrected 2026-08-29 by map research — supersedes the original sketch. The live-switch
machinery is **add-on-side**: Omarchy's theme switch rewrites state on disk and signals
nothing (the xdg portal signal carries light/dark polarity only), so the add-on watches
the state directory itself.

```
omarchy-theme-set (any trigger: manual, theme-scheduler, …)
        │
        └─▶ rm+mv swap of ~/.local/state/omarchy/current/theme/
                    │
                    ├─▶ Anki add-on (Python, in-process)
                    │       QFileSystemWatcher on that dir  ← palette changes, instant
                    │       QtDBus portal SettingChanged    ← light/dark polarity
                    │       Anki's own 5-min poll           ← last-resort fallback
                    │       │
                    │       reads colors.toml directly (tomllib), maps its 26 keys
                    │       onto Anki's 51 CSS color vars (aqt/colors.py mirror)
                    │       re-applies via aqt.colors + theme_manager._apply_palette/
                    │       _apply_style + gui_hooks.theme_did_change
                    │
                    └─▶ Omarchy plugin (QML/JS, `service` kind)
                            consent-gated copy of the bundled add-on payload into
                            ~/.local/share/Anki2/addons21/ — the installer never
                            runs plugin code, so the runtime does the install
```

- Plugin-emitted D-Bus is the **fallback** only if the spike (map ticket 09) disproves
  file watching — the dir swap is rm+mv (new inode), which is exactly what the spike
  must survive.
- Same-polarity switches (dark→dark palette change) are first-class: Anki's
  `apply_style()` early-returns on them, so the add-on drives the internal appliers
  directly.
- Webview styling: CSS variable overrides scoped on `body` (`:root` loses — stdHtml
  inlines `:root{…}` after webview.css); `webview_will_set_content` covers stdHtml
  pages, profile-level `QWebEngineScript` covers the sveltekit pages that bypass it
  (editor, deck options, stats).

## Stack

- **Omarchy plugin** — QML/JavaScript on the Quickshell `service` kind; `manifest.json`
  (schemaVersion 1, six kinds, entry points relative, no symlinks). No compiled
  binaries (marketplace security baseline), no install hooks — a runtime Process/FileView
  does the payload copy.
- **Anki add-on** — Python against Anki's own machinery, stdlib + aqt/Qt6 only:
  `tomllib`, `QFileSystemWatcher`, QtDBus. Zero third-party runtime dependencies.
- **License** — MIT for everything. ReColor (AGPL) is names-only prior art;
  catppuccin/anki (MIT) is reference palette data.

## Machine facts this design relies on

- Anki 26.08.1 from Arch extra (system Qt6), `/usr/bin/anki`, web assets in
  `/usr/lib/python3.14/site-packages/_aqt/data/web/`, data at `~/.local/share/Anki2/`
- Anki "Follow system" reads the freedesktop portal; Omarchy flips
  `org.gnome.desktop.interface color-scheme` on every theme apply
- Omarchy plugins: `~/.config/omarchy/plugins/`, hot-reloaded via inotify
  (150 ms debounce, full rescan); dev installs from local paths / `file://` work
- colors.toml: 26 universal keys across all 22 stock themes (mode, fg/bg 4-step
  ramps, accent, muted, selection, 8 base + 6 bright hues) + rare per-theme extras

## Reference prior art

- seara (`rodrada/seara`, MIT) — in-Anki D-Bus listener pattern; dispatches to the
  GUI thread via `mw.taskman.run_on_main`
- aronnaxlin/omarchy-anki-status-plugin — bundled helper + real collection access
- yamz8/omanki — plugin manifest/test craft, safe writes to user dirs
- acrogenesis.theme-scheduler (installed here) — theme-aware plugin precedent
- ReColor (AnKing-VIP/AnkiRecolor, AGPL) — variable names/config schema only, never code
- catppuccin/anki (MIT) — ReColor config JSONs as palette mapping reference
