/**
 * pace.mjs — the timing engine.
 *
 * Turns a parsed episode (segments of beats, per spec.schema.json) into ONE flat,
 * absolutely-timed beat list that the recorder plays back and the VTT writer captions.
 *
 * The guiding rule: the timecodes in the scripts (`## Cold open — 0:00`) are the author's
 * intended pacing, not a byproduct. When narration finishes early we hold the current shot
 * until the next segment's timecode instead of racing ahead; when it runs long we let it run
 * long and report the overrun so the author can trim the script rather than have the machine
 * silently speed-read it.
 */

/** Nothing is on screen for less than this — a sub-second cut reads as a glitch. */
const MIN_BEAT_SEC = 0.9;

/** Smoke-run cap. Short enough that a 13-episode pass is a couple of minutes. */
const FAST_MAX_SEC = 0.4;

/**
 * Dwell used when the parser could not attach an estSec to a non-narration beat.
 * These are deliberately conservative: a screen/action beat is a visual pause, and the
 * recorder may extend them anyway when it absorbs slack ahead of the next timecode.
 */
const DEFAULT_DWELL_SEC = {
  screen: 1.5,
  action: 2.5,
  callout: 2.5,
  hold: 3,
};

/** Float comparisons on cent-rounded seconds; anything under half a centisecond is noise. */
const EPSILON = 0.005;

const round2 = (n) => Math.round(n * 100) / 100;

/** Coerce to a usable positive number, or null. Guards against `"3s"`, NaN, null, -1. */
function positiveNumber(value) {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Same, but 0 is a legitimate value — `## Cold open — 0:00` parses to atSec 0. */
function nonNegativeNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/**
 * Narration duration for a block of text.
 *
 * @param {string} text Words to be read aloud.
 * @param {number} [wpm=150] Reading pace the scripts are written to (see walkthroughs/README).
 * @returns {number} Seconds, floored at MIN_BEAT_SEC, rounded to 2dp.
 */
export function estimateSeconds(text, wpm = 150) {
  const words = String(text ?? '')
    .trim()
    .split(/\s+/)
    .filter(Boolean).length;
  const rate = positiveNumber(wpm) ?? 150;
  return round2(Math.max(MIN_BEAT_SEC, (words / rate) * 60));
}

/**
 * How long a single beat occupies the timeline.
 * `estSec` always wins when the parser supplied one — that is where `[HOLD] 8s` lands, and
 * where the parser's own narration estimate lands.
 */
function beatDuration(beat) {
  const explicit = positiveNumber(beat?.estSec);
  if (explicit !== null) return round2(explicit);
  if (beat?.kind === 'say') return estimateSeconds(beat?.text);
  return DEFAULT_DWELL_SEC[beat?.kind] ?? MIN_BEAT_SEC;
}

/**
 * Flatten an episode into an absolutely-timed beat list.
 *
 * @param {object} episode Parsed episode matching spec.schema.json.
 * @param {{fast?: boolean}} [opts] `fast` collapses every beat to <= FAST_MAX_SEC for smoke runs.
 * @returns {{
 *   beats: Array<object>,   // every beat, in order, with startSec, durSec, segmentHeading
 *   totalSec: number,       // wall-clock length of the plan
 *   drift: Array<{heading: string, expected: number, actual: number}>,
 *   fast: boolean
 * }}
 *
 * Beats carry their original fields plus:
 *   segmentHeading, segmentIndex, index, startSec, durSec, padSec
 * where `padSec` is slack absorbed from the following timecode (0 for most beats).
 */
export function planEpisode(episode, opts = {}) {
  const fast = Boolean(opts?.fast);
  const segments = Array.isArray(episode?.segments) ? episode.segments : [];

  const beats = [];
  const drift = [];
  let clock = 0;

  for (const [segmentIndex, segment] of segments.entries()) {
    const heading = String(segment?.heading ?? '');
    const atSec = nonNegativeNumber(segment?.atSec);

    if (atSec !== null) {
      if (clock < atSec - EPSILON) {
        // Ahead of the script. Rather than cutting to the next segment early and leaving a
        // hole later, spend the slack holding the shot we are already on — the viewer keeps
        // reading whatever is on screen and the segment still starts on its timecode.
        const slack = round2(atSec - clock);
        const previous = beats[beats.length - 1];
        if (previous) {
          previous.durSec = round2(previous.durSec + slack);
          previous.padSec = round2(previous.padSec + slack);
        }
        // With no previous beat (an episode whose first segment starts at a non-zero
        // timecode) there is nothing to hold on, so the timeline simply starts late.
        clock = round2(atSec);
      } else if (clock > atSec + EPSILON) {
        // Behind the script. Never compress narration to catch up — a 150wpm script read at
        // 190wpm is a worse video than one that runs thirty seconds long. Just record it.
        drift.push({ heading, expected: round2(atSec), actual: round2(clock) });
      }
    }

    const segmentBeats = Array.isArray(segment?.beats) ? segment.beats : [];
    for (const beat of segmentBeats) {
      const durSec = beatDuration(beat);
      beats.push({
        ...beat,
        segmentHeading: heading,
        segmentIndex,
        index: beats.length,
        startSec: round2(clock),
        durSec,
        padSec: 0,
      });
      clock = round2(clock + durSec);
    }
  }

  if (fast) {
    // Smoke run: same beats, same order, same overlay text — just no dwell. Timecodes are
    // recomputed from zero so the VTT still matches the (much shorter) video.
    let fastClock = 0;
    for (const beat of beats) {
      beat.durSec = round2(Math.min(beat.durSec, FAST_MAX_SEC));
      beat.padSec = 0; // slack is meaningless once every beat is capped
      beat.startSec = round2(fastClock);
      fastClock = round2(fastClock + beat.durSec);
    }
    clock = fastClock;
  }

  return { beats, totalSec: round2(clock), drift, fast };
}
