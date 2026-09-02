"""A fake ``ffmpeg`` for the DisplayCapture start/stop plumbing test.

Behaves like a screen grabber that runs until it reads ``q`` on stdin (or
stdin closes, or ``FFMPEG_FAKE_MAX_SECONDS`` elapse), then writes a tiny file
at the output path (the last argument) plus a sidecar ``<out>.argv.json``
holding the argv it was started with and how it was stopped.

Knobs (environment):

* ``FFMPEG_FAKE_DIE=1``         exit 1 immediately without writing anything
* ``FFMPEG_FAKE_MAX_SECONDS``   safety timeout (default 15)
* ``FFMPEG_FAKE_IGNORE_Q=1``    keep running after ``q`` (forces the terminate path)

Run it through a launcher (``tests/test_bench_meta.py::fake_ffmpeg``): a ``sh``
script on POSIX, a ``.cmd`` file on Windows, both calling this file with the
current interpreter.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time


def _write_output(out_path: str, argv: list[str], stopped_by: str) -> None:
    try:
        with open(out_path, "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypisom fake-ffmpeg-capture\n")
        with open(out_path + ".argv.json", "w", encoding="utf-8") as fh:
            json.dump({"argv": argv, "stopped_by": stopped_by, "pid": os.getpid()}, fh)
    except OSError as e:
        print(f"fake ffmpeg: cannot write {out_path}: {e}", file=sys.stderr)


def main(argv: list[str]) -> int:
    if "-version" in argv:
        print("ffmpeg version fake-0.0 Copyright (c) tests")
        return 0
    if os.environ.get("FFMPEG_FAKE_DIE") == "1":
        print("fake ffmpeg: Could not open the display (FFMPEG_FAKE_DIE)", file=sys.stderr)
        return 1
    if not argv:
        print("fake ffmpeg: no output path", file=sys.stderr)
        return 2
    out_path = argv[-1]
    print(f"fake ffmpeg: recording to {out_path}", file=sys.stderr, flush=True)
    max_seconds = float(os.environ.get("FFMPEG_FAKE_MAX_SECONDS") or 15)
    done = threading.Event()

    def on_timeout() -> None:
        if not done.is_set():
            done.set()
            _write_output(out_path, argv, "timeout")
            os._exit(0)

    timer = threading.Timer(max_seconds, on_timeout)
    timer.daemon = True
    timer.start()
    stopped_by = "eof"
    stdin = getattr(sys.stdin, "buffer", sys.stdin)
    while True:
        ch = stdin.read(1)
        if not ch:
            if os.environ.get("FFMPEG_FAKE_IGNORE_Q") == "1":
                time.sleep(max_seconds + 5)   # a hung grabber: only the timer or a signal ends it
            break
        if ch in (b"q", "q"):
            if os.environ.get("FFMPEG_FAKE_IGNORE_Q") == "1":
                continue
            stopped_by = "q"
            break
    if done.is_set():
        return 0
    done.set()
    timer.cancel()
    time.sleep(0.05)   # mimic the muxer trailer write
    _write_output(out_path, argv, stopped_by)
    print("fake ffmpeg: done", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
