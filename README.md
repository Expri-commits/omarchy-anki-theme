# Ankiya

Anki, decked out in whatever Omarchy theme is active — live, full surface, no restart.

*Ankiya* = *anki* (暗記, "memorization" — where Anki gets its name) + the shop
suffix *-ya* (屋). Theming today; the shop stays open for whatever else Anki
needs from Omarchy.

<!-- flip-time: re-shoot both screenshot pairs with the shipped plugin, drop
     preview.png at the repo root, and uncomment every flip-time: block in this
     file (wayfinder ticket 10, decision 5) — see the flip checklist on the ticket.
![Anki following an Omarchy theme switch](preview.png)
-->

Every Omarchy theme switch — from the menu, the CLI, or a theme scheduler — recolors
all of Anki: deck browser, reviewer, editor, stats, dialogs, menus, tables. Switch
from Catppuccin Latte to Gruvbox and a running Anki follows along instantly.

## What you get

- **Live switching.** No restart; the recolor typically lands before the theme
  command even returns.
- **Same-polarity switches are first-class.** Dark→dark palette changes (e.g.
  Catppuccin → Gruvbox) are invisible to Anki's native theme handling — they work
  here like any other switch.
- **Every theme works.** All 22 stock Omarchy themes and user-made themes alike:
  the palette is rendered from Omarchy's own `colors.toml`, so there is no
  per-theme configuration.
- **Legibility guard.** Palettes that would drop text below its WCAG-derived
  contrast floor on key surfaces are nudged back toward readable before mapping.
  Prefer the palette exactly as authored? Set `contrast_clamp = false`
  (faithful mode).
- **No network, no third-party dependencies.** Stdlib-only Python inside Anki,
  QML/JS in the shell. MIT licensed.

<!-- flip-time: re-shot screenshots land here — uncomment this whole block
     (heading included) once the images exist. The polarity-flip pair doubles
     as the marketplace preview.png (wayfinder ticket 10, decision 5).
## Screenshots

![Deck browser before and after a light→dark theme switch](docs/img/polarity-flip.png)
![Even a dark→dark switch recolors live](docs/img/same-polarity.png)
-->

## How it works

Two halves, one feature:

```
omarchy theme set (any trigger: menu, CLI, scheduler)
        │
        └─▶ rewrites ~/.local/state/omarchy/current/theme/
                    │
                    └─▶ Anki add-on (Python, in-process)
                            watches that directory, reads colors.toml,
                            maps its palette onto Anki's color variables,
                            re-applies through Anki's own theme pipeline
                            — in ~13–15 ms, live
```

The Omarchy plugin (Quickshell `service`) is the delivery half: with your consent
it installs the bundled add-on into Anki and keeps it updated.

## Requirements

- Omarchy 4.0.1 or newer. Third-party plugin installs and the theme state
  layout the add-on watches arrived in 4.0.0; 4.0.1 settled the notification
  click-action that the consent prompt relies on. On an older Omarchy the
  plugin stays inert and Anki keeps its own theming.
- Anki 26.08 or newer (Qt6), with its profile directory at the default
  `~/.local/share/Anki2` (true for native installs such as the Arch package).

## Install

```bash
omarchy plugin add https://github.com/Expri-commits/omarchy-anki-theme
```

When Anki is present, the plugin asks for consent on first run with an Omarchy
notification: click it to allow the bundled add-on to be installed into Anki's
add-on folder (`~/.local/share/Anki2/addons21/ankiya`). Nothing touches your
Anki configuration before you say yes — not clicking leaves Anki untouched. Start
Anki (or restart it, if it's already running) and it follows your theme.

Updates flow in on their own: when the plugin updates, the installed add-on is
brought up to date at the next shell restart or Anki start — whichever comes
first. The same consent covers it, your Anki configuration stays untouched, and
an add-on whose payload didn't change isn't rewritten at all. Deleted the add-on
in Anki but kept the plugin? A notification offers to reinstall it; ignore it
and Anki stays as it is.

## Remove

Two halves, two steps:

1. Remove the plugin: `omarchy plugin remove io.github.expri-commits.anki-theme`
2. Remove the add-on from Anki: **Tools → Add-ons → Ankiya → Delete**

Removing only the plugin leaves the add-on running inside Anki — it keeps
theming, it just stops receiving updates. Step 2 is what turns the colors off.

Restart Anki and it returns to its own theming — a running Anki keeps the
colors already applied until it restarts. Optionally remove the plugin's state,
consent record included:

```bash
rm -rf ~/.local/state/omarchy/anki-theme
```

Keeping the state is harmless; it only means a future reinstall won't ask for
consent again.

## Configuration

Open **Tools → Add-ons → Ankiya → Config** in Anki:

| Key | Default | Meaning |
| --- | --- | --- |
| `contrast_clamp` | `true` | Clamp palette colors that would fall below their WCAG-derived contrast floor on core surfaces. Set `false` for faithful mode — colors exactly as the theme authors them. |

## Privacy

No network access, no telemetry. The add-on reads Omarchy's theme files and runs
inside Anki; it writes only its own configuration. The plugin asks before placing
anything in your Anki profile.

## Development

Repo: `Expri-commits/omarchy-anki-theme`. The project was charted and decided on a
[wayfinder map](.scratch/anki-theme/map.md) — the map, its tickets, and the
[research](.scratch/anki-theme/research/) notes are the full decision log.
Performance measurements live in [`docs/performance.md`](docs/performance.md);
architecture decisions in [`docs/adr/`](docs/adr/); the verification gate —
what "full surface" is held to, and how — in
[`docs/verification.md`](docs/verification.md).

- **Plugin** — Quickshell `service` (`Service.qml`), kept thin on purpose: payload
  install and theme watching only.
- **Add-on** — Python against Anki's own theme machinery: `tomllib`,
  `QFileSystemWatcher`, stdlib only. GUI-free logic is separated so pytest covers
  the mapping, clamp, and update-sync rules (`tests/`).
- **Tooling** — biome (JSON/JS), ruff (Python), qmllint/qmlformat (QML); tests run
  on the system python (the Anki runtime).

Prior art that shaped this project: [seara](https://github.com/rodrada/seara)
(in-Anki live-apply pattern), [ReColor](https://github.com/AnKing-VIP/AnkiRecolor)
(variable names only — it is AGPL, no code was used),
[catppuccin/anki](https://github.com/catppuccin/anki) (MIT palette-mapping
reference data).

## License

[MIT](LICENSE) — plugin, add-on, and docs.
