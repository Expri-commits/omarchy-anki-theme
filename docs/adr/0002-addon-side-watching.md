---
status: accepted
---

# Add-on-side watching for the live switch, no D-Bus middle layer

Omarchy's theme switch rewrites `~/.local/state/omarchy/current/theme/` on disk and
signals nothing on D-Bus; the only desktop signal (xdg portal `SettingChanged`)
carries polarity alone, so a seara-style listener can never see same-polarity
palette changes. Instead of the originally sketched plugin→D-Bus→add-on relay, the
add-on watches the Omarchy state directory itself (`QFileSystemWatcher`) plus the
portal signal for polarity, and re-applies through Anki's theme pipeline directly.
The plugin's role narrows to delivery: consent-gated install and update of the
add-on payload.

## Considered Options

- Plugin-emitted D-Bus signal + palette JSON state file — demoted to fallback if
  file-watching proves unreliable (map ticket 09 is the falsification gate)

## Consequences

The add-on reads Omarchy's state directory directly, so it works regardless of
which trigger switched the theme (manual, theme-scheduler, any future plugin).
Mind the rm+mv directory swap (new inode) when watching.
