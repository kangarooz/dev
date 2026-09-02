"""Verify ``final/<slug>.mp4``: length, audio/video agreement, no black
frames at the start/end, audible narration, thumbnails.  Results go to
``logs/verify.json`` with pass/fail + a human-readable detail per check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import ffmpeg_util as ff

EXTRA_SECONDS = 10.0          # allowed over max_length_seconds
AV_TOLERANCE = 0.5            # |audio - video| seconds
EDGE_SECONDS = 1.0            # blackdetect window at each end
BLACK_D = 0.1
BLACK_PIC_TH = 0.98
MIN_MEAN_VOLUME = -30.0       # dB, volumedetect mean_volume must be above this
THUMB_PERCENTS = (10, 50, 90)


class VerifyError(RuntimeError):
    """Expected failure (no final video, ffmpeg missing); one-line message."""


def _slug(scenario: dict) -> str:
    s = str(scenario.get("slug") or scenario.get("name") or "demo")
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-.")
    return s or "demo"


def find_final(out: Path, scenario: dict) -> Path:
    final_dir = Path(out) / "final"
    p = final_dir / f"{_slug(scenario)}.mp4"
    if p.is_file():
        return p
    others = sorted(final_dir.glob("*.mp4"), key=lambda q: q.stat().st_mtime) if final_dir.is_dir() else []
    if others:
        return others[-1]
    raise VerifyError(f"{p} not found: run `edit` first")


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "pass": bool(ok), "detail": detail}


def _fmt_black(intervals: list[dict], offset: float) -> str:
    return ", ".join(f"{offset + i['start']:.2f}-{offset + i['end']:.2f}s" for i in intervals)


def run_checks(final: Path, scenario: dict, info: dict) -> list[dict]:
    checks: list[dict] = []
    duration = float(info.get("duration") or 0.0)
    limit = float(scenario.get("max_length_seconds") or 0.0) + EXTRA_SECONDS

    checks.append(_check("duration", 0.0 < duration <= limit,
                         f"{duration:.1f} s (limit {limit:.0f} s = max_length_seconds "
                         f"{scenario.get('max_length_seconds', '?')} + {EXTRA_SECONDS:.0f})"))

    has_audio = bool(info.get("has_audio"))
    checks.append(_check("audio_present", has_audio,
                         f"{info.get('audio_codec') or 'no audio stream'}"
                         + (f" {info.get('sample_rate')} Hz" if has_audio and info.get("sample_rate") else "")))

    if has_audio:
        v_len = ff.stream_duration(final, "video")
        a_len = ff.stream_duration(final, "audio")
        if v_len is None or a_len is None:
            checks.append(_check("av_length_match", False,
                                 "could not decode both streams to measure their lengths"))
        else:
            diff = abs(a_len - v_len)
            checks.append(_check("av_length_match", diff <= AV_TOLERANCE,
                                 f"video {v_len:.2f} s, audio {a_len:.2f} s, diff {diff:.2f} s "
                                 f"(tolerance {AV_TOLERANCE} s)"))
    else:
        checks.append(_check("av_length_match", False, "no audio stream to compare"))

    try:
        head = ff.blackdetect(final, ss=0.0, t=EDGE_SECONDS, d=BLACK_D, pic_th=BLACK_PIC_TH)
        checks.append(_check("no_black_start", not head,
                             f"first {EDGE_SECONDS:.0f} s: "
                             + ("no black frames" if not head else "black at " + _fmt_black(head, 0.0))))
    except ff.FfmpegError as e:
        checks.append(_check("no_black_start", False, str(e)))

    tail_ss = max(0.0, duration - EDGE_SECONDS)
    try:
        tail = ff.blackdetect(final, ss=tail_ss, t=EDGE_SECONDS, d=BLACK_D, pic_th=BLACK_PIC_TH)
        checks.append(_check("no_black_end", not tail,
                             f"last {EDGE_SECONDS:.0f} s (from {tail_ss:.1f} s): "
                             + ("no black frames" if not tail else "black at " + _fmt_black(tail, tail_ss))))
    except ff.FfmpegError as e:
        checks.append(_check("no_black_end", False, str(e)))

    if has_audio:
        vol = ff.volumedetect(final)
        mean = vol.get("mean_volume")
        if mean is None:
            checks.append(_check("narration_audible", False, "volumedetect reported no mean_volume"))
        else:
            checks.append(_check("narration_audible", mean > MIN_MEAN_VOLUME,
                                 f"mean_volume {mean:.1f} dB (must be > {MIN_MEAN_VOLUME:.0f} dB)"
                                 + (f", max_volume {vol['max_volume']:.1f} dB"
                                    if vol.get("max_volume") is not None else "")))
    else:
        checks.append(_check("narration_audible", False, "no audio stream"))
    return checks


def make_thumbnails(final: Path, duration: float, final_dir: Path) -> tuple[list[str], str | None]:
    paths: list[str] = []
    for pct in THUMB_PERCENTS:
        at = max(0.0, duration * pct / 100.0)
        if duration > 0.2:
            at = min(at, duration - 0.1)      # 90 % of a short clip must still hit a frame
        dst = final_dir / f"thumb-{pct}.png"
        try:
            ff.thumbnail(final, at, dst)
        except ff.FfmpegError as e:
            return paths, f"thumb-{pct}.png at {at:.1f} s: {e}"
        paths.append(str(dst))
    return paths, None


def check(out: Path, scenario: dict) -> dict:
    """Run every check; returns ``{"pass","checks","duration","thumbnails"}`` and
    writes ``logs/verify.json``.  Raises ``VerifyError`` only when there is
    nothing to verify (no final video / no ffmpeg)."""
    out = Path(out)
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    final = find_final(out, scenario)
    try:
        info = ff.media_info(final)
    except ff.FfmpegError as e:
        raise VerifyError(str(e)) from None
    try:
        checks = run_checks(final, scenario, info)
    except ff.FfmpegError as e:
        raise VerifyError(str(e)) from None

    duration = float(info.get("duration") or 0.0)
    thumbs, thumb_err = make_thumbnails(final, duration, final.parent)
    checks.append(_check("thumbnails", thumb_err is None and len(thumbs) == len(THUMB_PERCENTS),
                         thumb_err or f"{len(thumbs)} thumbnails at {'/'.join(map(str, THUMB_PERCENTS))} %"))

    result = {
        "pass": all(c["pass"] for c in checks),
        "checks": checks,
        "duration": round(duration, 3),
        "thumbnails": thumbs,
        "final": str(final),
        "info": info,
    }
    (logs / "verify.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
