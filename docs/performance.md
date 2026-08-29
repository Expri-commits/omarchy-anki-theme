# Performance record

Append-only log. Every entry is dated, tied to a commit, and states the method and
machine; regressions get investigated, never erased.

## Standing metrics

- **switch-to-reapply** — time from `omarchy-theme-set` completion to Anki visually
  recolored, same-polarity switch (dark→dark palette change, the case Anki ignores
  natively). Measured from add-on-side timestamps in the log, cross-checked once
  against a screen recording frame diff.
- **add-on startup cost** — ms added to Anki profile load with the add-on enabled
  vs. disabled (mean of ≥5 runs).

## Log

| Date | Commit | Metric | Value | Method | Notes |
| ---- | ------ | ------ | ----- | ------ | ----- |
| 2026-08-29 | 3a31695 | dev-loop: watcher/IPC plugin reload | < 1 s (same-second) | journal timestamps of `Local plugin changed` → service mount proof-file rewrite | Reload re-instantiates but runs **stale** compiled QML (upstream basecamp/omarchy#6981) — not a valid apply path for code changes |
| 2026-08-29 | 3a31695 | dev-loop: `omarchy restart shell` (applies QML code) | ~0.4 s command return, service remounted ~2 s later | `time omarchy restart shell` + proof-file `mountedAt` delta; Ryzen 5 7500F, Arch, quickshell 0.3.1-1, Omarchy 4.0 line | The mandatory step per QML code change (ticket 05) |
| 2026-08-29 | 1264eb4 | ticket 06 prototype runs (mocha + latte) | not measured — standing metrics don't apply | Throwaway spike on the live install (Ryzen 5 7500F, Arch): no live-switch applier exists yet to time and nothing shipped on main | Recorded to close a review gap: first switch-to-reapply and startup-cost numbers land with the ticket-07/09 applier |
| 2026-08-29 | e3118de | switch-to-reapply (spike, all 3 legs incl. same-polarity) | recolor completes **265–283 ms before `omarchy theme set` returns** (i.e. ≤ 0 ms after completion); in-app apply 12.6–14.8 ms; end-to-end from command invocation 426–433 ms | Add-on-side timestamps joined to driver timings (`legs.jsonl` × `applied.log`), recolor pixel-verified hard (menubar, web canvas, open Add window) in before/after shots; ticket 09 spike, branch `prototype/09-live-switch`; Ryzen 5 7500F, Arch, Anki 26.08.1, Omarchy 4.0 line | First real numbers; screen-recording frame-diff cross-check still pending (single method so far) |
| 2026-08-29 | e3118de | add-on startup cost (spike, preliminary) | 4.3 ms startup palette apply (single run) | Time from `profile_did_open` to applied marker, spike add-on; same machine | NOT the standing metric yet — that needs the ≥5-run profile-load delta with the real add-on |
