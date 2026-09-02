"""Run the real OpenCode binary with the scripted fake LLM and save its ``--format json`` stdout.

The same setup as ``tests/test_opencode_e2e.py`` (fixture app served on a loopback
port, ``FakeOpenCodeLLM`` with the golden-path kit commands, scratch ``HOME``, the
fake provider injected through ``OPENCODE_CONFIG_CONTENT``), but the point is the
event stream itself: ``capture()`` returns the raw stdout and ``scrub()`` turns it
into a stable fixture (absolute paths -> ``<tmp>`` / ``<kit>``, ids -> fixed
strings) that ``demo_smoke.opencode_events`` is unit-tested against.

Refresh the sample after an OpenCode upgrade::

    .venv/bin/python -m tests.opencode_capture_events            # writes tests/fixtures/opencode-events.sample.jsonl
    .venv/bin/python -m tests.opencode_capture_events --dest x.jsonl --raw x.raw.jsonl

Needs ``OPENCODE_BIN`` (default: the sandbox install) and a Chrome binary
(``DEMO_SMOKE_CHROME`` or discovery); exits 3 with a one-line reason otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

from demo_smoke import chrome
from tests.fixtures.serve import serve_dir
from tests.opencode_fake_llm import FakeOpenCodeLLM, kit_command
from tests.test_opencode_e2e import (
    APP_DIR,
    OPENCODE_BIN,
    TIMEOUT_S,
    _opencode_env,
    _write_scenario,
)

SAMPLE_PATH = KIT / "tests" / "fixtures" / "opencode-events.sample.jsonl"
PROMPT = "Run the smoke pipeline as scripted"

# OpenCode ids: a 3-letter prefix, an underscore and a long alphanumeric tail
# (``ses_``, ``msg_``, ``prt_``); the tail is replaced by a per-prefix counter.
_ID_RE = re.compile(r"\b(ses|msg|prt|usr|req|tool|call)_([A-Za-z0-9]{8,})\b")


@dataclass
class Capture:
    stdout: str
    stderr: str
    returncode: int
    seconds: float
    commands: list[str]
    replacements: list[tuple[str, str]]      # (real path, placeholder) in scrub order
    fake_errors: list[str] = field(default_factory=list)


def golden_commands(scenario: Path, out: Path, python: str | None = None) -> list[str]:
    """The e2e golden path: doctor -> dryrun -> narrate -> synth -> record -> edit -> verify."""
    return [
        kit_command("doctor", "--out", str(out), python=python),
        kit_command("dryrun", str(scenario), "--out", str(out), "--headless", python=python),
        kit_command("narrate-template", str(scenario), "--out", str(out), python=python),
        kit_command("narrate-validate", str(scenario), "--out", str(out), python=python),
        kit_command("synth", "--out", str(out), "--tts", "tone", python=python),
        kit_command("record", str(scenario), "--out", str(out), "--capture", "screencast", "--headless",
                    python=python),
        kit_command("edit", "--out", str(out), python=python),
        kit_command("verify", "--out", str(out), python=python),
    ]


def missing_prerequisite() -> str | None:
    """Why a capture cannot run here (``None`` when it can)."""
    if not Path(OPENCODE_BIN).is_file():
        return f"OpenCode binary not found: {OPENCODE_BIN} (set OPENCODE_BIN)"
    if not chrome.find_chrome():
        return "no Chrome binary available (set DEMO_SMOKE_CHROME)"
    return None


def capture(tmp: Path, timeout_s: int = TIMEOUT_S, prompt: str = PROMPT) -> Capture:
    """Run ``opencode run --agent demo-smoke --auto --format json`` and return its raw output.

    ``tmp`` holds the scratch HOME, the scenario copy, the fake LLM log and the kit
    output directory; the caller owns it (and can inspect it after a failure).
    """
    tmp = Path(tmp).resolve()
    out = tmp / "out"
    home = tmp / "home"
    home.mkdir(parents=True, exist_ok=True)
    with serve_dir(APP_DIR) as base:
        scenario = _write_scenario("fixture-pass.json", base, tmp / "scenario" / "pass.json")
        commands = golden_commands(scenario, out)
        with FakeOpenCodeLLM(commands, log_path=tmp / "fake-llm.jsonl") as fake:
            argv = [OPENCODE_BIN, "run", "--agent", "demo-smoke", "--auto", "--model", "fake/scripted",
                    "--dir", str(KIT), "--format", "json", prompt]
            t0 = time.monotonic()
            proc = subprocess.run(argv, cwd=str(KIT), env=_opencode_env(home, fake.base_url),
                                  capture_output=True, text=True, timeout=timeout_s, check=False)
            seconds = time.monotonic() - t0
            errors = list(fake.errors)
    replacements = path_replacements(tmp)
    return Capture(stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode, seconds=seconds,
                   commands=commands, replacements=replacements, fake_errors=errors)


def path_replacements(tmp: Path, kit: Path = KIT, python: str | None = None) -> list[tuple[str, str]]:
    """Longest-first (real, placeholder) pairs so nested paths scrub cleanly on every OS."""
    python = python or sys.executable
    pairs = [(str(Path(tmp)), "<tmp>"), (str(kit), "<kit>"), (str(Path(python)), "<python>")]
    forms: list[tuple[str, str]] = []
    for real, tag in pairs:
        variants = {real, real.replace("\\", "/"), real.replace("\\", "\\\\"), real.replace("/", "\\")}
        forms.extend((v, tag) for v in variants if v)
    # a resolved tmp (symlinks) may differ from the given one; scrub both spellings
    try:
        resolved = str(Path(tmp).resolve())
        if resolved != str(tmp):
            forms.append((resolved, "<tmp>"))
            forms.append((resolved.replace("\\", "\\\\"), "<tmp>"))
    except OSError:
        pass
    forms.sort(key=lambda p: len(p[0]), reverse=True)
    return forms


def scrub(text: str, replacements: list[tuple[str, str]] | None = None) -> str:
    """Replace absolute paths with placeholders and OpenCode ids with fixed, numbered ids.

    Ids are rewritten per prefix in order of first appearance (``ses_0001``,
    ``msg_0001``, ``prt_0007``...), so every reference to the same id stays equal.
    """
    for real, tag in replacements or []:
        text = text.replace(real, tag)
    counters: dict[str, dict[str, str]] = {}

    def fixed(m: re.Match) -> str:
        prefix, tail = m.group(1), m.group(2)
        table = counters.setdefault(prefix, {})
        if tail not in table:
            table[tail] = f"{prefix}_{len(table) + 1:04d}"
        return table[tail]

    text = _ID_RE.sub(fixed, text)
    # home-directory spellings that survive a Windows/macOS/Linux capture
    home = os.path.expanduser("~")
    if home and home not in ("~", "/"):
        text = text.replace(home.replace("\\", "\\\\"), "<home>").replace(home, "<home>")
    return text


def write_sample(dest: Path, cap: Capture, raw: Path | None = None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        Path(raw).parent.mkdir(parents=True, exist_ok=True)
        Path(raw).write_text(cap.stdout, encoding="utf-8", newline="")   # verbatim, no newline translation
    text = scrub(cap.stdout, cap.replacements)
    lines = [ln for ln in text.splitlines() if ln.strip()]     # CRLF on Windows -> LF; drop blanks
    dest.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8", newline="\n")
    return dest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dest", default=str(SAMPLE_PATH), help="scrubbed JSONL to write")
    p.add_argument("--raw", default=None, help="also save the unscrubbed stdout here")
    p.add_argument("--tmp", default=None, help="scratch dir (default: a fresh temp dir, kept on failure)")
    p.add_argument("--timeout-s", type=int, default=TIMEOUT_S)
    args = p.parse_args(argv)
    why = missing_prerequisite()
    if why:
        print(f"error: {why}", file=sys.stderr)
        return 3
    tmp = Path(args.tmp) if args.tmp else Path(tempfile.mkdtemp(prefix="opencode-events-"))
    tmp.mkdir(parents=True, exist_ok=True)
    cap = capture(tmp, timeout_s=args.timeout_s)
    dest = write_sample(Path(args.dest), cap, raw=Path(args.raw) if args.raw else None)
    lines = [ln for ln in cap.stdout.splitlines() if ln.strip()]
    print(f"opencode exit={cap.returncode} seconds={cap.seconds:.0f} lines={len(lines)} "
          f"fake_errors={len(cap.fake_errors)} -> {dest}")
    if cap.returncode != 0 or cap.fake_errors:
        print(f"stderr (tail):\n{cap.stderr[-3000:]}", file=sys.stderr)
        print(f"scratch kept at {tmp}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
