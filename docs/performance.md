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
