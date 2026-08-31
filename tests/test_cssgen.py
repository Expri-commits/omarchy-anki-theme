"""Tier 1 — generated CSS and engine script (implementation ticket 16)."""

import json

from anki_theme.cssgen import STYLE_ID, css_text, engine_script, to_css_var
from anki_theme.palette import VAR_NAMES, map_palette
from theme_fixtures import theme_palette


def test_aqt_name_to_css_var() -> None:
    assert to_css_var("FG") == "--fg"
    assert to_css_var("FG_LINK") == "--fg-link"
    assert to_css_var("CANVAS_GLASS") == "--canvas-glass"
    assert to_css_var("FLAG_7") == "--flag-7"
    assert to_css_var("STATE_NEW") == "--state-new"


def test_css_is_body_scoped_never_root() -> None:
    css = css_text(map_palette(theme_palette("catppuccin")))
    assert css.startswith("body {")
    assert ":root" not in css


def test_css_carries_every_mapped_var_plus_bootstrap() -> None:
    mapping = map_palette(theme_palette("catppuccin"))
    css = css_text(mapping)
    for name in mapping.vars:
        assert f"{to_css_var(name)}: {mapping.vars[name]};" in css
    for name, value in mapping.bootstrap.items():
        assert f"{name}: {value};" in css
    # The degradation themes omit absent-key vars rather than inventing values.
    degraded = css_text(map_palette(theme_palette("white")))
    assert "--flag-2" not in degraded
    assert "--state-buried" not in degraded


def test_css_includes_on_accent_primary_rule() -> None:
    mapping = map_palette(theme_palette("catppuccin"))
    css = css_text(mapping)
    assert f"body .primary {{\n  color: {mapping.on_accent};\n}}" in css


def test_css_is_deterministic() -> None:
    once = css_text(map_palette(theme_palette("gruvbox")))
    twice = css_text(map_palette(theme_palette("gruvbox")))
    assert once == twice


def test_engine_script_embeds_css_and_replaces_by_id() -> None:
    mapping = map_palette(theme_palette("catppuccin"))
    script = engine_script(css_text(mapping))
    # The CSS rides inside a JSON-escaped string literal.
    assert json.dumps(css_text(mapping)) in script
    assert f'"{STYLE_ID}"' in script
    assert "getElementById" in script
    assert "old.remove()" in script
    # DocumentReady-safe: both readyState branches present (ticket 09).
    assert 'readyState === "loading"' in script
    assert "DOMContentLoaded" in script
    assert script.startswith("(function () {")
    assert script.rstrip().endswith("})();")


def test_engine_script_round_trips_the_css() -> None:
    css = css_text(map_palette(theme_palette("nord")))
    script = engine_script(css)
    literal = script.split("var css = ", 1)[1].split(";\n", 1)[0]
    assert json.loads(literal) == css


def test_css_covers_the_tables_var_names() -> None:
    # catppuccin carries every consumed key, so its CSS must carry a name for
    # every var in the table — the generator and the mapping stay in lockstep.
    css = css_text(map_palette(theme_palette("catppuccin")))
    assert all(to_css_var(name) in css for name in VAR_NAMES)


def test_no_selectors_touch_card_content() -> None:
    """Card faces keep their notetype CSS (ticket 17): the only selectors the
    runtime ever injects are `body` (vars) and `body .primary` (on-accent)."""
    for name in ("catppuccin", "white"):
        css = css_text(map_palette(theme_palette(name)))
        script = engine_script(css)
        for selector in (".card", ".card ", " .card", ".cloze", "#ans"):
            assert selector not in css
            assert selector not in script
