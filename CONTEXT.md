# omarchy-anki-theme

Anki recolored live in the active Omarchy palette, delivered as an Omarchy plugin
with a bundled Anki add-on. This glossary is the project's shared language; the
wayfinder map (`.scratch/anki-theme/map.md`) holds the plan, not the vocabulary.

## Language

### The two halves

**Plugin**:
The Omarchy shell plugin (Quickshell `service`, QML/JS) — the delivery half that
installs and updates the add-on payload with the user's consent.
_Avoid_: extension, theme package

**Add-on**:
The Anki add-on (Python, in-process) — the live half that watches for theme
changes and applies the palette inside Anki.
_Avoid_: plugin (when inside Anki), extension

### Update propagation

**Payload**:
The add-on's file tree as bundled inside the plugin — the single source of truth
the installed copy under `addons21/anki_theme/` is made from.
_Avoid_: bundle, add-on package (when meaning the bundled tree)

**Stamp**:
The installed copy's identity and freshness record: `payload.json` inside
`anki_theme/`, shipping identity fields and carrying the `payloadHash` (content hash
of the bundled payload tree) written at install or swap time.
_Avoid_: version file, version stamp

**Sync**:
The one routine (`anki_theme/sync.py`, stdlib-only, pure functions over paths) that
installs, updates, and verifies the installed copy against the bundled payload.
The only writer to `anki_theme/`; driven by fresh code only (service mount, add-on
startup).
_Avoid_: updater, installer (sync is both)

**Swap**:
Replacing the installed copy by staging a fresh tree as a dot-prefixed sibling
outside `addons21` and double-renaming it into place, preserving `meta.json`.
_Avoid_: in-place update, overwrite

**Bootloader**:
The shape of `anki_theme/__init__.py`: run sync first, then hand off to
`runtime.start()` — so a payload update can land at Anki startup without a
shell restart.
_Avoid_: loader, self-update shim

**Standalone mode**:
An installed add-on whose plugin is gone: it keeps theming (it is self-sufficient
for recoloring) but receives no updates and sends no notifications, except the
in-Anki drift notice (ticket 15).
_Avoid_: orphaned add-on, degraded mode

### Upgrades

**Snapshot**:
The vendored name-set of the installed Anki's `aqt.colors` vars the mapping
was built against (`anki_theme/var_snapshot.txt`, shipped in the payload).
Tier 1's tripwire asserts the mapping covers it; regenerated via
`scripts/regen_var_snapshot.py` on every Anki upgrade.
_Avoid_: baseline file, golden file

**Drift**:
The startup verdict on Anki's color-var inventory versus the Snapshot —
name-set only, values never. Retract-class (the live inventory lost covered
names, or is unreadable) logs and shows one transient in-Anki tooltip per
signature (the sorted retract set — the state-dir marker dedups on it);
add-class (new names) logs only. Never gates theming.
_Avoid_: variance, mismatch (when meaning the verdict)

### The service half

**Gate**:
The service's one decision pass (`service/gate.py`) at every service start —
version floor, then Anki detection, then consent, then deleted-in-Anki, else
mount Sync — emitted as one JSON line (with the acting outcome's complete
`exec` argv) that the QML relays.
_Avoid_: startup check, preflight

**Grant**:
The consent dialog's Allow action (`service/grant.py`): record Consent, then
mount Sync as a subprocess. An interrupted Allow is safe — Consent lands
atomically before any install, and the next service start's Gate completes
whatever the interruption left.
_Avoid_: installer callback, consent handler

**Consent**:
The recorded permission to install and keep updated the add-on
(`consent.json` in the plugin state dir, written only by Grant): asked once
per service start while unanswered, and only when Anki's data dir exists.
Before it, the service writes nothing at all.
_Avoid_: opt-in, permission (when meaning the record)

### Theming

**Palette**:
The color set of an Omarchy theme, rendered from its `colors.toml` — the thing
applied to Anki. The canonical live source is
`~/.local/state/omarchy/current/theme/colors.toml`.
_Avoid_: color scheme, skin, theme (a theme is more than its palette)

**Theme**:
A named stock or user-defined appearance configuration in Omarchy, of which the
palette is the color part. Switching themes rewrites the palette on disk.
_Avoid_: skin

**Full surface**:
Every user-visible Anki surface — webview screens (deck browser, reviewer,
editor, stats) and Qt chrome (menus, dialogs, tables) — following the palette.
_Avoid_: complete theming, total theme

**Applier**:
The add-on component that maps a palette onto Anki's color variables and pushes
it through Anki's own theme pipeline.
_Avoid_: theme engine, injector, styler

**Clamp**:
The normalize-then-map pre-pass (`clamp_palette`) that adjusts a palette's
foreground-side colors when a guarded relationship falls below its floor.
Pure function of the palette keys; the mapping runs after it, amended only
where ticket 08 records (the on-tint candidate extension).
_Avoid_: fix-up, correction

**Guarded relationship**:
A palette-color pairing (e.g. `foreground` on `background`) whose contrast the
clamp enforces. Everything not listed as guarded is verbatim by policy.
_Avoid_: check, rule (when meaning the pairing)

**Floor**:
The minimum WCAG contrast ratio a guarded relationship must meet — chosen as
the highest threshold no stock palette violates, so stock themes never trigger
a clamp.
_Avoid_: threshold, limit

**Faithful mode**:
The add-on config `contrast_clamp = false`: guarded relationships render
verbatim, no clamping.
_Avoid_: raw mode, passthrough mode

### Switching

**Polarity**:
Whether the active theme is light or dark (`mode` in colors.toml). The only
theme fact the desktop portal signals.
_Avoid_: dark mode, color scheme

**Same-polarity switch**:
A theme change that keeps polarity (e.g. dark→dark palette change) — invisible
to the portal signal and ignored by Anki's native `apply_style`; the case this
project exists to catch.
_Avoid_: palette-only switch

**Live switch**:
A theme change reflected in running Anki without restart — the switch-to-reapply
latency metric in `docs/performance.md` measures it.
_Avoid_: hot reload (that is plugin dev reload), dynamic theme
