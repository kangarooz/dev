"""Concatenate video clips into one H.264 file (used by the bench meta video).

Screen recordings from different drivers may differ in size or frame rate, so
the clips go through the ``concat`` *filter* (not the demuxer): every input is
scaled and padded to a common size, forced to one constant frame rate and one
pixel format, then joined.  Audio is dropped (screen grabs have none; the meta
narration is mixed in afterwards by :func:`demo_smoke.bench_meta.build_meta_video`).

Kept separate from ``ffmpeg_util`` (another builder's module); it only uses
that module's ``run``/``media_info``/``FfmpegError``.
"""

from __future__ import annotations

from pathlib import Path

from . import ffmpeg_util as ff

DEFAULT_FPS = 30
MIN_TIMEOUT_S = 900       # ffmpeg_util.run's default
TIMEOUT_PER_SECOND = 4    # seconds of encode time allowed per second of footage


def encode_timeout(footage_seconds: float) -> int:
    """Timeout for an encode of ``footage_seconds`` of video: ``max(900, 4 x footage)``."""
    return max(MIN_TIMEOUT_S, int(TIMEOUT_PER_SECOND * max(0.0, float(footage_seconds or 0.0))))


def _even(n: int) -> int:
    n = int(n)
    return max(2, n if n % 2 == 0 else n - 1)


def concat_filter(n: int, width: int, height: int, fps: int = DEFAULT_FPS) -> str:
    """The ``filter_complex`` string joining ``n`` video inputs at ``width``x``height`` (pure)."""
    w, h = _even(width), _even(height)
    parts = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={int(fps)},"
            f"format=yuv420p[v{i}]"
        )
    labels = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"{labels}concat=n={n}:v=1:a=0[vout]")
    return ";".join(parts)


def concat_videos(paths: list[str | Path], out: str | Path, fps: int = DEFAULT_FPS,
                  size: tuple[int, int] | None = None) -> Path:
    """Join ``paths`` (in order) into ``out`` (H.264, yuv420p, ``fps`` CFR, no audio).

    ``size`` defaults to the first clip's dimensions.  Raises ``FfmpegError``
    when a clip is missing, has no video stream, or ffmpeg fails.
    """
    clips = [Path(p) for p in paths]
    if not clips:
        raise ff.FfmpegError("concat_videos: no clips given")
    for c in clips:
        if not c.is_file():
            raise ff.FfmpegError(f"concat_videos: clip not found: {c}")
    infos = [ff.media_info(c) for c in clips]
    if size is None:
        info = infos[0]
        if not info.get("width"):
            raise ff.FfmpegError(f"concat_videos: {clips[0].name} has no video stream")
        size = (int(info["width"]), int(info["height"]))
    total = sum(float(i.get("duration") or 0.0) for i in infos)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = ["-y"]
    for c in clips:
        args += ["-i", str(c)]
    args += ["-filter_complex", concat_filter(len(clips), size[0], size[1], fps),
             "-map", "[vout]", "-an",
             "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(out)]
    # whole-display bench recordings run for as long as the bench did: scale the timeout with the footage
    ff.run(args, timeout=encode_timeout(total), what=f"concat of {len(clips)} clip(s)")
    if not out.is_file() or out.stat().st_size == 0:
        raise ff.FfmpegError(f"concat_videos: ffmpeg finished but {out} was not written")
    return out
