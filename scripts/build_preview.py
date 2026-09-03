#!/usr/bin/env python
"""Compose the marketplace preview montage: four theme shots cut as X quadrants.

Usage: /usr/bin/python scripts/build_preview.py tests/gate/artifacts/<run-id> \
    [--bottoms hackerman rose-pine ...] [--out DIR]

Every panel is the theme's canonical deck-browser shot (the instantly
recognizable surface), cover-cropped to the canvas and masked to one triangle
of an X cut. All four share one framing, so the cut reads as a single Anki
window in four palettes; the quadrants butt together with no interior seams,
only a thin outer frame.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests" / "gate" / "vendor"))
sys.path.insert(0, str(REPO / "scripts"))

from build_gallery import parse_shots  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

CANVAS = (1600, 900)
SEAM = 8
SEAM_COLOR = "#0e0e0e"
DEFAULT_TOP = "lumon:deck"
DEFAULT_LEFT, DEFAULT_RIGHT = "catppuccin-latte:deck", "osaka-jade:deck"
DEFAULT_BOTTOM = "rose-pine"
SURFACE_ALIASES = {"add": "add (editor)", "menu": "menu (grab)"}


def panel_shot(spec: str, deck: dict[str, dict[str, str]]) -> str:
    theme, _, surface = spec.partition(":")
    surface = SURFACE_ALIASES.get(surface, surface) or "deck"
    if theme not in deck or surface not in deck[theme]:
        msg = f"no {surface!r} shot for theme {theme!r} (have: {sorted(deck.get(theme, {}))})"
        raise SystemExit(msg)
    return deck[theme][surface]


def cover_image(path: str, size: tuple[int, int]) -> Image.Image:
    """Scale the shot to cover `size`, center-cropped to it."""
    shot = Image.open(path).convert("RGB")
    scale = max(size[0] / shot.width, size[1] / shot.height)
    resized = shot.resize((round(shot.width * scale), round(shot.height * scale)))
    x = (resized.width - size[0]) // 2
    y = (resized.height - size[1]) // 2
    return resized.crop((x, y, x + size[0], y + size[1]))


def build(run: Path, panels: dict[str, str], deck: dict[str, dict[str, str]]) -> Image.Image:
    w, h = CANVAS
    cx, cy = w // 2, h // 2
    polygons = {
        "top": [(0, 0), (w, 0), (cx, cy)],
        "left": [(0, 0), (cx, cy), (0, h)],
        "right": [(w, 0), (w, h), (cx, cy)],
        "bottom": [(0, h), (cx, cy), (w, h)],
    }
    canvas = Image.new("RGB", CANVAS)
    for slot, poly in polygons.items():
        mask = Image.new("L", CANVAS, 0)
        ImageDraw.Draw(mask).polygon(poly, fill=255)
        canvas.paste(cover_image(panel_shot(panels[slot], deck), CANVAS), (0, 0), mask)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, w - 1, h - 1], outline=SEAM_COLOR, width=SEAM)
    return canvas


def main() -> int:
    ap = argparse.ArgumentParser(description="Compose the preview montage from a gate run.")
    ap.add_argument("run", type=Path, help="gate run dir with shot-*.png")
    ap.add_argument("--out", type=Path, default=Path(".scratch/anki-theme/preview"))
    ap.add_argument("--top", default=DEFAULT_TOP, help="top triangle, THEME[:SURFACE]")
    ap.add_argument("--left", default=DEFAULT_LEFT, help="left triangle, THEME[:SURFACE]")
    ap.add_argument("--right", default=DEFAULT_RIGHT, help="right triangle, THEME[:SURFACE]")
    ap.add_argument("--bottoms", nargs="+", default=[DEFAULT_BOTTOM],
                    help="bottom-triangle theme candidates")
    ap.add_argument("--bottom-surface", default="deck", help="surface for the bottom triangle")
    args = ap.parse_args()
    run = args.run.resolve()
    if not run.is_dir():
        print(f"not a gate run dir: {run}", file=sys.stderr)
        return 2
    themes = parse_shots(run)
    deck = {name: dict(shots) for name, shots in themes.items()}
    args.out.mkdir(parents=True, exist_ok=True)
    panels = {"top": args.top, "left": args.left, "right": args.right}
    for bottom in args.bottoms:
        png = args.out / f"preview-{bottom}.png"
        spec = f"{bottom}:{args.bottom_surface}"
        build(run, panels | {"bottom": spec}, deck).save(png)
        print(png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
