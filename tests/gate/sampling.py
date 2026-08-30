"""Deterministic PIL pixel sampling — the gate's only pass/fail path.

docs/verification.md: expected values are computed from the palette, sampled
points are fixed by the versioned sample map, and the tolerance is ±10 per
channel (the ticket-09 method). PIL itself is vendored under
tests/gate/vendor (python-pillow, same cp314 ABI as the system python) —
imported lazily so unmarked (commit-gate) collection never needs it.
"""

from __future__ import annotations

TOLERANCE = 10


def _pil():
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - setup guard
        raise RuntimeError(
            "PIL not importable — vendor it first: "
            "python -m pip install --target tests/gate/vendor pillow"
        ) from exc
    return Image


class Shot:
    """One captured PNG window, in shot coordinates."""

    def __init__(self, path) -> None:
        self.path = str(path)
        image = _pil().open(self.path)
        self.size = image.size
        self._image = image.convert("RGB")
        self._pixels = self._image.load()

    def px(self, x: int, y: int) -> tuple[int, int, int]:
        # No silent clamping: a point mapped outside the shot is a mapping
        # bug, and sampling an edge pixel would hide it behind a wrong color.
        if not (0 <= int(x) < self.size[0] and 0 <= int(y) < self.size[1]):
            raise AssertionError(f"point ({x}, {y}) outside shot {self.size} ({self.path})")
        return self._pixels[int(x), int(y)]


def channel_delta(sample: tuple[int, int, int], expected: tuple[int, int, int]) -> int:
    return max(abs(s - e) for s, e in zip(sample, expected, strict=True))


def assert_color(
    surface: str, point_name: str, shot: Shot, xy: tuple[int, int], expected: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Sample one point; fail naming surface, point, sampled vs expected."""
    sample = shot.px(*xy)
    delta = channel_delta(sample, expected)
    if delta > TOLERANCE:
        raise AssertionError(
            f"{surface}/{point_name}: sampled rgb{sample} at shot {xy}, expected "
            f"rgb{expected} — max channel delta {delta} > {TOLERANCE} "
            f"({shot.path})"
        )
    return sample


def scan_for_color(
    shot: Shot, rect: tuple[int, int, int, int], expected: tuple[int, int, int]
) -> tuple[tuple[int, int], tuple[int, int, int]]:
    """Deterministic glyph scan: the pixel in the rect closest to expected.

    Text pixels are antialiased against their fill, so a fixed point can land
    between strokes; the closest-in-rect pixel is a pure deterministic
    function of the render (no thresholds, no ordering luck). Returns
    (pixel, sampled rgb); the caller asserts the distance and uses the
    sampled pair for contrast checks.
    """
    x, y, w, h = (int(v) for v in rect)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(shot.size[0], x + w), min(shot.size[1], y + h)
    if x1 <= x0 or y1 <= y0:
        raise AssertionError(f"scan rect {rect} outside shot {shot.size} ({shot.path})")
    best, best_xy, best_sample = None, None, None
    for py in range(y0, y1):
        for px in range(x0, x1):
            sample = shot.px(px, py)
            delta = channel_delta(sample, expected)
            if best is None or delta < best:
                best, best_xy, best_sample = delta, (px, py), sample
    assert best_xy is not None and best_sample is not None
    return best_xy, best_sample


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """WCAG ratio between two sampled rgb triples — one WCAG math source: the
    pure `ankiya.palette` helpers, via hex round-trip."""
    from ankiya.palette import contrast_ratio as hex_ratio

    def hexed(rgb: tuple[int, int, int]) -> str:
        return "#{:02x}{:02x}{:02x}".format(*rgb)

    return hex_ratio(hexed(a), hexed(b))
