# Walkthrough recorder

Turns the 13 recording scripts in `walkthroughs/` into training videos. The scripts stay
the source of truth — edit the markdown, re-run, get new videos.

```
npm install
npm run record -- --target fixture --all --mp4
```

Output lands in `out/<id>-<slug>/`: `episode.mp4`, `episode.webm`, `episode.vtt` (narration
as subtitles) and `manifest.json` (what was recorded, planned vs actual runtime, drift).

## Targets

| `--target` | What the camera points at | Needs |
|---|---|---|
| `fixture` | Offline stand-in for the agent-builder UI: a terminal, a chat screen, a JSON viewer | nothing |
| `lol` | The live Legion platform | VPN + `npm run auth` |
| `gitlab` | Workflow JSON, from a local clone or gitlab.com | `--repo-dir` (or auth) |

`fixture` is the one that works anywhere, including CI and cloud containers. It is a
reconstruction, not the real app — but the beats, pacing, captions and chapter cards are
identical, so it is a real review copy and a real proof that the pipeline works.

`lol` is the one that produces the videos you would actually publish. It has to run on a
machine that is on the VPN. From a cloud container it fails preflight with an explanation
rather than filming a proxy error page for ten minutes.

## Recording against the live app

One-time, on a machine with the VPN connected:

```
npm run auth -- --target lol          # opens a browser, log in, press Enter
```

Then, as often as you like — no interaction required, so this is safe to trigger over SSH
from a phone or hand to an agent:

```
npm run record -- --target lol --all --mp4
```

Useful flags:

```
--episode 00,05      just these episodes
--fast               every beat collapsed to 0.4s — a ~20s smoke run per episode
--headed             watch it drive the browser
--base-url URL       record against a dev deployment instead
--repo-dir PATH      (gitlab) render workflow JSON from your clone
--burn-captions      hard-burn the subtitles into the picture
--concat             also produce out/full-series.mp4
```

Other commands:

```
node bin/record.mjs list                  # episodes, beat counts, planned runtimes
node bin/record.mjs check --target lol    # preflight only: reachable or why not
```

## How it fits together

```
walkthroughs/NN-*.md
        │
        ▼
   lib/parse.mjs      script → episode spec (segments, beats, code fences, timecodes)
        │
        ▼
   lib/pace.mjs       beats → one absolute timeline honouring the script's timecodes
        │
        ▼
   lib/record.mjs     drives a target, draws overlays, captures video
        │   ├── lib/overlay.mjs      chapter cards, captions, callouts — drawn in-page
        │   └── lib/targets/*.mjs    fixture | lol | gitlab
        ▼
   lib/post.mjs       WebVTT, H.264 mp4, series concat
```

The recorder owns the timeline, the overlays and the capture. A target owns only where the
camera points and what an `[ACTION]` beat means there. That split is why the same 13 scripts
produce a real video against the live app and a reviewable one offline, with no branching in
between.

An action the target does not recognise returns `false` and degrades to a dwell on the
current screen. A beat that fails to render is caught and the timeline continues. A take that
crashes still writes its partial video and a manifest saying why. Losing ten minutes of
recording to one bad selector is the failure mode worth engineering against.

## Notes and gotchas

**ffmpeg.** `npm install` pulls `ffmpeg-static`. Playwright also ships an ffmpeg, but it is
stripped to VP8-into-WebM — no H.264, no mp4 muxer, no subtitle filter — so asking it for an
mp4 fails with `Unrecognized option 'preset'`. `post.mjs` probes capabilities first and says
what is missing. The `.webm` is always produced regardless and plays in any browser and in
Slack. Override with `PW_FFMPEG=/path/to/ffmpeg`.

**Chromium.** If the installed Playwright is newer than the browsers on the machine, the
recorder falls back to a Chromium it finds on disk rather than telling you to download one.
Override with `PW_CHROMIUM=/path/to/chrome`.

**Pacing.** Narration length is computed from word count at 150wpm, the rate the scripts were
written to. Where a section declares a timecode and the clock is behind it, the slack is added
as dwell on the previous beat, so the video honours the script's intended pacing. Sections
that run long are reported as `drift` in the manifest rather than compressed — that is a
signal to trim the script, not the recording.

**Narration audio.** Off by default — the videos carry captions and a WebVTT track and are
watched in silence. `--narrate` adds a voice track:

```
npm run record -- --target lol --all --mp4 --narrate
```

It picks an engine off PATH, preferring macOS `say` (good) over `espeak-ng` (robotic, but
offline and available everywhere). Override with `TTS_BIN`. Anything neural needs model files
from a CDN, so it will not work on a locked-down host.

The part that matters is not the voice, it is the timing. With `--narrate`, each spoken beat's
duration becomes the **measured** length of its audio instead of a word count at 150wpm. Those
disagree by more than you would expect — one 33-word line measured 11.35s against a 13.2s
estimate — and the error accumulates until captions drift away from the voice. Narration is
therefore synthesized *before* the recording, and the recorder paces to the real numbers.

`--narrate` is ignored with `--fast`, since there is nothing to sync to. Narrated output is
written alongside the silent take as `episode-narrated.mp4`, so you can compare and keep
whichever you prefer.
