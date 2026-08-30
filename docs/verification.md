# Verification gate

How the full-surface claim is proven and kept true. Decisions and rationale live
in [wayfinder ticket 14](../.scratch/anki-theme/issues/14-verification-approach.md);
this file is the operational spec the implementation builds against, plus the
residuals ledger the gate is responsible for closing. Vocabulary per
[CONTEXT.md](../CONTEXT.md).

## Principle: palette-derived oracles, deterministic asserts

- **Expected values are computed, not remembered.** Every assert derives from the
  active Palette (`colors.toml`) through the locked mapping (ticket 07) plus the
  compositing rules — never from a previous run's pixels. Golden screenshots are
  **not** diff baselines: font rendering, hinting, and Anki asset churn make
  whole-image diffs flaky, and the question the gate answers is pointwise —
  "does this element's color equal the palette key it should consume."
- **PIL pixel sampling is the only pass/fail path for surface-render fidelity**
  (±10/channel, the tolerance the ticket-09 method used). The vision subagent
  builds sample-point maps and reviews galleries; it never decides a run.
- **Contrast asserts are computed from the render itself**: sample the text
  pixel and its fill pixel, assert the pair meets its Floor (WCAG ratio). The
  render proves the Clamp landed — the pure-function tests alone don't.
- **Characterize-then-lock** for points whose exact formula isn't known a
  priori: stats chart series colors, and the Anki-default hexes the
  below-floor legs assert against. Characterized values/formulas are locked
  into versioned oracle data keyed by Anki version (`tests/gate/data/<anki
  version>/sample_points.json`), then derived like everything else. First
  characterization (26.08.1, ticket 22): the deck-row highlight is **not a
  blend** — `tr.deck.current td` paints `--border-subtle` opaque, so the
  oracle is the `selection` key verbatim; further characterized facts live in
  the map's `characterized` section and the corrections to the surface table
  below.

## Tiers

### Tier 1 — pure logic (commit gate; every change)

`pytest` on system python, GUI-free, seconds. Suites:

- **Mapping** — the 22 stock palettes (vendored colors.toml fixtures) through
  the full 25-key → 51-var + `--bs-*` table; missing-key degradation (the
  `white` theme: `FLAG_2`/`STATE_BURIED`/`FLAG_7` stay stock); `bright_blue`
  fallback to `blue`; alpha rules (glass 0.4, disabled/selection 0.5); the
  on-tint luminance rule.
- **Clamp** — zero adjustments across all 22 stock palettes (the
  stock-invisibility regression ticket 08 requires: floors must stay invisible
  as Omarchy adds themes); synthetic pathological palettes P1–P5 (below) hit
  their floors — or the max-min foreground plus the `AA unsatisfiable across
  backgrounds` line for band-violating cases; faithful mode adjusts nothing;
  backgrounds byte-identical; hue/saturation preserved by the lightness nudge.
- **Sync** — stamp compare (install / swap / skip / downgrade converges
  backward); swap idempotence and ENOENT re-entrancy; crash recovery order
  (recover the interrupted swap first, then sweep dot-siblings; `meta.json`
  salvaged); refuse-unstamped-folder; unique stage names under simulated
  concurrency; `config.json` ships fresh, `meta.json` carried over,
  `__pycache__` never.
- **CSS / script generation** — body-scoped vars string, the injected
  `.primary` on-accent rule, the four `--bs-*` extras, engine-script source.
- **Var-inventory tripwire** (the dev-side half of
  [ticket 15](../.scratch/anki-theme/issues/15-anki-upgrade-var-churn-policy.md)'s
  drift policy — the same snapshot and diff routine ship in the add-on payload
  as its runtime startup check: `ankiya/var_snapshot.txt` + `ankiya/drift.py`)
  — a vendored snapshot of the installed Anki's `aqt.colors` names; tier 1
  asserts the mapping covers every name in the snapshot **and** that the live
  `aqt.colors` inventory still matches it. The Anki-upgrade regeneration move:
  run `/usr/bin/python scripts/regen_var_snapshot.py`, repair `VAR_RULES`
  (`ankiya/palette.py`) for any retracted/added names it reports, re-run tier
  1, and ship via the normal payload propagation. Renamed/retracted vars fail
  here first.

### Tier 2 — fast live leg (changes touching theming delivery)

`pytest -m gate` (markers registered in `pyproject.toml`, excluded from the
commit gate by `addopts` — a CLI `-m` overrides). Any change under the payload
tree (mapping, clamp, applier, sync, runtime), `Service.qml`, or `tests/gate/`
itself. Minutes (~30 s after the Qt caches warm; the scratch base's first run
builds them). One session: a dedicated scratch base (seeded via aqt's own
`ProfileManager`, the dev-linked payload under `ANKIYA_BUNDLED_PAYLOAD` pinned
to the same tree so the bootloader's sync check lands on "current"), the
dev-only `zz_gate_control` add-on as control channel (command files in, JSON
results out), window capture via grim after focus (the ticket-09 method;
`hyprctl dispatch` speaks Lua since Hyprland 0.55, and focus is verified by
observed effect — hyprctl exits 0 for no-ops). Bootstrap once per machine:
`python -m pip install --target tests/gate/vendor pillow` (gitignored; PIL on
the system python, same cp314 ABI). Outputs — every probe report, switch
record, and screenshot — land under `tests/gate/artifacts/<run>/` (gitignored;
tier 3's gallery builds on this path). Fixture hygiene: the original theme is
restored and the scratch base removed on teardown; `--no-restore` keeps both
for debugging.

- Palettes: **Catppuccin** (dark) + **Catppuccin Latte** (light).
- Surfaces: deck browser, reviewer, editor (Add screen, sveltekit), menubar
  (Qt chrome) — the tier-3 subset.
- **One same-polarity live switch on the running instance** (Catppuccin →
  Gruvbox, the case Anki ignores natively): nothing navigates between the
  switch and the captures — deck canvas + menubar and the still-open Add
  window (no page rebuild) must be pixel-exact, and the run's timings must
  sit inside the thresholds below.
- Thresholds assert on **every** switch (in-app apply from the applied
  record; the 250 ms bound measured from the observed state-dir swap — the
  `theme.name` poll in the harness — not from command invocation, whose
  front half runs before any swap happens).

### Tier 3 — full gate (checkpoints)

`pytest -m gate_full`. Runs at:

1. **Pre-release**: before the marketplace flip (this is what "passed ticket
   14's verification" means — all three tiers green on the exact submission
   tree, gallery reviewed) and before **every** marketplace update submission.
2. **Version bumps**: any Anki or Omarchy upgrade on the dev machine —
   characterize, re-point the sample maps, re-run. This is the upgrade-regression
   detector ticket 15's question pointed at.
3. **Unexplained drift** in any tier-2 run.

Composition:

- **Stock matrix** — all 22 stock palettes × the full surface set below.
- **Pathological user palettes** — registered as user themes so every switch
  rides the production `omarchy theme set` path:
  - P1 `fg≈bg` — core foreground×background guard (4.5).
  - P2 `link≈bg` — link floor (3.0).
  - P3 mid-luminance accent (~`#808080`) — on-tint guard (3.0) **and the
    focus-ring observation** ticket 08 deferred here: accent-as-ornament stays
    verbatim unless this render shows the focus ring effectively invisible.
  - P4 straddling backgrounds — max-min foreground + `AA unsatisfiable` logged.
  - P5 dead-zone `#ffffff`/`#595959`/`#010101` — ticket 08's pairwise-band
    fixture, same max-min path.
  - P1 again in faithful mode — rendered verbatim (sampled hexes equal the
    authored keys).
  - Asserts per render: text/fill pairs sampled **from the render** meet their
    floors (faithful run excepted); background pixels byte-match the authored
    keys; the add-on log's clamp lines match what tier-1's pure function
    predicted (key, old → new, ratios).
- **Update-propagation legs** (ticket 12): drift the bundled payload, start
  Anki **without touching the shell**, assert convergence (installed stamp
  equals the bundled hash, new behavior live); race smoke (service-mount sync
  during Anki launch stays convergent); downgrade converges backward.
- **Below-floor legs** (ticket 13): service under a PATH-shimmed old
  `omarchy-version`, **and with the command failing to run entirely**, stays
  inert — one journal line, consent toast never shown, nothing mounted; add-on
  with the palette file **absent or unreadable** applies nothing — one log
  line, surfaces assert against characterized Anki defaults. Characterized
  with ticket 23: the no-op must be **total** — hooks are wired before the
  apply crashes, so the web delivery legs would otherwise serve the previous
  session's generated CSS; every web/engine-script delivery is gated on this
  process's first completed apply (`_applied_once` in the runtime). The legs
  pin the base dark (`pm.meta["theme"]`) so the characterized defaults they
  assert against aren't polarity-relative.
- **Consent / reinstall / standalone smoke** (tickets 11/12): fresh consent
  flow (toast → click → install → minimal `meta.json` → no re-ask next service
  start); reinstall toast after Anki-side delete, once per service start,
  gated on the Anki data dir existing; standalone mode (plugin removed →
  theming continues, no Omarchy notifications; a drift tooltip may still fire,
  ticket 15).
- **Drift smoke** (ticket 15): mocked retract-class inventory drift — one log
  line + one transient tooltip, second start silent (state-dir signature
  dedup); add-class drift log-only.
- **Perf session** — the standing metrics per their defined methods
  ([performance.md](performance.md)): switch-to-reapply (real add-on, incl. the
  screen-recording frame-diff cross-check the spike left pending), add-on
  startup cost (≥5-run mean, sync check included — the startup check is
  ticket 12's perf duty). Plus swap cost, measured once (ticket 12's other
  duty). Appends to the log.
- **Gallery review** — every surface × palette screenshot archived under
  `tests/gate/artifacts/` (**gitignore that path when the gate lands**); vision
  subagent sweeps for seams sampling can't see (the deep `--bs-*` decision
  below, unthemed patches, focus rings, on-tint legibility); the gallery stays
  available for the human eyeball pre-flip and feeds ticket 10's re-shot
  screenshots.

## The mandatory surface set

Corrections from the 26.08.1 characterization (ticket 22) are folded in — the
original oracle guesses that aqt's own CSS contradicts are marked.

| # | Surface | Sample points (oracle) |
| --- | --- | --- |
| 1 | Deck browser (webview + the table) | canvas = `background`; current row = **`selection` verbatim** (characterized: `td` paints `--border-subtle` opaque — no blend); deck name = **`foreground`** (characterized: `a.deck { color: var(--fg) }`, not the link var; `--fg-link` has no consumer on this surface) |
| 2 | Reviewer | page = `background` in night mode; **in light mode the page mirrors the card's own background** (characterized: Anki blends the card in — the assert locks the authored card hex, proving theming left the notetype layer alone); card face keeps notetype CSS (asserted unchanged, ticket 07 rule 4 — tier 2 seeds a notetype with an authored background); bottom bar buttons = `lighter_background` (`button { background: var(--button-bg) }`) |
| 3 | Editor / Add screen (sveltekit) | page = `background`; input fill = elevated (`--canvas-elevated`); focus ring = `accent` (the focused field's outline) |
| 4 | Stats | characterized (26.08.1, ticket 23): page bg sampled DOM-anchored 24 px below the intro canvas — the right edge sits on flot's axis-label glyph column (invisible in dark mode, dark glyphs in light); first series = `STATE_NEW` drawn by flot at fill 0.7 over the page canvas, oracle blends at 0.7 |
| 5 | Qt chrome: menubar + toolbar | `background` / `foreground` |
| 6 | Open dropdown menu | menu bg/fg; highlighted row = `selection`@0.5 + on-tint — sampled from the widget's own `grab()` (characterized, ticket 23: Wayland clients can't know a popup's compositor position and the compositor dims the window behind it; the highlight is driven synthetically — `setActiveAction` + a synthetic MouseMove at the action center, re-driven until the report moment) |
| 7 | Modal dialog (Preferences) | characterized (ticket 23): the reachable dialog is the **native Qt one** — 26.08.1's sveltekit prefs page hides on a non-current labs tab, its webview never visible; dialog margin = `background` (QPalette Window), tab pane = `dark_background` (`aqt.stylesheets.tabwidget`) |
| 8 | Toast + tooltip | `dark_background` / `foreground` (overlay semantics, ticket 07) — **joins when the mechanism lands** (ledger row 1) |
| 9 | Below-floor no-op renders | characterized Anki defaults |

## Thresholds (asserted on every live run)

| Bound | Threshold | Grounding |
| --- | --- | --- |
| In-app apply (palette read, post-debounce → all four delivery legs done) | ≤ 200 ms same-polarity; ≤ 250 ms across a dark↔light flip | 12.6–14.8 ms short-session (ticket 09 spike); two additive costs scale it: aqt's `_apply_style()` re-polishes every Qt widget on a polarity flip (~60–115 ms), and the restyle loop pays per open webview — the full-matrix session accumulates up to 7 restyled views, a 12 ms first switch reaching a **150–260 ms plateau, variable across sessions** (perf log 2026-08-30). **These bounds are mid-recalibration**: they cover one observed session, not the other — raise (≈ 400/450 ms) or scale per view once the per-view cost investigation (deferred 2026-08-30) lands |
| State-dir swap → applied record (all delivery legs done, open-page evals included) | ≤ 450 ms same-polarity; ≤ 500 ms across a flip | completes before `omarchy theme set` returns in short sessions (ticket 09); the flip + session-scale view count ride the same window (150 ms debounce + apply + jitter) |
| Startup single-run sanity (the apply; the sync check rides unmeasured at import) | ≤ 100 ms | 4.3 ms apply, ~10-file tree walk (startup restyles only the 3 launch views) |

The flip split keys off a `polarity_flip` field the applied record carries
(ticket 23): same-polarity switches keep the tight bounds, dark↔light flips get
their own — one slow population must not loosen the threshold the standing
metric asserts.

The in-app apply bound starts at the **post-debounce palette read** — ticket
09's measured interval. The watcher's 150 ms debounce + digest guard run before
that point and are excluded deliberately: they are coalescing machinery, not
apply work, and the end-to-end bound (row 2) already covers them from the
outside. Row 2's clock starts at the **observed state-dir swap** (the harness
polls `theme.name`, 5 ms granularity — not command invocation, whose front half
runs before any swap happens) and ends at the applied record; the pixel-exact
sampling that proves the recolor visually is asserted untimed after a fixed
settle, since capture machinery (focus + grim) must not sit inside a render
budget.

Standing-metric **sessions** (defined methods, ≥5-run means) append to the perf
log; tier-2/3 runs threshold-assert internally and append only their session
entries — a deliberate interpretation of the append-every-measurement rule so
the log records method-consistent numbers, not per-run noise. Threshold
failures are regressions: investigated, never tuned away.

## Residuals ledger

| # | Residual | Provenance | State |
| --- | --- | --- | --- |
| 1 | Toast/tooltip `QPalette` roles unreached by the var mapping; overlay semantics assigned, mechanism TBD at build | tickets 06 → 07 | Gate grows surface 8 when the mechanism lands; until then stock toast/tooltip is expected behavior, not a failure |
| 2 | Bootstrap vars beyond `--bs-body-bg/-color`, `--bs-border-color`, `--bs-link-color` stay stock | ticket 07 | Tier-3 gallery sweep decides: visible sveltekit seam → mapping grows rows (tests follow); no seam → closed as sufficient |
| 3 | Focus-ring visibility with accent-as-ornament verbatim | ticket 08 | P3 render + gallery observation; invisible ring reopens ticket 08's policy |
| 4 | Link floor checked against `background` only — elevated-surface link contrast unobserved | ticket 08 mechanics 5 | Tier-3 gallery observation on deck-browser-filtered/elevated links |
| 5 | Stats chart colors uncharacterized | ticket 14 | **Closed (ticket 23)**: first tier-3 run characterized them — first series = `STATE_NEW` (aqt's theme) drawn by flot at fill 0.7 over the page canvas; oracle blends at 0.7, verified δ2. Locked in `tests/gate/oracles.py` (`stats_added_bar`) |

## Infrastructure notes

- The gate drives a **dedicated Anki base/profile** — the user's collection is
  never the test substrate (the 06/09 spikes ran on the live install with
  restore discipline; the gate must not need it). If aqt ignores base
  relocation in GUI mode, a dedicated profile inside the real base is the
  fallback.
- A **dev-only gate add-on** (in `tests/gate/`, never shipped; not the
  `omarchy_live` spike name) provides the control channel — the hardened
  ticket-06/09 driver pattern (deck select + async nextCard polling, restore
  after).
- Sample-point maps + oracle data: `tests/gate/data/`, keyed by Anki version;
  rebuilt via the vision subagent on characterization runs.
- Machine hygiene is fixture-shaped: setup/teardown restores the active theme,
  removes gate themes/profiles/add-on; `--no-restore` escape hatch for
  debugging a failed run. **Landed with ticket 22**: the harness restores the
  theme and removes the scratch base on teardown, keeps every artifact under
  `tests/gate/artifacts/<run>/`, and the gate add-on seeds its own base
  (deck + due card + authored-card-background notetype) — the user's
  collection is never the test substrate.
- Markers: **landed with ticket 22** — `gate`/`gate_full` are registered in
  `pyproject.toml` with default exclusion via `addopts` (a CLI `-m` overrides
  it, per pytest's last-`-m`-wins rule), so the commit gate stays fast and
  the live legs are explicit invocations.
