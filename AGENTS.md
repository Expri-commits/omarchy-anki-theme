# AGENTS.md — omarchy-anki-theme

Anki recolored live in the active Omarchy palette: a Quickshell `service` plugin (QML/JS) plus an MIT Anki add-on (Python), published to the Omarchy plugin marketplace at the end. MIT throughout.

## Orient before working

Development runs on the wayfinder map: read `.scratch/anki-theme/map.md` at the start of any session, claim one frontier ticket at a time, and record resolutions on the ticket and the map. Use the mattpocock skill that matches the move — /grilling, /domain-modeling, /prototype, /tdd, /research, /code-review — instead of improvising a flow.

## Working rules

- **Accelerate with subagents.** Dispatch independent work — research, multi-file exploration, disjoint implementation slices — as parallel subagents in one message; keep the main thread for human-in-the-loop decisions. Every subagent prompt is self-contained and carries the docs rule below verbatim, because subagents inherit neither this file nor global instructions.
- **Docs before internal knowledge.** Before writing code against a third-party surface — aqt/Anki internals, Qt6, Quickshell/QML, biome, ruff, pytest — resolve it on Context7 and query the docs there; agent priors go stale. For vendor docs Context7 lacks (Anki's own add-on docs), fetch them with exa. All web lookups go through the exa-search MCP tools only; the built-in WebSearch/WebFetch/webReader/analyze_image are out of credits and forbidden.
- **Land features with tests.** Pure logic lives in GUI-free modules (plain Python functions, plain JS) so pytest reaches it; QML/Qt glue gets smoke scripts under `tests/`. A feature is done when its tests pass, not when its code runs.
- **Record performance.** Measure the standing metrics (theme-switch → Anki reapplied latency; add-on startup cost) and append every measurement to `docs/performance.md`, dated, with method and machine. The log is append-only; regressions get investigated, not erased. What appends is the method-consistent measurement session; automated verification-gate runs record their timings into the run artifacts (`perf-*.json[l]`) and append only their session rows — timing asserts were de-gated 2026-08-30 by user directive (correctness first), the interpretation locked in `docs/verification.md`; the perf-polish ticket re-arms them recalibrated.
- **The commit gate is green.** Lint, format, and tests all pass before any commit: biome for JSON/JS (`biome.json` committed), ruff for Python, qmllint + qmlformat for QML.

## Agent skills

### Issue tracker

Issues live as local markdown under `.scratch/<feature>/`; the active effort is the wayfinder map at `.scratch/anki-theme/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary; label strings equal the role names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `CONTEXT.md` plus `docs/adr/`. Use the glossary's vocabulary in issues, tests, and proposals. See `docs/agents/domain.md`.

## Tooling gotchas

- Python tooling runs on **system python** (the Anki runtime): invoke tests as `pytest` or `/usr/bin/python -m pytest`, never bare `python -m pytest` — agent shells resolve `python` to mise 3.14.6, which cannot see `aqt`. Keep ruff/pytest pacman-installed (`omarchy pkg add`), never mise-managed: mise-managed global python breaks system-python packages like anki itself (basecamp/omarchy#2831).
- gh hangs through the Omarchy `~/.local/bin/gh` wrapper — it runs unversioned `mise use -g gh` (a network "latest" lookup) on every invocation; pinned mise tools are fine, this is not a general shim problem — call the direct binary under `~/.local/share/mise/installs/gh/*/gh_*/bin/gh` with `GH_HOST=github.com GH_PROMPT_DISABLED=1` and a timeout.
