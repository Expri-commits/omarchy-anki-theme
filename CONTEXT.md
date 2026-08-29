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
