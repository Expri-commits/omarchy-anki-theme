"""Web-delivery text generation: body-scoped CSS and the engine script.

Pure string work over a `Mapping` — the applier (ticket 17) writes the CSS
into the add-on's web/ dir for stdHtml pages and bakes the engine script into
a profile-level QWebEngineScript for sveltekit pages.

Scoping is on `body`, never `:root` (ticket 02 §2.5): stdHtml inlines
`:root { --canvas: … }` after webview.css, and an element-scoped declaration
on body wins for everything inside it regardless of stylesheet order. The
`.primary` rule gets `body` prefix for the same reason — the script-injected
style must beat stock's `.primary { color: #fff }` on specificity, not order.
"""

from __future__ import annotations

import json

from ankiya.palette import Mapping

STYLE_ID = "ankiya-style"


def to_css_var(aqt_name: str) -> str:
    """aqt.colors name → CSS variable name (FG_LINK → --fg-link)."""
    return "--" + aqt_name.lower().replace("_", "-")


def css_text(mapping: Mapping) -> str:
    """The palette as body-scoped CSS variables plus the on-accent rule.

    Deterministic in Mapping order — regenerating with an unchanged palette
    yields a byte-identical string.
    """
    declarations = [f"{to_css_var(name)}: {value};" for name, value in mapping.vars.items()]
    declarations += [f"{name}: {value};" for name, value in mapping.bootstrap.items()]
    css = "body {\n  " + "\n  ".join(declarations) + "\n}"
    if mapping.on_accent is not None:
        css += f"\nbody .primary {{\n  color: {mapping.on_accent};\n}}"
    return css


def engine_script(css: str) -> str:
    """QWebEngineScript source carrying `css`, DocumentReady-safe.

    Remove-then-insert keyed on STYLE_ID: Qt6 script collections have no
    update(), and re-inserting without removing would stack style elements on
    every live re-apply (ticket 09 spike finding).
    """
    return (
        "(function () {\n"
        '  "use strict";\n'
        f"  var css = {json.dumps(css)};\n"
        f"  var id = {json.dumps(STYLE_ID)};\n"
        "  function apply() {\n"
        "    var old = document.getElementById(id);\n"
        "    if (old) {\n"
        "      old.remove();\n"
        "    }\n"
        '    var style = document.createElement("style");\n'
        "    style.id = id;\n"
        "    style.textContent = css;\n"
        "    (document.head || document.documentElement).appendChild(style);\n"
        "  }\n"
        '  if (document.readyState === "loading") {\n'
        '    document.addEventListener("DOMContentLoaded", apply);\n'
        "  } else {\n"
        "    apply();\n"
        "  }\n"
        "})();\n"
    )
