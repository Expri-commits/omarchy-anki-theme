"""Screen-recording frame diff — the switch-to-reapply cross-check.

The standing metric's definition (docs/performance.md) cross-checks the
applied-record timestamps against the moment the recolor is actually visible
on screen — the leg the ticket-09 spike left pending. gpu-screen-recorder
(60 fps CFR, the omarchy wrapper's own settings) records the session during
two stock switches; ffmpeg downscales every frame to a 32x18 rgb stream and
`flip_frame` finds the first frame whose mean color settles on the target
palette's canvas. Comparing the two flips' *interval* against the applied
records validates the timestamps with the recorder's start offset unknown;
the first-frame wall clock (the `-write-first-frame-ts` sidecar TSV —
characterized) anchors the absolute claim.
"""

from __future__ import annotations

import json
import signal
import subprocess
import time
from pathlib import Path


class Recorder:
    """One gpu-screen-recording of the whole screen, stopped on exit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.t_spawn = time.time()
        self.t_stop = 0.0
        self._proc = subprocess.Popen(
            [
                "gpu-screen-recorder",
                "-w",
                "screen",
                "-f",
                "60",
                "-fm",
                "cfr",
                "-k",
                "auto",
                "-fallback-cpu-encoding",
                "yes",
                "-write-first-frame-ts",
                "yes",
                "-o",
                str(path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> float:
        """SIGINT (the wrapper's own stop signal — SIGTERM discards), then
        wait for the muxer to flush. Returns the wall clock at the stop."""
        self.t_stop = time.time()
        self._proc.send_signal(signal.SIGINT)
        self._proc.wait(timeout=60)
        return self.t_stop


def _frame_means(path: Path) -> tuple[float, list[tuple[int, int, int]]]:
    """(fps, per-frame 32x18 mean rgb) — ffmpeg decodes, python means.

    rawvideo on the pipe keeps it to one decode pass; 576 px/frame is nothing
    and antialiasing detail is exactly what we don't want here.
    """
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            "scale=32:18",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    raw, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame decode failed: {err.decode()[-400:]}")
    fps = _fps(path)
    frame = 32 * 18 * 3
    pixels = 32 * 18
    means = []
    for start in range(0, len(raw) - frame + 1, frame):
        px = raw[start : start + frame]
        means.append(
            (
                sum(px[0::3]) // pixels,
                sum(px[1::3]) // pixels,
                sum(px[2::3]) // pixels,
            )
        )
    return fps, means


def _fps(path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    stream = json.loads(probe)["streams"][0]
    num, _, den = stream["r_frame_rate"].partition("/")
    return float(num) / float(den or 1)


def first_frame_timestamp(path: Path) -> float | None:
    """The recorder's first-frame wall clock, when it is available.

    `-write-first-frame-ts yes` (gpu-screen-recorder 6.0.1) writes a
    ``<output>.ts`` sidecar TSV — monotonic_microsec → realtime_microsec of
    the first captured frame — not an mp4 tag. None → interval-only method.
    """
    sidecar = Path(f"{path}.ts")
    try:
        lines = sidecar.read_text().splitlines()
    except OSError:
        return None
    if len(lines) < 2:
        return None
    try:
        return int(lines[1].split()[1]) / 1_000_000
    except IndexError, ValueError:
        return None


def flip_frames(path: Path, before: tuple[int, int, int], after: tuple[int, int, int]) -> list[int]:
    """Frame indices where the screen mean settles on `after`, one per flip.

    A frame counts when it is closer to `after` than to `before` and the next
    two frames agree — the settle requirement rides out the compositor's
    crossfade (Omarchy animates the wallpaper transition; the webviews cut).
    The reported flips are the rising edges of that settled state (the first
    frame of each run); the baseline at frame 0 is not a flip — a video that
    starts already settled on `after` reports no flip until it leaves and
    returns.
    """
    fps, means = _frame_means(path)
    if len(means) < 3:
        raise RuntimeError(f"recording {path} decoded to {len(means)} frames")

    def closer(target, pixel) -> int:
        return sum(abs(a - b) for a, b in zip(target, pixel, strict=True))

    flips = []
    settled = False
    for i in range(len(means) - 2):
        on = (
            closer(after, means[i]) < closer(before, means[i])
            and closer(after, means[i + 1]) < closer(before, means[i + 1])
            and closer(after, means[i + 2]) < closer(before, means[i + 2])
        )
        if on and not settled and i > 0:
            flips.append(i)
        settled = on
    return flips
