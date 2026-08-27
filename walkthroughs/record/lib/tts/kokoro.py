#!/usr/bin/env python3
"""Batch narration with Kokoro.

Reads a JSON job list on stdin and writes one wav per entry:

    [{"text": "...", "path": "/abs/out.wav"}, ...]

Emits one JSON object on stdout: {"ok": [...], "failed": [...]}, where each ok entry
carries the measured duration so the caller can pace the video to the real audio.

Batching is the whole point of this file. The model is ~325MB and takes a couple of
seconds to load, so synthesizing 238 beats one subprocess at a time would spend about
nine minutes doing nothing but loading the same weights over and over.
"""

import json
import os
import sys


def main() -> int:
    try:
        import soundfile as sf
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        print(json.dumps({"error": f"missing dependency: {exc}. pip install kokoro-onnx soundfile"}))
        return 2

    model = os.environ.get("KOKORO_MODEL", "/opt/kokoro/kokoro-v1.0.onnx")
    voices = os.environ.get("KOKORO_VOICES", "/opt/kokoro/voices-v1.0.bin")
    voice = os.environ.get("KOKORO_VOICE", "af_heart")
    speed = float(os.environ.get("KOKORO_SPEED", "1.0"))
    lang = os.environ.get("KOKORO_LANG", "en-us")

    for path in (model, voices):
        if not os.path.exists(path):
            print(json.dumps({"error": f"model file not found: {path}"}))
            return 2

    try:
        jobs = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"bad job list: {exc}"}))
        return 2

    kokoro = Kokoro(model, voices)

    ok, failed = [], []
    for job in jobs:
        text = (job.get("text") or "").strip()
        path = job.get("path")
        if not text or not path:
            failed.append({"path": path, "error": "empty text or path"})
            continue
        try:
            samples, rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
            sf.write(path, samples, rate)
            ok.append({"path": path, "durSec": round(len(samples) / rate, 3)})
        except Exception as exc:  # one bad line must not cost the episode its track
            failed.append({"path": path, "error": str(exc)})

    print(json.dumps({"ok": ok, "failed": failed, "voice": voice, "speed": speed}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
