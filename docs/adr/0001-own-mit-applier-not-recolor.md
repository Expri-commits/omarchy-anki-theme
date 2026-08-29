---
status: accepted
---

# Own MIT applier instead of building on ReColor

ReColor themes Anki well but is AGPL-3.0, while this project ships to the Omarchy
plugin marketplace as MIT throughout — deriving from ReColor would contaminate the
license. We write our own concise applier against Anki's theme manager and CSS
variables, using ReColor strictly as read-only prior art for variable names and
config shape, and catppuccin/anki (MIT) config JSONs as reference palette data.

## Considered Options

- ReColor dependency or derivative — rejected: AGPL vs MIT everything
- catppuccin/anki as a base — rejected: it is ReColor config JSONs, not an engine;
  used as reference data only

## Consequences

We own the variable-mapping maintenance as Anki updates (charted in the map's
fog). No third-party add-on dependency at runtime.
