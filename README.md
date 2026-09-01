# Anki Theme for Omarchy

Anki recolored live in whatever Omarchy theme is active. Switch themes from the
menu, the CLI, or a scheduler and a running Anki follows: deck browser,
reviewer, editor, stats, dialogs, menus, tables. No restart.

https://github.com/user-attachments/assets/029fc080-28ee-490b-8d8e-955f56667da2

All 22 stock Omarchy themes work, and so do user-made ones: colors are read
from Omarchy's own `colors.toml`, so there is no per-theme setup. Dark→dark
switches (Catppuccin → Gruvbox) work too, which Anki's own theme handling
can't see. A legibility guard nudges palette colors that would drop text
below a WCAG-derived contrast floor back toward readable; set
`contrast_clamp = false` in the add-on config if you want the palette exactly
as authored. No network access, no dependencies beyond Omarchy and Anki.

## How it works

An Omarchy plugin (Quickshell `service`) watches the theme state directory.
The first time it runs, it shows a dialog asking for consent
to install a small bundled add-on into Anki
(`~/.local/share/Anki2/addons21/anki_theme`). The add-on reads `colors.toml`,
maps the palette onto Anki's color variables, and re-applies through Anki's
own theme pipeline. The recolor usually lands before the theme command
returns.

When Anki itself updates, its color variables can churn. The add-on notices
at startup and degrades gracefully, keeping Anki's own colors on surfaces it
no longer knows, with one transient tooltip until a plugin update restores
coverage.

## Requirements

- Omarchy 4.0.1 or newer. On older versions the plugin stays inert.
- Anki 26.08 or newer (Qt6), profile at the default `~/.local/share/Anki2`.

## Install

```bash
omarchy plugin add https://github.com/Expri-commits/omarchy-anki-theme
```

Nothing is written into your Anki profile until you click Allow in the
consent dialog. Start Anki (or restart it if it is already running) and it
follows your theme. Plugin updates also update the installed add-on at the
next shell restart or Anki start, under the same consent. If you delete the
add-on in Anki but keep the plugin, a dialog offers to reinstall it;
ignoring that leaves things as they are.

## Remove

1. `omarchy plugin remove io.github.expri-commits.anki-theme`
2. In Anki: Tools → Add-ons → Anki Theme for Omarchy → Delete

Removing the plugin alone leaves the add-on theming; it just stops getting
updates. Step 2 turns the colors off. Restart Anki and it returns to its own
theming. To also drop the plugin's state, consent record included:

```bash
rm -rf ~/.local/state/omarchy/anki-theme
```

## Config

One setting, under Tools → Add-ons → Anki Theme for Omarchy → Config in Anki:
`contrast_clamp`, default `true`. Set it to `false` for faithful mode, colors
exactly as the theme authors them.

## Privacy

No network access, no telemetry. The add-on reads Omarchy's theme files and
writes only its own configuration and small status markers in the plugin's
state directory. It asks before placing anything in your Anki profile.

## Development

Two parts: a thin Quickshell `service` (`Service.qml`) that installs and
updates the payload, and a stdlib-only Python add-on that does the
recoloring. GUI-free logic is split out so pytest covers the mapping,
clamping, and update sync rules (`tests/`). Tooling: biome (JSON/JS), ruff
(Python), qmllint/qmlformat (QML); tests run on the system python.

- [Performance log](docs/performance.md)
- [Architecture decisions](docs/adr/)
- [Verification gate](docs/verification.md)

Prior art: [seara](https://github.com/rodrada/seara) (in-Anki live-apply
pattern), [ReColor](https://github.com/AnKing-VIP/AnkiRecolor) (variable
names only; it is AGPL, no code was used),
[catppuccin/anki](https://github.com/catppuccin/anki) (MIT palette-mapping
reference data).

## License

[MIT](LICENSE): plugin, add-on, and docs.
