# Vendored stock Omarchy palettes

The 22 stock themes' `colors.toml`, copied from `/usr/share/omarchy/themes/`
on 2026-08-29 (Omarchy 4.x). Tier-1 oracles must not drift with the live
install, so the fixtures are vendored rather than read at run time. Refresh
the copy deliberately — a diff here is a stock-palette change worth seeing.

`white`, `last-horizon`, and `solitude` ship without `orange`/`brown`; three
themes (hackerman, last-horizon, solitude) carry non-color `hyprland_*` keys.
Both facts are load-bearing test cases (missing-key degradation; unknown-key
tolerance) — preserve them.
