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
