"""Bench "meta review" pieces: whole-display recording, the meta narration and the meta video.

* :class:`DisplayCapture` records the whole display (not a window) with the OS
  grabber ffmpeg offers - ``gdigrab -i desktop`` on Windows, ``avfoundation -i
  "Capture screen N:none"`` on macOS, ``x11grab -i $DISPLAY.N`` on Linux (``:0.0``
  when ``DISPLAY`` is unset) - at 15 fps,
  and stops by writing ``q`` to ffmpeg's stdin (same plumbing as
  ``capture.ScreenCapture``, minus the window bounds).  :func:`build_argv` is
  the pure argv builder.
* :func:`meta_narration` turns the bench's per-driver rows (``bench.meta_view`` of
  ``bench.json``: ``rows`` + ``scenario {name, slug}``; the standalone ``bench-meta``
  command applies the same view to the file it reads) plus optional baseline rows
  into a narration dict in the kit's ``narration.json`` shape (``intro``, one
  ``steps`` entry per driver, ``outro``) with the numbers spoken naturally.
* :func:`build_meta_video` synthesises those segments with ``demo_smoke.tts``,
  concatenates the screen recordings (:mod:`demo_smoke.ffmpeg_concat`), places
  the segments one after another with a short gap (padding the picture with
  its last frame when the narration is longer), normalises loudness with the
  same chain ``edit.build`` uses and muxes H.264/AAC.
* ``bench-meta`` (``register``) builds the meta video for an existing bench
  directory; the ``bench`` command's ``--meta-narrate`` calls the same functions.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

from . import ffmpeg_util as ff
from .capture import CaptureError, find_ffmpeg
from .edit import FPS, SAMPLE_RATE, audio_chain
from .ffmpeg_concat import concat_videos, encode_timeout

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 3
EXIT_BAD_INPUT = 4
EXIT_INTERRUPTED = 130

DISPLAY_FPS = 15
GAP_SECONDS = 0.5          # silence between two narration segments
TAIL_SECONDS = 0.5         # picture kept after the last segment ends
STARTUP_WAIT = 0.5         # seconds before checking that the grabber is still alive
STOP_TIMEOUT = 10.0        # seconds to wait for ffmpeg to finish after 'q'
TTS_CHOICES = ("auto", "turbo", "nano", "classic", "tone")
INTRO_ID = "intro"
OUTRO_ID = "outro"
CLIPS_SUBDIR = "clips"

_SCALE_EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
_ENCODE = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart"]


class MetaError(RuntimeError):
    """Expected failure while building the meta narration/video (one-line message)."""


# --------------------------------------------------------------------------- display capture


def normalize_os(os_name: str | None) -> str:
    """``sys.platform`` / ``platform.system()`` spellings -> ``win32`` | ``darwin`` | ``linux``."""
    s = (os_name or sys.platform or "").strip().lower()
    if s.startswith("win") or s == "nt" or s == "cygwin":
        return "win32"
    if s in ("darwin", "macos", "mac", "osx") or s.startswith("mac"):
        return "darwin"
    return "linux"


def build_argv(os_name: str, out_path: str | Path, fps: int = DISPLAY_FPS, display_index: int = 0,
               display: str | None = None) -> list[str]:
    """ffmpeg arguments (without the executable) that record the whole display to ``out_path``.

    ``os_name`` is ``win32`` / ``darwin`` / ``linux`` (``platform.system()`` names
    are accepted too).  ``display_index`` is the macOS display number ("Capture
    screen N") or the X screen number (``:0.N``); gdigrab always records the
    whole virtual desktop.  ``display`` overrides ``$DISPLAY`` on Linux.
    The frame size is forced even so libx264 never rejects an odd screen.
    """
    fps = int(fps) if int(fps) > 0 else DISPLAY_FPS
    idx = int(display_index or 0)
    head = ["-hide_banner", "-loglevel", "warning", "-y"]
    tail = ["-vf", _SCALE_EVEN, *_ENCODE, str(out_path)]
    kind = normalize_os(os_name)
    if kind == "win32":
        return head + ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1",
                       "-i", "desktop"] + tail
    if kind == "darwin":
        return head + ["-f", "avfoundation", "-framerate", str(fps), "-capture_cursor", "1",
                       "-i", f"Capture screen {idx}:none"] + tail
    disp = display or os.environ.get("DISPLAY") or ":0"
    host, _, num = disp.rpartition(":")
    num = num.split(".")[0] or "0"
    return head + ["-f", "x11grab", "-framerate", str(fps), "-draw_mouse", "1",
                   "-i", f"{host}:{num}.{idx}"] + tail


class DisplayCapture:
    """Whole-display recording via ffmpeg; ``start() -> t0``, ``now()``, ``stop() -> Path``, ``abort()``.

    ``ffmpeg`` defaults to the kit's discovery (``DEMO_SMOKE_FFMPEG`` -> PATH ->
    imageio-ffmpeg); tests pass a fake.  ``display_index`` defaults to
    ``DEMO_SMOKE_SCREEN_INDEX``.  ``t_stop`` is the capture length in seconds.
    """

    def __init__(self, out_path: str | Path, fps: int = DISPLAY_FPS, display_index: int | None = None,
                 ffmpeg: str | None = None, os_name: str | None = None, log_path: str | Path | None = None,
                 startup_wait: float = STARTUP_WAIT, stop_timeout: float = STOP_TIMEOUT):
        self.path = Path(out_path)
        self.fps = int(fps)
        if display_index is None:
            display_index = int(os.environ.get("DEMO_SMOKE_SCREEN_INDEX", "0") or 0)
        self.display_index = int(display_index)
        self.ffmpeg = ffmpeg
        self.os_name = normalize_os(os_name or platform.system())
        self.log_path = Path(log_path) if log_path else self.path.with_name(self.path.stem + "-ffmpeg.log")
        self.startup_wait = float(startup_wait)
        self.stop_timeout = float(stop_timeout)
        self.note = ""
        self.t0: float | None = None
        self.t_stop: float | None = None
        self.capture_start_epoch: float | None = None
        self._proc: subprocess.Popen | None = None
        self._log = None
        self._stopped = False

    def args(self) -> list[str]:
        exe = self.ffmpeg or find_ffmpeg()
        return [exe, *build_argv(self.os_name, self.path, self.fps, self.display_index)]

    def start(self) -> float:
        if self._proc is not None:
            return self.t0
        if self.os_name == "linux" and os.environ.get("WAYLAND_DISPLAY"):
            self.note = ("Wayland session detected: x11grab only sees XWayland surfaces; "
                         "native Wayland windows record black")
            log.warning(self.note)
        argv = self.args()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "wb")  # noqa: SIM115 - handed to Popen, closed in stop()
        try:
            self._log.write((" ".join(argv) + "\n").encode("utf-8", "replace"))
            self._log.flush()
            self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=self._log, stderr=subprocess.STDOUT)
        except OSError as exc:
            self._log.close()
            raise CaptureError(f"could not start ffmpeg display grabber: {exc}") from None
        self.t0 = time.monotonic()
        self.capture_start_epoch = time.time()
        if self.startup_wait > 0:
            time.sleep(self.startup_wait)
        if self._proc.poll() is not None:
            self._close_stdin(self._proc)
            self._log.close()
            raise CaptureError(f"ffmpeg display grabber exited immediately: {self._log_tail()}")
        return self.t0

    @staticmethod
    def _close_stdin(proc: subprocess.Popen) -> None:
        """Close the child's stdin pipe (never raises); a dead child's pipe fd leaks otherwise."""
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            log.debug("ignored error", exc_info=True)

    def now(self) -> float:
        if self.t0 is None:
            return 0.0
        return time.monotonic() - self.t0

    def stop(self) -> Path:
        if self._stopped:
            return self.path
        self._stopped = True
        if self.t0 is not None:
            self.t_stop = time.monotonic() - self.t0
        proc = self._proc
        if proc is None:
            raise CaptureError("display capture was never started")
        try:
            if proc.stdin:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:
            log.debug("ignored error", exc_info=True)
        try:
            proc.wait(timeout=self.stop_timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if self._log:
            self._log.close()
        if proc.returncode not in (0, 255) or not self.path.exists():
            raise CaptureError(f"ffmpeg display grabber failed (exit {proc.returncode}): {self._log_tail()}")
        return self.path

    def abort(self) -> None:
        """Stop the grabber on the error path; never raises."""
        if self._stopped:
            return
        self._stopped = True
        if self.t0 is not None:
            self.t_stop = time.monotonic() - self.t0
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.write(b"q\n")
                    proc.stdin.flush()
                    proc.stdin.close()
                proc.wait(timeout=3)
            except Exception:
                log.debug("ignored error", exc_info=True)
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    log.debug("ignored error", exc_info=True)
        if proc is not None:
            self._close_stdin(proc)                  # a child that already died still holds the pipe open
        if self._log and not self._log.closed:
            try:
                self._log.close()
            except Exception:
                log.debug("ignored error", exc_info=True)

    def _log_tail(self) -> str:
        try:
            lines = [ln for ln in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                     if ln.strip()]
            return lines[-1] if lines else "no output"
        except OSError:
            return "no log"


def meta_clip_path(bench_dir: str | Path, index: int, driver_slug: str, repeat: int = 1) -> Path:
    """Where ``bench --record-screen`` stores the display recording of one driver run:
    ``<bench>/meta/clips/NN-<driver-slug>-rN.mp4`` (sorted order = run order)."""
    return Path(bench_dir) / "meta" / CLIPS_SUBDIR / f"{int(index):02d}-{driver_slug}-r{int(repeat)}.mp4"


# --------------------------------------------------------------------------- spoken numbers


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def spoken_number(n) -> str:
    """0-10 as words, larger values as digits ("four", "12"); rounds floats."""
    if n is None:
        return "no"
    try:
        v = round(float(n))
    except (TypeError, ValueError):
        return str(n)
    if 0 <= v <= 10:
        return _ONES[v]
    return str(v)


def spoken_minutes(minutes) -> str:
    """'under a minute' / '45 seconds' / 'about a minute' / 'four minutes' / 'about 12 minutes'."""
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return "an unknown time"
    if m < 0:
        m = 0.0
    if m < 0.75:
        secs = round(m * 60)
        if secs < 5:
            return "a few seconds"
        return f"{spoken_number(secs)} seconds"
    whole = round(m)
    approx = abs(m - whole) > 0.05
    if whole == 1:
        return "about a minute" if approx else "one minute"
    words = f"{spoken_number(whole)} minutes"
    return f"about {words}" if approx else words


def _is_approx(n) -> bool:
    """A mean that is not a whole number is spoken as 'about N' (the table shows the exact mean)."""
    try:
        f = float(n)
    except (TypeError, ValueError):
        return False
    return abs(f - round(f)) > 0.05


def spoken_count(n) -> str:
    """``spoken_number`` with 'about' in front when ``n`` is a fractional mean ('about eight', '12')."""
    return f"about {spoken_number(n)}" if _is_approx(n) else spoken_number(n)


def spoken_tool_calls(n) -> str:
    """'no tool calls' / 'one tool call' / '12 tool calls' / 'about 12 tool calls' for a fractional mean
    (None, lists and {'count'} accepted)."""
    if isinstance(n, dict):
        n = n.get("count", n.get("n"))
    if isinstance(n, (list, tuple)):
        n = len(n)
    if n is None:
        return "no tool calls"
    try:
        v = round(float(n))
    except (TypeError, ValueError):
        return "no tool calls"
    if v <= 0:
        return "no tool calls"
    if v == 1 and not _is_approx(n):
        return "one tool call"
    about = "about " if _is_approx(n) else ""
    return f"{about}{spoken_number(v)} tool call{'s' if v != 1 else ''}"


def spoken_list(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# --------------------------------------------------------------------------- bench rows


def driver_slug(driver: str, model: str | None = None) -> str:
    """``opencode:lmstudio/qwen3@http://...`` -> ``opencode-lmstudio-qwen3`` (same idea as bench run dirs)."""
    text = str(driver or "driver")
    text = text.split("@", 1)[0]
    if model and model not in text and text.split(":", 1)[0] in ("manual",):
        text = f"{text}-{model}"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug or "driver"


def _spoken_model(model: str | None) -> str:
    m = str(model or "").strip()
    m = m.split("@", 1)[0]
    m = m.replace("/", " ").replace("_", " ")
    return " ".join(m.split())


def spoken_driver(driver: str, model: str | None = None) -> str:
    """How a driver is named in the narration."""
    d = str(driver or "").strip()
    kind, _, rest = d.partition(":")
    kind = kind.lower()
    if kind == "template":
        return "the template driver, with no model at all"
    if kind == "llm":
        name = _spoken_model(model) or _spoken_model(rest.split("|")[-1])
        return f"the LLM narration driver with {name}" if name else "the LLM narration driver"
    if kind == "opencode":
        name = _spoken_model(model) or _spoken_model(rest)
        return f"OpenCode with {name}" if name else "OpenCode"
    if kind == "manual":
        name = _spoken_model(model)
        return f"a manual run with {name}" if name else "a manual run"
    name = _spoken_model(model)
    return f"{d} with {name}" if name else (d or "an unknown driver")


def _first(row: dict, *keys, default=None):
    for k in keys:
        if isinstance(row, dict) and row.get(k) is not None:
            return row[k]
    return default


def row_minutes(row: dict) -> float | None:
    """Total minutes of a bench/baseline row (``total_minutes``, ``minutes``, ``wall_s``, or the mean)."""
    for src in (row, row.get("mean") if isinstance(row.get("mean"), dict) else None):
        if not src:
            continue
        v = _first(src, "total_minutes", "minutes")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
        v = _first(src, "wall_s", "seconds", "wall_seconds")
        if v is not None:
            try:
                return float(v) / 60.0
            except (TypeError, ValueError):
                pass
    runs = row.get("runs")
    if isinstance(runs, list) and runs:
        vals = [row_minutes(r) for r in runs if isinstance(r, dict)]
        vals = [v for v in vals if v is not None]
        if vals:
            return sum(vals) / len(vals)
    return None


def row_tool_calls(row: dict):
    tc = _first(row, "tool_calls", "tool_call_count")
    if tc is None and isinstance(row.get("mean"), dict):
        tc = _first(row["mean"], "tool_calls", "tool_call_count")
    if tc is None and isinstance(row.get("opencode"), dict):
        tc = _first(row["opencode"], "tool_calls", "tool_call_count")
    return tc


def row_kit_calls(row: dict):
    kit = _first(row, "kit_tool_calls", "kit_calls")
    if kit is None and isinstance(row.get("opencode"), dict):
        kit = _first(row["opencode"], "kit_tool_calls")
    return kit


def _result_rows(value) -> list[dict]:
    """``value`` as a list of result rows: dicts that name a ``driver`` (a ``{name: row}`` mapping is
    unrolled).  The bench's ``drivers`` key is the *spec* list (``kind``/``spec``/``slug``, no ``driver``),
    so it never qualifies."""
    if isinstance(value, dict):
        value = [{"driver": k, **(v if isinstance(v, dict) else {})} for k, v in value.items()]
    if not isinstance(value, list) or not value:
        return []
    rows = [r for r in value if isinstance(r, dict)]
    return rows if rows and all("driver" in r for r in rows) else []


def bench_rows(bench_json) -> list[dict]:
    """The per-driver rows of ``bench.json``: ``rows`` first (what the bench writes), then ``results``,
    ``runs`` or ``drivers`` when their entries are result rows (carry a ``driver``), or a bare list."""
    if isinstance(bench_json, list):
        return [r for r in bench_json if isinstance(r, dict)]
    if not isinstance(bench_json, dict):
        return []
    for key in ("rows", "results", "runs", "drivers"):
        rows = _result_rows(bench_json.get(key))
        if rows:
            return rows
    return []


def _is_manual(row: dict) -> bool:
    return bool(row.get("manual")) or str(row.get("driver", "")).lower().startswith("manual")


_PASS_K_OF_N = re.compile(r"^PASS\s+(\d+)\s*/\s*(\d+)")


def _spoken_count(k: int, n: int) -> str:
    """'passed two of three runs' pieces: 'both runs', 'all four runs', 'none of the three runs', 'two of three runs'."""
    if k == n:
        return "both runs" if n == 2 else f"all {spoken_number(n)} runs"
    if k == 0:
        return f"none of the {spoken_number(n)} runs"
    return f"{spoken_number(k)} of {spoken_number(n)} runs"


def _verdict_phrase(row: dict) -> str:
    verdict = str(_first(row, "verdict", default="") or "").upper()
    where = _first(row, "failing_stage", "failed_stage", "stage")
    step = _first(row, "failing_step", "failed_step")
    verdicts = row.get("verdicts")
    if isinstance(verdicts, list) and len(verdicts) > 1:
        vs = [str(v or "ERROR").upper() for v in verdicts]
        n_pass = vs.count("PASS")
        if n_pass == len(vs):
            return f"passed {_spoken_count(n_pass, len(vs))}"
        if n_pass:
            return f"passed {_spoken_count(n_pass, len(vs))}"
        kinds = sorted({v for v in vs})
        if kinds == ["FAIL"]:
            return f"failed {_spoken_count(len(vs), len(vs))}"
        if kinds == ["ERROR"]:
            return f"hit an error on {_spoken_count(len(vs), len(vs))}"
        return f"passed {_spoken_count(0, len(vs))}, failing on {spoken_number(vs.count('FAIL'))} " \
               f"and erroring on {spoken_number(vs.count('ERROR'))}"
    m = _PASS_K_OF_N.match(verdict)
    if m:
        return f"passed {_spoken_count(int(m.group(1)), int(m.group(2)))}"
    if verdict == "PASS":
        return "passed"
    if verdict == "FAIL":
        return f"failed at the {step} step" if step else "failed"
    if verdict == "ERROR":
        return f"hit an error in the {where} stage" if where else "hit an error"
    return f"ended with {verdict.lower()}" if verdict else "ended without a verdict"


def _source_phrase(source) -> str:
    s = str(source or "").strip().lower()
    if not s or s == "none":
        return ""
    # the llm driver talks to any OpenAI-compatible endpoint, local or hosted: do not call it local
    mapping = {"template": "the scenario template", "llm": "the LLM endpoint",
               "agent": "the agent itself", "cloud model": "the cloud model"}
    return mapping.get(s, s if s.startswith("the ") else f"the {s}")


def _row_runs(row: dict) -> int:
    """How many runs a row aggregates: ``runs`` as an int (the bench's rows) or a list (per-run dicts)."""
    runs = row.get("runs")
    if isinstance(runs, bool):
        return 1
    if isinstance(runs, int):
        return max(1, runs)
    if isinstance(runs, list):
        return max(1, len(runs))
    verdicts = row.get("verdicts")
    if isinstance(verdicts, list) and verdicts:
        return len(verdicts)
    try:
        return max(1, int(_first(row, "repeat", "n_runs", default=1) or 1))
    except (TypeError, ValueError):
        return 1


def _sources_phrase(row: dict) -> str:
    """'the agent itself' / 'the agent itself on two runs and the scenario template on one'."""
    counts = row.get("narration_sources")
    if isinstance(counts, dict) and counts:
        items = [(str(k), v) for k, v in counts.items() if _source_phrase(k)]
        if len(items) == 1:
            return _source_phrase(items[0][0])
        parts = []
        for name, n in items:
            try:
                n = int(n)
            except (TypeError, ValueError):
                n = 0
            parts.append(f"{_source_phrase(name)} on {spoken_number(n)} run{'s' if n != 1 else ''}")
        return spoken_list(parts)
    raw = _first(row, "narration_source", "narration")
    names = [x for x in str(raw or "").split("/") if x.strip()]
    return spoken_list([_source_phrase(x) for x in names if _source_phrase(x)])


def _driver_segment(row: dict) -> str:
    driver = str(row.get("driver", ""))
    model = row.get("model")
    who = spoken_driver(driver, model)
    minutes = row_minutes(row)
    n_runs = _row_runs(row)
    if n_runs > 1:
        lead = f"Averaged over {spoken_number(n_runs)} runs under {who}, "
        pass_minutes = row.get("pass_minutes")
        if isinstance(pass_minutes, (int, float)) and not isinstance(pass_minutes, bool):
            passed = row.get("passed_runs")
            which = "a passing run" if isinstance(passed, int) and 0 < passed < n_runs else "a run"
            took = f"{which} took {spoken_minutes(pass_minutes)}"
        elif minutes is not None:
            took = f"a run took {spoken_minutes(minutes)}"
        else:
            took = "the run time was not recorded"
    else:
        lead = f"Under {who}, "
        took = f"the run took {spoken_minutes(minutes)}" if minutes is not None else "the run time was not recorded"
    kind = driver.partition(":")[0].lower()
    tc = row_tool_calls(row)
    kit = row_kit_calls(row)
    if kind == "opencode" or tc is not None:
        calls = f", used {spoken_tool_calls(tc)}"
        if kind == "opencode" and tc is not None:
            calls += " including file reads"
            if kit is not None:
                calls += f", {spoken_count(kit)} of them kit commands"
    else:
        calls = ", made no tool calls"
    sentence = f"{lead}{took}{calls} and {_verdict_phrase(row)}."
    src = _sources_phrase(row)
    if src:
        sentence += f" The narration came from {src}."
    fallback = _first(row, "fallback", "fell_back")
    if fallback:
        sentence += " It fell back to the template narration."
    return " ".join(sentence.split())


def _passing_minutes(row: dict) -> float | None:
    """Minutes of a row's passing runs: ``pass_minutes`` (mean over the PASS repeats) when the bench
    wrote it, else the row's minutes when its verdict is PASS (or ``PASS k/n`` with k > 0)."""
    v = row.get("pass_minutes")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    verdicts = row.get("verdicts")
    if isinstance(verdicts, list) and verdicts:
        passed = any(str(x or "").upper() == "PASS" for x in verdicts)
    else:
        label = str(row.get("verdict", "") or "").upper()
        m = _PASS_K_OF_N.match(label)
        passed = label == "PASS" or (m is not None and int(m.group(1)) > 0)
    return row_minutes(row) if passed else None


def _is_template(row: dict) -> bool:
    """The template driver runs no model: the pipeline-only baseline, never the 'fastest driver'."""
    return row.get("kind") == "template" or str(row.get("driver", "")).partition(":")[0].strip().lower() == "template"


def _fastest_sentence(fastest: dict | None, template: dict | None, with_baseline: bool) -> str:
    """The closing comparison: the fastest passing *model* driver, the template quoted apart."""
    parts = []
    if fastest is not None:
        who = spoken_driver(str(fastest.get("driver", "")), fastest.get("model"))
        took = spoken_minutes(_passing_minutes(fastest))
        parts.append(f"The fastest passing model driver here, {who}, took {took}." if with_baseline
                     else f"The fastest passing model driver was {who} at {took}.")
    elif template is not None:
        parts.append("No model driver passed this time.")
    if template is not None:
        parts.append(f"The template driver, the pipeline on its own with no model, took "
                     f"{spoken_minutes(_passing_minutes(template))}.")
    return " ".join(parts)


def meta_narration(bench_json, baseline=None) -> dict:
    """Narration dict (``intro``, ``steps`` [{id, text}], ``outro``) describing a bench run.

    ``bench_json`` is the bench's aggregate JSON (or its rows); ``baseline`` the
    list from ``--baseline`` (manual rows already merged into ``bench_json`` are
    used when ``baseline`` is None); every baseline entry is spoken.  Numbers are
    spoken naturally ("four minutes", "12 tool calls", "about eight tool calls"
    for a fractional mean).  The template driver gets its own segment but is
    never the "fastest driver": it runs no model.
    """
    rows = bench_rows(bench_json)
    auto = [r for r in rows if not _is_manual(r)]
    manual = list(baseline) if isinstance(baseline, list) else [r for r in rows if _is_manual(r)]
    manual = [m for m in manual if isinstance(m, dict)]
    scen = bench_json.get("scenario") if isinstance(bench_json, dict) else None
    scen_name = None
    if isinstance(scen, dict):
        scen_name = scen.get("name") or scen.get("slug")
    elif isinstance(scen, str):
        scen_name = scen
    scen_name = scen_name or (bench_json.get("scenario_name") if isinstance(bench_json, dict) else None)

    names = [spoken_driver(r.get("driver", ""), r.get("model")) for r in auto]
    if not auto:
        intro = "This is the smoke kit running itself, but no driver finished, so there is nothing to compare."
    elif len(auto) == 1:
        intro = f"This is the smoke kit running itself under {names[0]}."
    else:
        intro = (f"This is the smoke kit running itself under {spoken_number(len(auto))} drivers: "
                 f"{spoken_list(names)}.")
    if scen_name:
        intro += f" The scenario is {scen_name}, the same one every time."

    steps = []
    seen: set[str] = set()
    for i, r in enumerate(auto):
        sid = str(r.get("slug") or driver_slug(str(r.get("driver", f"driver-{i + 1}")), r.get("model")))
        base, n = sid, 2
        while sid in seen or sid in (INTRO_ID, OUTRO_ID):
            sid = f"{base}-{n}"
            n += 1
        seen.add(sid)
        steps.append({"id": sid, "text": _driver_segment(r)})

    passed = [r for r in auto if _passing_minutes(r) is not None]
    model_passed = [r for r in passed if not _is_template(r)]
    template_passed = [r for r in passed if _is_template(r)]
    fastest = min(model_passed, key=_passing_minutes) if model_passed else None
    template = min(template_passed, key=_passing_minutes) if template_passed else None
    if manual:
        parts = []
        for m in manual:
            who = spoken_driver(str(m.get("driver") or "manual"), m.get("model"))
            when = f" on {m['date']}" if m.get("date") else ""
            mins = row_minutes(m)
            took = spoken_minutes(mins) if mins is not None else "an unrecorded time"
            parts.append(f"{who}{when} took {took} and {_verdict_phrase(m)}")
        outro = "For comparison, " + spoken_list(parts) + "."
        tail = _fastest_sentence(fastest, template, with_baseline=True)
        if tail:
            outro += " " + tail
    elif passed:
        outro = ("No manual baseline was given. " + _fastest_sentence(fastest, template, with_baseline=False)
                 + " Every number is in the bench report.")
    else:
        outro = "No driver passed this time. The details are in the bench report."
    return {"intro": " ".join(intro.split()), "outro": " ".join(outro.split()), "steps": steps}


# --------------------------------------------------------------------------- meta video


def plan_meta(segments: list[tuple[str, float]], video_seconds: float, gap: float = GAP_SECONDS,
              tail: float = TAIL_SECONDS, clip_starts: list[float] | None = None) -> dict:
    """Pure placement: segments one after another with ``gap`` s between them.

    ``segments`` are ``(id, seconds)`` in playback order (intro, drivers..., outro).
    With ``clip_starts`` (one per driver segment, i.e. ``len(segments) - 2``
    entries; ``None`` for a driver without a clip of its own) a driver's segment
    starts no earlier than its own clip.  The picture is padded with its last
    frame when the narration outlasts it: ``total = max(video_seconds, audio_end + tail)``.
    """
    placements: list[dict] = []
    t = 0.0
    n_drivers = max(0, len(segments) - 2)
    for i, (sid, secs) in enumerate(segments):
        secs = max(0.0, float(secs))
        if placements:
            t = placements[-1]["t"] + placements[-1]["duration"] + float(gap)
        if clip_starts and 1 <= i <= n_drivers and len(clip_starts) >= i and clip_starts[i - 1] is not None:
            t = max(t, float(clip_starts[i - 1]))
        placements.append({"id": sid, "t": round(t, 4), "duration": round(secs, 4)})
    audio_end = (placements[-1]["t"] + placements[-1]["duration"]) if placements else 0.0
    video_seconds = max(0.0, float(video_seconds))
    total = max(video_seconds, audio_end + float(tail))
    return {
        "audio": placements,
        "audio_end": round(audio_end, 4),
        "video_seconds": round(video_seconds, 4),
        "pad_seconds": round(max(0.0, total - video_seconds), 4),
        "total": round(total, 4),
        "gap": float(gap),
        "tail": float(tail),
    }


def narration_segments(narration: dict) -> list[tuple[str, str]]:
    """``[(id, text)]`` in playback order; empty texts are skipped."""
    segs: list[tuple[str, str]] = []
    intro = str(narration.get("intro") or "").strip()
    if intro:
        segs.append((INTRO_ID, intro))
    for st in narration.get("steps") or []:
        if not isinstance(st, dict):
            continue
        text = str(st.get("text") or "").strip()
        if text and st.get("id"):
            segs.append((str(st["id"]), text))
    outro = str(narration.get("outro") or "").strip()
    if outro:
        segs.append((OUTRO_ID, outro))
    return segs


_CLIP_NAME = re.compile(r"^\d+-(?P<slug>.+)-r\d+\.mp4$", re.IGNORECASE)


def clip_slug(name: str) -> str | None:
    """``01-opencode-lmstudio-qwen3-r2.mp4`` -> ``opencode-lmstudio-qwen3`` (the ``meta_clip_path`` layout)."""
    m = _CLIP_NAME.match(Path(str(name)).name)
    return m.group("slug") if m else None


def clip_starts_by_slug(clips: list[Path], durations: list[float], segment_ids: list[str]) -> list[float | None]:
    """Where each driver segment's own footage starts in the concatenated video.

    ``bench --record-screen`` writes one clip per *run* (``NN-<slug>-rN.mp4``) while the narration
    has one segment per *driver*, so clips are matched by slug, not by position: a segment starts
    at the first clip whose slug equals the segment id, ``None`` when no clip carries that slug.
    """
    starts: dict[str, float] = {}
    acc = 0.0
    for c, d in zip(clips, durations):
        slug = clip_slug(c.name)
        if slug is not None and slug not in starts:
            starts[slug] = acc
        acc += float(d or 0.0)
    return [starts.get(sid) for sid in segment_ids]


def video_chain_meta(pad_seconds: float) -> list[str]:
    parts = [f"fps={FPS}"]
    if pad_seconds > 0.001:
        parts.append(f"tpad=stop_mode=clone:stop_duration={pad_seconds:.3f}")
    parts.append("format=yuv420p")
    return [f"[0:v]{','.join(parts)}[vout]"]


def build_meta_video(clips: list[Path], narration: dict, out: Path, tts: str = "tone", ref=None,
                     online: bool = False, gap: float = GAP_SECONDS, tail: float = TAIL_SECONDS,
                     align_to_clips: bool = False) -> Path:
    """Render ``out`` (MP4) from the screen recordings ``clips`` and ``narration``.

    Writes next to ``out``: ``narration.json``, ``audio/seg-<id>.wav`` +
    ``audio/durations.json``, ``concat.mp4`` (the joined clips), ``meta-filter.txt``
    and ``meta-edit.json`` (plan + exact ffmpeg argv).  ``tts`` is a
    ``demo_smoke.tts`` backend (``tone`` needs no ML deps); ``ref`` the reference
    voice WAV or None.  ``align_to_clips=True`` additionally starts each
    driver's segment no earlier than that driver's own clip, matched by the slug
    in the clip name (``NN-<slug>-rN.mp4``); clips named otherwise are matched by
    position when there is exactly one per driver, and a note is logged (and kept
    in ``meta-edit.json`` as ``align_note``) when the option could not be applied.
    """
    from . import tts as tts_mod

    clips = [Path(c) for c in clips]
    if not clips:
        raise MetaError("no screen recordings to build the meta video from")
    missing = [str(c) for c in clips if not c.is_file()]
    if missing:
        raise MetaError("screen recording(s) not found: " + ", ".join(missing))
    segs = narration_segments(narration)
    if not segs:
        raise MetaError("the meta narration has no segments")
    out = Path(out)
    meta_dir = out.parent
    meta_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = meta_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "narration.json").write_text(json.dumps(narration, indent=2, ensure_ascii=False),
                                             encoding="utf-8")

    backend = tts_mod.resolve_backend(tts or "tone")
    ref_path = Path(ref) if ref else None
    durations: dict[str, float] = {}
    wavs: dict[str, Path] = {}
    for sid, text in segs:
        wav, sr = tts_mod.synthesize(text, ref_path, backend, online=online)
        p = tts_mod.write_wav(audio_dir / f"seg-{sid}.wav", wav, sr)
        durations[sid] = float(tts_mod.audio_stats(wav, sr)["duration"])
        wavs[sid] = p
    (audio_dir / "durations.json").write_text(json.dumps(durations, indent=2), encoding="utf-8")

    concat = concat_videos(clips, meta_dir / "concat.mp4")
    vinfo = ff.media_info(concat)
    clip_starts = None
    align_note = None
    if align_to_clips:
        driver_ids = [sid for sid, _ in segs][1:-1] if len(segs) > 2 else []
        clip_durs = [float(ff.media_info(c).get("duration") or 0.0) for c in clips]
        clip_starts = clip_starts_by_slug(clips, clip_durs, driver_ids)
        if driver_ids and all(s is None for s in clip_starts):
            if len(clips) == len(driver_ids):
                starts, acc = [], 0.0
                for d in clip_durs:
                    starts.append(acc)
                    acc += d
                clip_starts = starts
                align_note = "clips matched to driver segments by position (no clip name carries a driver slug)"
            else:
                clip_starts = None
                align_note = (f"--align-clips not applied: no clip name carries a driver slug and there are "
                              f"{len(clips)} clip(s) for {len(driver_ids)} driver segment(s)")
            log.warning(align_note)
    plan = plan_meta([(sid, durations[sid]) for sid, _ in segs], float(vinfo.get("duration") or 0.0),
                     gap=gap, tail=tail, clip_starts=clip_starts)

    inputs: list[Path] = [concat]
    placements: list[dict] = []
    for a in plan["audio"]:
        placements.append({"input": len(inputs), "id": a["id"], "t": a["t"], "file": str(wavs[a["id"]])})
        inputs.append(wavs[a["id"]])
    graph = [*video_chain_meta(plan["pad_seconds"]), *audio_chain(placements, plan["total"])]
    script = meta_dir / "meta-filter.txt"
    script.write_text(";\n".join(graph) + "\n", encoding="utf-8")

    args: list[str] = ["-y"]
    for p in inputs:
        args += ["-i", str(p)]
    # veryfast: the source is already crf-23 ultrafast screen footage, a slower preset buys nothing
    args += ["-filter_complex_script", str(script), "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-c:a", "aac", "-b:a", "160k", "-ar", str(SAMPLE_RATE),
             str(out)]
    log_data = {
        "final": str(out), "clips": [str(c) for c in clips], "concat": str(concat), "concat_info": vinfo,
        "tts": backend, "ref": str(ref_path) if ref_path else None, "durations": durations,
        "plan": plan, "clip_starts": clip_starts, "align_note": align_note,
        "audio_inputs": placements, "filter_complex": ";\n".join(graph),
        "filter_script": str(script), "argv": ff.argv(args), "ok": False, "error": None,
        "timeout_s": encode_timeout(plan["total"]),
    }
    log_path = meta_dir / "meta-edit.json"
    try:
        ff.run(args, timeout=log_data["timeout_s"], what="ffmpeg meta edit")
        log_data["ok"] = True
    except ff.FfmpegError as e:
        log_data["error"] = str(e)
        log_path.write_text(json.dumps(log_data, indent=2, default=str), encoding="utf-8")
        raise MetaError(str(e)) from None
    if not out.is_file() or out.stat().st_size == 0:
        log_data["error"] = "ffmpeg produced no output"
        log_path.write_text(json.dumps(log_data, indent=2, default=str), encoding="utf-8")
        raise MetaError(f"ffmpeg finished but {out} was not written")
    log_path.write_text(json.dumps(log_data, indent=2, default=str), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- CLI: bench-meta


def _say(msg: str) -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr, flush=True)


def _one_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0][:300] if text else exc.__class__.__name__


def _write_log(bench_dir: Path, data: dict) -> None:
    try:
        logs = bench_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "bench-meta.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except OSError:
        log.debug("could not write bench-meta.json", exc_info=True)


def default_clips(bench_dir: Path) -> list[Path]:
    """``<bench>/meta/clips/*.mp4`` in name order (the ``--record-screen`` layout)."""
    d = Path(bench_dir) / "meta" / CLIPS_SUBDIR
    return sorted(p for p in d.glob("*.mp4") if p.is_file()) if d.is_dir() else []


def meta_output_path(bench_dir: Path, bench_json) -> Path:
    slug = None
    if isinstance(bench_json, dict):
        scen = bench_json.get("scenario")
        if isinstance(scen, dict):
            slug = scen.get("slug") or scen.get("name")
        elif isinstance(scen, str):
            slug = scen
        slug = slug or bench_json.get("slug") or bench_json.get("scenario_slug")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(slug or "bench")).strip("-.") or "bench"
    return Path(bench_dir) / "meta" / f"{slug}-bench.mp4"


def cmd_bench_meta(args) -> int:
    """Handler for ``bench-meta``: meta video for an existing bench directory."""
    bench_dir = Path(args.out)
    try:
        from . import tts as tts_mod

        tts_mod.set_offline_env(getattr(args, "online", False))
        bench_path = Path(args.bench_json) if getattr(args, "bench_json", None) else bench_dir / "bench.json"
        if not bench_path.is_file():
            msg = f"{bench_path} not found: run `bench` first or pass --bench-json"
            _write_log(bench_dir, {"error": msg, "exit_code": EXIT_BAD_INPUT})
            _err(msg)
            return EXIT_BAD_INPUT
        try:
            bench_json = json.loads(bench_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            msg = f"{bench_path} is not valid JSON: {_one_line(e)}"
            _write_log(bench_dir, {"error": msg, "exit_code": EXIT_BAD_INPUT})
            _err(msg)
            return EXIT_BAD_INPUT
        baseline = None
        if getattr(args, "baseline", None):
            from .bench_report import load_baseline

            bp = Path(args.baseline)          # load_baseline: the same shapes `bench --baseline` accepts
            try:
                baseline = load_baseline(bp)
            except (OSError, ValueError, TypeError) as e:
                msg = f"baseline {bp}: {_one_line(e)}"
                _write_log(bench_dir, {"error": msg, "exit_code": EXIT_BAD_INPUT})
                _err(msg)
                return EXIT_BAD_INPUT
        if isinstance(bench_json, dict) and ("runs" in bench_json or "rows" in bench_json):
            # a real DIR/bench.json: its `drivers` key is the spec list and `scenario` a path, so read it
            # the way `bench --meta-narrate` does (rows + stored baseline rows, scenario {name, slug})
            from .bench import meta_view

            if baseline is None and isinstance(bench_json.get("baseline"), list) and bench_json["baseline"]:
                baseline = list(bench_json["baseline"])
            bench_json = meta_view(bench_json)
        clips = [Path(c) for c in (getattr(args, "clips", None) or [])] or default_clips(bench_dir)
        if not clips:
            msg = (f"no screen recordings: pass --clips a.mp4 b.mp4 or run `bench --record-screen` "
                   f"(looked in {bench_dir / 'meta' / CLIPS_SUBDIR})")
            _write_log(bench_dir, {"error": msg, "exit_code": EXIT_BAD_INPUT})
            _err(msg)
            return EXIT_BAD_INPUT
        missing = [str(c) for c in clips if not c.is_file()]
        if missing:
            msg = "clip(s) not found: " + ", ".join(missing)
            _write_log(bench_dir, {"error": msg, "exit_code": EXIT_BAD_INPUT})
            _err(msg)
            return EXIT_BAD_INPUT
        ref = getattr(args, "ref", None)
        if ref and not Path(ref).is_file():
            msg = f"reference voice not found: {ref}"
            _write_log(bench_dir, {"error": msg, "exit_code": EXIT_BAD_INPUT})
            _err(msg)
            return EXIT_BAD_INPUT

        narration = meta_narration(bench_json, baseline)
        out = meta_output_path(bench_dir, bench_json)
        final = build_meta_video(clips, narration, out, tts=getattr(args, "tts", "tone") or "tone", ref=ref,
                                 online=getattr(args, "online", False),
                                 gap=float(getattr(args, "gap", GAP_SECONDS)),
                                 align_to_clips=bool(getattr(args, "align_clips", False)))
        edit_log = json.loads((out.parent / "meta-edit.json").read_text(encoding="utf-8"))
        total = edit_log.get("plan", {}).get("total")
        data = {"final": str(final), "clips": [str(c) for c in clips], "narration": str(out.parent / "narration.json"),
                "segments": len(narration.get("steps", [])) + 2, "tts": edit_log.get("tts"),
                "total_seconds": total, "bench_json": str(bench_path), "baseline": getattr(args, "baseline", None),
                "exit_code": EXIT_OK}
        _write_log(bench_dir, data)
        _say(f"bench-meta: {final} ({len(clips)} clip(s), {data['segments']} segments, "
             f"{float(total or 0):.1f} s, tts={edit_log.get('tts')})")
        return EXIT_OK
    except KeyboardInterrupt:
        _err("bench-meta: interrupted")
        return EXIT_INTERRUPTED
    except Exception as e:  # tooling failures become exit 3 + one line
        if os.environ.get("DEMO_SMOKE_DEBUG"):
            raise
        msg = f"bench-meta: {_one_line(e)}"
        _write_log(bench_dir, {"error": msg, "exit_code": EXIT_ERROR})
        _err(msg)
        return EXIT_ERROR


def register(subparsers, run_map: dict) -> None:
    """Add ``bench-meta`` to an argparse subparsers object; fill ``run_map``."""
    sp = subparsers.add_parser("bench-meta",
                               help="narrated meta video from a bench directory's screen recordings")
    sp.add_argument("--out", default="demo-output/bench", metavar="DIR",
                    help="bench output directory (holds bench.json; the video goes to DIR/meta/)")
    sp.add_argument("--bench-json", default=None, metavar="PATH", help="bench summary (default DIR/bench.json)")
    sp.add_argument("--clips", nargs="+", default=None, metavar="MP4",
                    help="screen recordings in run order (default: DIR/meta/clips/*.mp4)")
    sp.add_argument("--baseline", default=None, metavar="PATH",
                    help='manual entries JSON (a list or {"entries": [...]}; the same file as `bench --baseline`; '
                         "default: the entries stored in bench.json)")
    sp.add_argument("--tts", choices=TTS_CHOICES, default="auto", help="TTS backend")
    sp.add_argument("--ref", default=None, help="reference voice WAV for the cloned narration")
    sp.add_argument("--online", action="store_true", help="allow HF downloads (default: HF_HUB_OFFLINE=1)")
    sp.add_argument("--gap", type=float, default=GAP_SECONDS, help="silence between segments (s)")
    sp.add_argument("--align-clips", action="store_true",
                    help="start each driver's segment no earlier than that driver's own clip (matched by the "
                         "<slug> in NN-<slug>-rN.mp4; by position when no name carries a slug and there is "
                         "one clip per driver)")
    sp.set_defaults(fn=cmd_bench_meta)
    run_map["bench-meta"] = cmd_bench_meta
