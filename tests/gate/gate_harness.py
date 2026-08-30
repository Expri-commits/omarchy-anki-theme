"""Tier-2 gate harness (ticket 22, docs/verification.md §Tier 2).

Drives one real Anki on a dedicated scratch base through the whole live leg:
launch (seeded base, dev-linked payload, the gate control add-on), command
round-trips, live Omarchy theme switches with timing capture, and window
screenshots via grim (the ticket-09 method: focus, then capture the client
geometry — the composited pixels are exactly what rendered).

Fixture hygiene is the harness's contract: the original theme is restored and
the scratch base removed on teardown, unless the run passed --no-restore
(escape hatch for debugging a failed run). Every probe result, applied record
and screenshot is kept under tests/gate/artifacts/<run>/ for tier 3 to build
on — that path is gitignored.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import signal
import subprocess
import threading
import time

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
PAYLOAD = REPO / "payload" / "ankiya"
GATE_DIR = REPO / "tests" / "gate"
DATA_DIR = GATE_DIR / "data"
ARTIFACTS = GATE_DIR / "artifacts"
FIXTURE_THEMES = REPO / "tests" / "fixtures" / "themes"
SYSTEM_THEMES = pathlib.Path("/usr/share/omarchy/themes")

STATE_DIR = pathlib.Path.home() / ".local/state/omarchy/current"
THEME_NAME_FILE = STATE_DIR / "theme.name"
APPLIED_LOG = pathlib.Path.home() / ".local/state/omarchy/anki-theme/applied.jsonl"

STARTUP_TIMEOUT_S = 240.0  # scratch-base first run builds Qt caches slowly
COMMAND_TIMEOUT_S = 60.0
SWITCH_TIMEOUT_S = 15.0
PAINT_SETTLE_S = 1.2  # webview repaint after an apply, before capture

# Path setup (payload/, tests/, vendored PIL) lives in tests/gate/conftest.py.
from smoke_live_switch import seed_base  # noqa: E402

# The live leg's palettes (docs/verification.md): Catppuccin dark, Latte
# light, and Gruvbox as the same-polarity switch target.
GATE_THEMES = ("catppuccin", "catppuccin-latte", "gruvbox")


def to_display_name(dir_name: str) -> str:
    return " ".join(part.capitalize() for part in dir_name.split("-"))


def anki_version() -> str:
    import anki.buildinfo

    return anki.buildinfo.version


def load_sample_map(version: str) -> dict:
    map_file = DATA_DIR / version / "sample_points.json"
    if not map_file.exists():
        raise RuntimeError(
            f"no sample-point map for Anki {version} at {map_file} — characterize and "
            "add it (docs/verification.md infrastructure notes)"
        )
    return json.loads(map_file.read_text())


class GateSession:
    """One Anki process, one scratch base, three asserted theme switches
    (a decoupling flip at launch may add one more)."""

    def __init__(self, no_restore: bool) -> None:
        self.no_restore = no_restore
        self.run_dir = ARTIFACTS / time.strftime("%Y%m%d-%H%M%S")
        self.scratch = self.run_dir / "scratch"
        self.base = self.scratch / "base"
        self.ctl = self.scratch / "ctl"
        self.anki: subprocess.Popen | None = None
        self.anki_log = self.run_dir / "anki.log"
        # None until launch() opens it — teardown must survive preflight and
        # early-launch failures (the fixture's finally always calls it).
        self._anki_log_handle = None
        self.original_theme = ""
        self.t_launch = 0.0
        self._cmd_seq = 0
        self._shot_seq = 0
        self.map = load_sample_map(anki_version())
        self.startup_record: dict | None = None

    # -- preflight -----------------------------------------------------------

    def preflight(self) -> None:
        if subprocess.run(["pgrep", "-x", "anki"], capture_output=True).returncode == 0:
            raise RuntimeError("an Anki instance is already running — close it first")
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            raise RuntimeError("not inside a Hyprland session — the gate captures windows live")
        for tool in ("grim", "hyprctl", "omarchy"):
            if shutil.which(tool) is None:
                raise RuntimeError(f"required tool {tool!r} not on PATH")
        for theme in GATE_THEMES:
            system = SYSTEM_THEMES / theme / "colors.toml"
            fixture = FIXTURE_THEMES / theme / "colors.toml"
            if not system.exists():
                raise RuntimeError(f"stock theme {theme!r} missing at {system}")
            if system.read_text() != fixture.read_text():
                raise RuntimeError(
                    f"vendored fixture for {theme!r} drifted from {system} — re-vendor it "
                    "(tests/fixtures/themes/README.md) before oracles mean anything"
                )
        self.original_theme = THEME_NAME_FILE.read_text().strip()
        print(f"gate: original theme {self.original_theme!r}, Anki {anki_version()}")

    # -- launch / teardown ---------------------------------------------------

    def launch(self) -> None:
        self.run_dir.mkdir(parents=True)
        self.base.mkdir(parents=True)
        seed_base(self.base)
        addons = self.base / "addons21"
        addons.mkdir()
        # Dev-link the payload (the smoke pattern) and pin the bootloader's
        # bundled dir to the same tree, so the sync check lands on "current"
        # and never swaps the link out from under the run.
        (addons / "ankiya").symlink_to(PAYLOAD)
        shutil.copytree(
            GATE_DIR / "gate_addon",
            addons / "zz_gate_control",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        self.ctl.mkdir()
        env = dict(
            os.environ,
            ANKIYA_BUNDLED_PAYLOAD=str(PAYLOAD),
            GATE_CTL_DIR=str(self.ctl),
        )
        self._anki_log_handle = self.anki_log.open("w")
        self.anki = subprocess.Popen(
            ["anki", "-b", str(self.base)],
            stdout=self._anki_log_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
        self.t_launch = time.time()
        hello = self.cmd("hello", timeout=STARTUP_TIMEOUT_S)
        if not hello.get("ok"):
            raise RuntimeError(f"gate add-on never answered hello: {hello}")
        if hello.get("version") != anki_version():
            raise RuntimeError(f"hello version {hello.get('version')!r} != {anki_version()!r}")
        self.startup_record = self.wait_applied(
            lambda r: r["reason"] == "startup" and r["applied_at"] > self.t_launch,
            STARTUP_TIMEOUT_S,
            "the scratch instance's startup apply",
        )
        # The review fixture: Gate deck + one new card, before any navigation.
        self.seed = self.cmd("seed")
        # The first gate switch must change the palette or the digest guard
        # legitimately skips the apply (no record, no live switch). Move off
        # Catppuccin first when the session started on it.
        if self.startup_record["theme"] == "catppuccin":
            print("gate: session started on Catppuccin — decoupling via one Latte flip")
            self.switch("catppuccin-latte")
        print(
            f"gate: Anki up (profile {hello.get('profile')!r}), "
            f"startup apply {self.startup_record['apply_ms']}ms"
        )

    def teardown(self) -> None:
        if self.anki is not None and self.anki.poll() is None:
            self.anki.send_signal(signal.SIGTERM)
            try:
                self.anki.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.anki.kill()
                self.anki.wait()
        if self._anki_log_handle is not None:
            self._anki_log_handle.close()
        if self.no_restore:
            left = THEME_NAME_FILE.read_text().strip()
            print(f"gate: --no-restore — theme left at {left!r}, scratch kept at {self.scratch}")
            return
        if not self.original_theme:  # preflight never captured it — nothing to restore
            print("gate: no original theme recorded (launch failed early) — skipping restore")
            return
        subprocess.run(
            ["omarchy", "theme", "set", self.original_theme],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(self.scratch, ignore_errors=True)
        print(f"gate: restored {self.original_theme!r}, removed {self.scratch}")

    def _fail_with_log(self, message: str) -> RuntimeError:
        tail = ""
        if self.anki_log.exists():
            tail = "\n".join(self.anki_log.read_text().splitlines()[-30:])
        return RuntimeError(f"{message}\n--- anki.log tail ---\n{tail}")

    def _check_alive(self) -> None:
        assert self.anki is not None
        if self.anki.poll() is not None:
            raise self._fail_with_log(f"Anki exited with {self.anki.returncode} mid-run")

    # -- control channel -----------------------------------------------------

    def cmd(self, name: str, args: dict | None = None, timeout: float = COMMAND_TIMEOUT_S):
        self._cmd_seq += 1
        stem = f"{self._cmd_seq:03d}-{name}"
        cmd_file = self.ctl / f"{stem}.cmd"
        done_file = self.ctl / f"{stem}.done"
        staging = self.ctl / f"{stem}.cmd.tmp"
        staging.write_text(json.dumps({"cmd": name, **(args or {})}))
        os.replace(staging, cmd_file)  # the 100 ms poller must never see a partial file
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if done_file.exists():
                result = json.loads(done_file.read_text())
                done_file.unlink()
                (self.run_dir / f"{stem}.done.json").write_text(json.dumps(result, indent=1))
                if not result.get("ok"):
                    raise self._fail_with_log(f"gate command {name!r} failed: {result}")
                return result
            self._check_alive()
            time.sleep(0.05)
        raise TimeoutError(f"gate command {name!r} got no reply within {timeout:.0f}s")

    def probe(self, surface: str, overrides: dict | None = None):
        """Run the versioned DOM probe for a surface and stash its result.

        The sample map names probe scripts (``js``/``bottom_js``) as files
        next to it under data/<anki-version>/; overrides swap any spec field,
        e.g. a dump script while characterizing.
        """
        probe_spec = dict(self.map["probes"][surface])
        probe_spec.update(overrides or {})
        for key in ("js", "bottom_js"):
            if key in probe_spec:
                probe_spec[key] = (DATA_DIR / anki_version() / probe_spec[key]).read_text()
        result = self.cmd("probe", probe_spec)
        (self.run_dir / f"probe-{surface}.json").write_text(json.dumps(result, indent=1))
        return result

    # -- applied records -----------------------------------------------------

    def applied_records(self) -> list[dict]:
        try:
            lines = APPLIED_LOG.read_text().splitlines()
        except OSError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def wait_applied(self, predicate, timeout_s: float, what: str) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for record in reversed(self.applied_records()):
                if predicate(record):
                    return record
            self._check_alive()
            time.sleep(0.1)
        raise TimeoutError(f"no applied record for {what} within {timeout_s:.0f}s")

    def switch(self, theme_dir: str) -> tuple[dict, float, float]:
        """One live switch through the production path.

        Returns (applied record, t_swap, t_set_done). t_swap is the moment
        the state dir's theme.name first read back as the target (poll, 5 ms
        granularity) — the threshold's origin per docs/verification.md
        ("state-dir swap → applied record ≤ 250 ms"), not command invocation
        (the front half of `omarchy theme set` runs before any swap happens).
        """
        stop = threading.Event()
        swap: dict[str, float] = {}

        def watch_swap() -> None:
            while not stop.is_set():
                try:
                    if THEME_NAME_FILE.read_text().strip() == theme_dir:
                        swap["t"] = time.time()
                        return
                except OSError:
                    pass  # mid-swap: the theme dir is being rm+mv'd
                time.sleep(0.005)

        watcher = threading.Thread(target=watch_swap, daemon=True)
        watcher.start()
        t0 = time.time()
        try:
            subprocess.run(
                ["omarchy", "theme", "set", to_display_name(theme_dir)],
                capture_output=True,
                check=True,
            )
            t_set_done = time.time()
            record = self.wait_applied(
                lambda r: (
                    r["applied_at"] > self.t_launch
                    and r["reason"] in ("watcher", "watcher-retry")
                    and r["theme"] == theme_dir
                    and r["applied_at"] > t0
                ),
                SWITCH_TIMEOUT_S,
                f"the live switch to {theme_dir!r}",
            )
        finally:
            stop.set()
            watcher.join(timeout=1.0)
        # A missing "t" means theme.name read as the target from the first
        # poll — only possible on a same-theme switch (excluded by design),
        # so t0 is the conservative fallback.
        t_swap = swap.get("t", t0)
        (self.run_dir / f"switch-{theme_dir}.json").write_text(
            json.dumps(
                {**record, "t0": t0, "t_swap": t_swap, "t_set_done": t_set_done}, indent=1
            )
        )
        time.sleep(PAINT_SETTLE_S)
        return record, t_swap, t_set_done

    # -- capture -------------------------------------------------------------

    def _find_client(self, want_add: bool) -> dict | None:
        out = subprocess.run(
            ["hyprctl", "-j", "clients"], capture_output=True, text=True, check=True
        ).stdout
        for client in json.loads(out):
            if client.get("class") != "anki" or not client.get("mapped"):
                continue
            if ("Add" in (client.get("title") or "")) == want_add:
                return client
        return None

    def capture(self, target: str, label: str) -> pathlib.Path:
        """Screenshot the target's window: focus it, then grim its geometry."""
        want_add = target == "add"
        client = None
        deadline = time.monotonic() + 10.0
        while client is None and time.monotonic() < deadline:
            client = self._find_client(want_add)
            if client is None:
                time.sleep(0.3)
        if client is None:
            raise RuntimeError(f"no {'Add ' if want_add else 'main '}window to capture")
        # Hyprland ≥0.55 speaks Lua here (`dispatch X Y` is gone); and hyprctl
        # prints ok for no-op dispatches, so focus is verified by observed
        # effect (activewindow), never by exit code.
        focused = False
        for _ in range(10):
            subprocess.run(
                [
                    "hyprctl",
                    "dispatch",
                    f"hl.dsp.focus({{window = 'address:{client['address']}'}})",
                ],
                capture_output=True,
                check=True,
            )
            active = subprocess.run(
                ["hyprctl", "-j", "activewindow"], capture_output=True, text=True
            ).stdout
            try:
                focused = json.loads(active)["address"] == client["address"]
            except (ValueError, KeyError):
                focused = False
            if focused:
                break
            time.sleep(0.2)
        if not focused:
            raise RuntimeError(f"could not focus the {'Add' if want_add else 'main'} window")
        time.sleep(0.4)
        # Re-query after the focus loop: up to seconds passed since the first
        # read, and a layout shift in between would silently mis-frame grim.
        fresh = self._find_client(want_add)
        if fresh is not None and fresh["address"] == client["address"]:
            client = fresh
        at, size = client["at"], client["size"]
        self._shot_seq += 1
        path = self.run_dir / f"shot-{self._shot_seq:03d}-{target}-{label}.png"
        subprocess.run(
            ["grim", "-g", f"{at[0]},{at[1]} {size[0]}x{size[1]}", str(path)], check=True
        )
        return path

    def shot_offset(self, shot, window_size: list[int]) -> tuple[int, int]:
        """Window-content origin inside the shot (borders/title bar outside).

        Derived per capture from the two sizes themselves — symmetric left/
        right chrome, the remainder on top — so decorations never become
        magic constants. Asserted non-negative by the caller.
        """
        shot_w, shot_h = shot.size
        win_w, win_h = window_size
        dx = (shot_w - win_w) // 2
        dy = shot_h - win_h - dx
        if dy < 0:
            dy = (shot_h - win_h) // 2
        return dx, dy
