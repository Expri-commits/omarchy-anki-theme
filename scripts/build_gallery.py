#!/usr/bin/env python
"""Build the human-eyeball gallery from a gate run's artifacts.

Usage: /usr/bin/python scripts/build_gallery.py tests/gate/artifacts/<run-id>

Writes <run-id>/gallery/: one labeled montage per theme (deck, review,
add/editor, stats, prefs, menu grab), plus pathological / below-floor
montages and an index.html linking everything full-size. Feeds the
pre-flip eyeball pass and ticket 10's re-shot screenshots.
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
from pathlib import Path

MAGICK = "/usr/sbin/magick"
# shot-NNN-{kind}-{theme} for main/add, shot-NNN-{kind}-{kind}-{theme} for
# stats/prefs (the capture label repeats the target there)
SHOT_RE = re.compile(r"^shot-\d+-(main|add|stats-stats|prefs-prefs)-(.+)\.png$")
KIND_LABEL = {"add": "add (editor)", "stats-stats": "stats", "prefs-prefs": "prefs"}
TILE_W = 620  # montage tile width; heights keep native aspect


def parse_shots(run: Path) -> dict[str, list[tuple[str, str]]]:
    """theme -> ordered [(kind, path)]; the two ``main`` shots of a stock
    theme are deck then review, in step order (show_deck precedes show_review)."""
    themes: dict[str, list[tuple[str, str]]] = {}
    shots = sorted(run.glob("shot-*.png"))
    for shot in shots:
        m = SHOT_RE.match(shot.name)
        if not m:
            continue
        kind, theme = m.groups()
        if theme.startswith("below-floor-") or theme == "p1-faithful":
            continue  # special legs, montaged separately
        themes.setdefault(theme, []).append((KIND_LABEL.get(kind, kind), str(shot)))
    for theme in themes:
        mains = 0
        relabeled = []
        for kind, path in themes[theme]:
            if kind == "main":
                mains += 1
                kind = "deck" if mains == 1 else "review"
            relabeled.append((kind, path))
        themes[theme] = relabeled
    return themes


def montage(out: Path, tiles: list[tuple[str, str]]) -> None:
    cmd = [MAGICK, "montage", "-geometry", f"{TILE_W}x+8+8", "-tile", "3x", "-background", "#333"]
    for label, path in tiles:
        cmd += ["-label", label, path]
    cmd.append(str(out))
    subprocess.run(cmd, check=True)


def main() -> int:
    run = Path(sys.argv[1]).resolve()
    if not run.is_dir():
        print(f"not a gate run dir: {run}", file=sys.stderr)
        return 2
    out = run / "gallery"
    out.mkdir(exist_ok=True)

    themes = parse_shots(run)
    stock = [t for t in themes if not t.startswith("anki_theme-gate-")]
    pathological = [t for t in themes if t.startswith("anki_theme-gate-")]

    sections: list[tuple[str, str, Path]] = []
    for theme in stock:
        tiles = [(kind, path) for kind, path in themes[theme]]
        grab = run / f"menu-{theme}.png"
        if grab.exists():
            tiles.append(("menu (grab)", str(grab)))
        png = out / f"{theme}.png"
        montage(png, tiles)
        sections.append(("Stock palettes", theme, png))

    if pathological:
        tiles = []
        for theme in pathological:
            tiles += [(f"{theme} {kind}", path) for kind, path in themes[theme]]
        png = out / "pathological.png"
        montage(png, tiles)
        sections.append(("Pathological P1–P5 (clamped except P1-faithful)", "all", png))

    faithful = sorted(run.glob("shot-*-main-p1-faithful.png"))
    below = sorted(run.glob("shot-*-main-below-floor-*.png"))
    specials = [(f"below-floor / faithful: {p.stem.split('-', 3)[3]}", str(p)) for p in faithful]
    specials += [(f"below-floor / faithful: {p.stem.split('-', 3)[3]}", str(p)) for p in below]
    if specials:
        png = out / "below-floor.png"
        montage(png, specials)
        sections.append(("Below-floor legs + verbatim-faithful P1", "all", png))

    rows = []
    for title, label, png in sections:
        rows.append(f"<h2>{html.escape(title)}</h2>")
        rel = png.relative_to(run)
        rows.append(
            f"<figure><img loading='lazy' src='{html.escape(rel.name)}'>"
            f"<figcaption>{html.escape(label)}</figcaption></figure>"
        )
    (out / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Anki Theme gate gallery</title>"
        "<style>body{background:#222;color:#ddd;font-family:sans-serif;max-width:2000px;"
        "margin:auto}figure{display:inline-block;margin:8px}img{max-width:100%;"
        "outline:1px solid #555}figcaption{font-size:13px;padding:4px}</style>" + "\n".join(rows)
    )
    print(f"gallery: {out} ({len(themes)} themes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
