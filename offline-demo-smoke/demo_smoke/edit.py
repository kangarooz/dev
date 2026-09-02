"""Assemble ``final/<slug>.mp4`` from the raw capture, the narration segments
and the actual times recorded in ``logs/markers.json``.

:func:`plan_timeline` is pure: it turns markers + narration durations into a
list of source video segments (with speed factors), remapped audio placements
and the final length.  :func:`build` renders that plan with ONE ffmpeg
``filter_complex`` (trim/setpts + concat for video; aresample/adelay/amix +
loudnorm for audio) and records the exact command in ``logs/edit.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import ffmpeg_util as ff

INTRO = "intro"
OUTRO = "outro"
FPS = 30
SAMPLE_RATE = 48000
AMIX_MAX_INPUTS = 32          # amix accepts far more, but keep each node readable
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
DEFAULT_TAIL = 1.0            # seconds of picture kept after the outro narration ends


class EditError(RuntimeError):
    """Expected failure while building the final video (one-line message)."""


# --------------------------------------------------------------------------- pure timeline maths


def _speedup_windows(markers: dict, durations: dict, min_wait: float, end_t: float) -> list[dict]:
    """Wait windows that may be sped up: at least ``min_wait`` long and no
    narration (intro or any step) still playing when the window starts."""
    playing: list[tuple[float, float]] = []
    intro_len = float(durations.get(INTRO, 0.0) or 0.0)
    if intro_len > 0:
        playing.append((float(markers.get("intro_t", 0.0) or 0.0), intro_len))
    for st in markers.get("steps", []):
        d = float(durations.get(st["id"], 0.0) or 0.0)
        if d > 0:
            playing.append((float(st["t_start"]), d))

    def narration_free(a: float, b: float) -> bool:
        return all(s + d <= a or s >= b for s, d in playing)

    found: list[dict] = []
    for st in markers.get("steps", []):
        for win in st.get("wait_windows") or []:
            a, b = max(0.0, float(win[0])), min(end_t, float(win[1]))
            if b - a < min_wait:
                continue
            if not narration_free(a, b):
                continue
            found.append({"src_start": a, "src_end": b, "step_id": st["id"]})
    found.sort(key=lambda w: w["src_start"])
    merged: list[dict] = []
    for w in found:                       # merge overlaps (defensive; windows should not overlap)
        if merged and w["src_start"] <= merged[-1]["src_end"]:
            merged[-1]["src_end"] = max(merged[-1]["src_end"], w["src_end"])
        else:
            merged.append(dict(w))
    return merged


def _segments(windows: list[dict], end_t: float, speed: float) -> list[dict]:
    segs: list[dict] = []
    cur = 0.0
    for w in windows:
        if w["src_start"] > cur:
            segs.append({"src_start": cur, "src_end": w["src_start"], "speed": 1.0})
        segs.append({"src_start": w["src_start"], "src_end": w["src_end"], "speed": float(speed)})
        cur = w["src_end"]
    if end_t > cur or not segs:
        segs.append({"src_start": cur, "src_end": max(end_t, cur), "speed": 1.0})
    return segs


def _with_output_times(segs: list[dict]) -> list[dict]:
    out = 0.0
    for s in segs:
        s["out_start"] = round(out, 4)
        out += (s["src_end"] - s["src_start"]) / s["speed"]
        s["out_end"] = round(out, 4)
    return segs


def remap(segments: list[dict], t: float) -> float:
    """Source time -> output time through the segment list (the time map)."""
    t = float(t)
    if not segments:
        return t
    if t <= segments[0]["src_start"]:
        return segments[0]["out_start"]
    out = segments[0]["out_start"]
    for s in segments:
        if t <= s["src_end"]:
            return round(out + (t - s["src_start"]) / s["speed"], 4)
        out = s["out_end"]
    last = segments[-1]
    return round(last["out_end"] + (t - last["src_end"]), 4)   # beyond the plan: 1x


def plan_timeline(markers: dict, durations: dict, min_wait: float = 1.5, speed: float = 4.0,
                  tail: float = DEFAULT_TAIL) -> dict:
    """Pure timeline plan (JSON-serialisable).

    * wait windows >= ``min_wait`` are played at ``speed`` when no narration is
      playing during them (the step's own narration has ended before the
      window starts); everything else stays at 1.0
    * audio: intro at ``intro_t``, each step at its remapped ``t_start``, outro
      at the remapped ``outro_t``; ``end_t`` (+ ``tail``) is remapped as the
      picture's end
    """
    end_t = float(markers.get("end_t", 0.0) or 0.0) + max(0.0, float(tail))
    windows = _speedup_windows(markers, durations, float(min_wait), end_t)
    segments = _with_output_times(_segments(windows, end_t, float(speed)))
    total = segments[-1]["out_end"] if segments else 0.0

    audio: list[dict] = []
    intro_t = float(markers.get("intro_t", 0.0) or 0.0)
    audio.append({"id": INTRO, "src_t": intro_t, "t": remap(segments, intro_t),
                  "duration": float(durations.get(INTRO, 0.0) or 0.0)})
    for st in markers.get("steps", []):
        src = float(st["t_start"])
        audio.append({"id": st["id"], "src_t": src, "t": remap(segments, src),
                      "duration": float(durations.get(st["id"], 0.0) or 0.0)})
    outro_t = float(markers.get("outro_t", 0.0) or 0.0)
    audio.append({"id": OUTRO, "src_t": outro_t, "t": remap(segments, outro_t),
                  "duration": float(durations.get(OUTRO, 0.0) or 0.0)})

    return {
        "video_segments": [{"src_start": s["src_start"], "src_end": s["src_end"], "speed": s["speed"],
                            "out_start": s["out_start"], "out_end": s["out_end"]} for s in segments],
        "audio": audio,
        "total": round(total, 4),
        "map": {
            "source_end": end_t,
            "saved_seconds": round(end_t - total, 4),
            "speedups": windows,
            "rule": (f"wait windows >= {float(min_wait)} s with no narration playing are "
                     f"played at {float(speed)}x; output_t = out_start + (src_t - src_start) / speed"),
        },
    }


# --------------------------------------------------------------------------- filter graph pieces


def _f(x: float) -> str:
    return f"{float(x):.4f}".rstrip("0").rstrip(".") or "0"


def video_chain(segments: list[dict], crop: dict | None) -> list[str]:
    lines = []
    labels = []
    for i, s in enumerate(segments):
        lab = f"v{i}"
        labels.append(f"[{lab}]")
        lines.append(f"[0:v]trim=start={_f(s['src_start'])}:end={_f(s['src_end'])},"
                     f"setpts=(PTS-STARTPTS)/{_f(s['speed'])}[{lab}]")
    lines.append(f"{''.join(labels)}concat=n={len(segments)}:v=1:a=0[vcat]")
    post = [f"fps={FPS}"]
    if crop:
        post.insert(0, f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}")
    lines.append(f"[vcat]{','.join(post)},format=yuv420p[vout]")
    return lines


def _amix_tree(labels: list[str], lines: list[str]) -> str:
    """Mix ``labels`` with amix (inputs=N); chunked when N > AMIX_MAX_INPUTS."""
    level = 0
    while len(labels) > 1:
        nxt = []
        for k in range(0, len(labels), AMIX_MAX_INPUTS):
            chunk = labels[k:k + AMIX_MAX_INPUTS]
            if len(chunk) == 1:
                nxt.append(chunk[0])
                continue
            lab = f"mix{level}_{k // AMIX_MAX_INPUTS}"
            lines.append(f"{''.join(chunk)}amix=inputs={len(chunk)}:normalize=0:"
                         f"dropout_transition=0[{lab}]")
            nxt.append(f"[{lab}]")
        labels = nxt
        level += 1
    return labels[0]


def audio_chain(placements: list[dict], total: float) -> list[str]:
    """``placements``: [{"input": ffmpeg input index, "t": output seconds}]."""
    lines = []
    labels = []
    for i, p in enumerate(placements):
        lab = f"a{i}"
        labels.append(f"[{lab}]")
        delay_ms = max(0, round(float(p["t"]) * 1000))
        # one delay per channel (the stream is stereo after aformat): `adelay=D|D` works on
        # every ffmpeg 4.x, unlike `all=1`, which only exists from 4.3 on
        lines.append(f"[{p['input']}:a]aresample={SAMPLE_RATE},"
                     f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"adelay={delay_ms}|{delay_ms}[{lab}]")
    bus = _amix_tree(labels, lines)
    lines.append(f"{bus}{LOUDNORM},aresample={SAMPLE_RATE},"
                 f"apad=whole_dur={_f(total)},atrim=end={_f(total)},asetpts=PTS-STARTPTS[aout]")
    return lines


def crop_for(capture: dict, viewport: dict | None) -> dict | None:
    """Crop box when the capture is larger than the viewport (screen backend)."""
    if not viewport:
        return None
    vw, vh = int(viewport.get("width", 0)), int(viewport.get("height", 0))
    cw, ch = int(capture.get("width", 0)), int(capture.get("height", 0))
    if vw <= 0 or vh <= 0 or cw <= 0 or ch <= 0:
        return None
    if cw <= vw and ch <= vh:
        return None
    w, h = min(vw, cw) & ~1, min(vh, ch) & ~1
    return {"width": w, "height": h, "x": 0, "y": 0}


# --------------------------------------------------------------------------- inputs


def _slug(scenario: dict) -> str:
    s = str(scenario.get("slug") or scenario.get("name") or "demo")
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-.")
    return s or "demo"


def find_capture(out: Path) -> Path:
    raw = Path(out) / "raw"
    for name in ("capture.mp4", "capture.mkv"):
        p = raw / name
        if p.is_file():
            return p
    raise EditError(f"{raw / 'capture.mp4'} not found: run `record` first")


def _wav_seconds(path: Path) -> float:
    try:
        import soundfile as sf  # type: ignore

        info = sf.info(str(path))
        return float(info.frames) / float(info.samplerate)
    except Exception as e:  # noqa: BLE001 - unreadable wav is an input error
        raise EditError(f"cannot read narration segment {path.name}: {e}") from None


def load_durations(out: Path, ids: list[str]) -> dict:
    """``audio/durations.json`` (written by synth); any id missing there is
    measured from its wav, absent segments count as 0."""
    audio = Path(out) / "audio"
    durations: dict = {}
    p = audio / "durations.json"
    if p.is_file():
        try:
            durations = {k: float(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
        except (ValueError, AttributeError, TypeError) as e:
            raise EditError(f"{p} is not valid: {e}") from None
    for sid in ids:
        if sid not in durations:
            wav = audio / f"seg-{sid}.wav"
            durations[sid] = _wav_seconds(wav) if wav.is_file() else 0.0
    return durations


# --------------------------------------------------------------------------- build


def build(out: Path, scenario: dict) -> Path:
    """Render ``final/<slug>.mp4``; writes ``logs/edit.json`` (plan + exact argv)."""
    from . import markers as mk

    out = Path(out)
    logs = out / "logs"
    final_dir = out / "final"
    logs.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    capture = find_capture(out)
    try:
        markers = mk.load(out)
    except (FileNotFoundError, ValueError) as e:
        raise EditError(str(e)) from None
    step_ids = [st["id"] for st in markers.get("steps", [])]
    durations = load_durations(out, [INTRO, *step_ids, OUTRO])

    cap_info = ff.media_info(capture)
    if not cap_info["width"]:
        raise EditError(f"{capture.name} has no video stream")
    notes: list[str] = []
    end_t = float(markers.get("end_t", 0.0) or 0.0)
    if end_t <= 0:
        raise EditError("markers.json end_t is 0: the recording has no timeline")
    tail = DEFAULT_TAIL
    if cap_info["duration"] and end_t + tail > cap_info["duration"] + 0.05:
        tail = max(0.0, cap_info["duration"] - end_t)
        if end_t > cap_info["duration"]:
            notes.append(f"markers end_t {end_t:.2f}s exceeds capture length "
                         f"{cap_info['duration']:.2f}s; timeline clamped")
            markers = {**markers, "end_t": cap_info["duration"]}
    plan = plan_timeline(markers, durations, tail=tail)

    audio_dir = out / "audio"
    inputs: list[Path] = [capture]
    placements: list[dict] = []
    for a in plan["audio"]:
        wav = audio_dir / f"seg-{a['id']}.wav"
        if not wav.is_file():
            notes.append(f"no narration segment for '{a['id']}' ({wav.name} missing)")
            continue
        placements.append({"input": len(inputs), "id": a["id"], "t": a["t"], "file": str(wav)})
        inputs.append(wav)
    if not placements:
        raise EditError(f"no narration segments (seg-*.wav) found in {audio_dir}: run `synth` first")

    crop = crop_for(cap_info, scenario.get("viewport"))
    graph = [*video_chain(plan["video_segments"], crop), *audio_chain(placements, plan["total"])]
    script = logs / "edit-filter.txt"
    script.write_text(";\n".join(graph) + "\n", encoding="utf-8")

    final = final_dir / f"{_slug(scenario)}.mp4"
    args: list[str] = ["-y"]
    for p in inputs:
        args += ["-i", str(p)]
    args += ["-filter_complex_script", str(script), "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-c:a", "aac", "-b:a", "160k", "-ar", str(SAMPLE_RATE),
             str(final)]
    log = {
        "final": str(final), "capture": str(capture), "capture_info": cap_info, "crop": crop,
        "plan": plan, "audio_inputs": placements, "notes": notes,
        "filter_complex": ";\n".join(graph), "filter_script": str(script),
        "argv": ff.argv(args), "ok": False, "error": None,
    }
    try:
        ff.run(args, what="ffmpeg edit")
        log["ok"] = True
    except ff.FfmpegError as e:
        log["error"] = str(e)
        (logs / "edit.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        raise EditError(str(e)) from None
    if not final.is_file() or final.stat().st_size == 0:
        log["error"] = "ffmpeg produced no output"
        (logs / "edit.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        raise EditError(f"ffmpeg finished but {final} was not written")
    (logs / "edit.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    return final
