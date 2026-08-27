/**
 * Optional narration track.
 *
 * Without this the videos carry captions and a VTT and are watched in silence. With
 * `--narrate` each [SAY] beat is synthesized to audio, and — the part that matters —
 * the beat's duration becomes the MEASURED length of that audio rather than a word
 * count at 150wpm. Estimated pacing is close but not close enough: a 33-word line
 * measured 11.35s against a 13.2s estimate, and errors like that accumulate across a
 * five-minute episode until the captions drift away from the voice.
 *
 * Engine quality varies enormously and the operator should choose deliberately:
 *   say        macOS built-in. Good. Use this one if you are on a Mac.
 *   espeak-ng  Formant synthesis. Robotic, but available everywhere and offline.
 * Anything neural needs model files from a CDN, which an offline or locked-down host
 * will not have.
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { resolveFfmpeg } from './post.mjs';

let cachedEngine = null;

/**
 * @returns {{name: string, bin: string}|null} null when nothing usable is installed.
 */
export function resolveTts() {
  if (cachedEngine !== null) return cachedEngine;

  if (process.env.TTS_BIN && existsSync(process.env.TTS_BIN)) {
    cachedEngine = { name: process.env.TTS_ENGINE || 'espeak-ng', bin: process.env.TTS_BIN };
    return cachedEngine;
  }

  const dirs = (process.env.PATH || '').split(':').filter(Boolean);
  // 'say' first: on macOS it is markedly better than espeak, and the operator's own
  // machine is where the publishable takes get recorded.
  for (const name of ['say', 'espeak-ng', 'espeak']) {
    for (const dir of dirs) {
      const candidate = join(dir, name);
      if (existsSync(candidate)) {
        cachedEngine = { name, bin: candidate };
        return cachedEngine;
      }
    }
  }
  cachedEngine = null;
  return null;
}

export function narrationAvailable() {
  return resolveTts() !== null;
}

function run(bin, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let err = '';
    child.stderr.on('data', (c) => {
      err += String(c);
      if (err.length > 4000) err = err.slice(-4000);
    });
    child.on('error', reject);
    child.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`${bin} exited ${code}: ${err.slice(-400)}`))));
  });
}

/**
 * Synthesize one line to a wav.
 * @param {{name: string, bin: string}} engine
 * @param {number} wpm Speaking rate. The scripts are written for ~150.
 */
async function synthesize(engine, text, outPath, wpm = 150) {
  if (engine.name === 'say') {
    // macOS `say` writes AIFF natively; --data-format coaxes a plain wav out of it.
    await run(engine.bin, ['-r', String(wpm), '--data-format=LEI16@22050', '-o', outPath, text]);
    return;
  }
  await run(engine.bin, ['-v', 'en-us', '-s', String(wpm), '-w', outPath, text]);
}

/** Measured length of an audio file, in seconds. */
export function audioDuration(path) {
  const ffmpeg = resolveFfmpeg();
  const r = spawnSync(ffmpeg, ['-hide_banner', '-i', path], { encoding: 'utf8' });
  const out = `${r.stdout || ''}${r.stderr || ''}`;
  const m = /Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)/.exec(out);
  if (!m) return null;
  return Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]);
}

/**
 * Synthesize every spoken beat in a plan and rewrite the timeline around the real
 * audio lengths.
 *
 * Called BEFORE recording, because the durations it measures are what the recorder
 * paces to. Returns the clips so the caller can build a track once the video exists.
 *
 * @param {{beats: Array}} plan Mutated in place: durSec and startSec are corrected.
 * @param {string} dir Where to write the wavs.
 * @param {{wpm?: number, padSec?: number}} opts
 */
export async function narratePlan(plan, dir, opts = {}) {
  const engine = resolveTts();
  if (!engine) return { available: false, engine: null, clips: [] };

  const wpm = opts.wpm || 150;
  // A beat of pure speech with no gap after it runs into the next line. This is the
  // breath between sentences.
  const pad = opts.padSec ?? 0.35;

  mkdirSync(dir, { recursive: true });
  const clips = [];

  for (let i = 0; i < plan.beats.length; i++) {
    const beat = plan.beats[i];
    if (beat.kind !== 'say' || !beat.text) continue;
    const path = join(dir, `beat-${String(i).padStart(3, '0')}.wav`);
    try {
      await synthesize(engine, beat.text, path, wpm);
    } catch {
      // One failed line must not cost the whole episode its audio; that beat simply
      // keeps its estimated duration and plays silent.
      continue;
    }
    const measured = audioDuration(path);
    if (!measured) continue;
    beat.durSec = Math.round((measured + pad) * 100) / 100;
    beat.narrationPath = path;
    clips.push({ index: i, path, durSec: measured });
  }

  // Durations changed, so every start time after the first spoken beat is now wrong.
  let clock = 0;
  for (const beat of plan.beats) {
    beat.startSec = Math.round(clock * 100) / 100;
    clock += beat.durSec;
  }
  plan.totalSec = Math.round(clock * 100) / 100;
  for (const clip of clips) clip.startSec = plan.beats[clip.index].startSec;

  return { available: true, engine: engine.name, clips };
}

/**
 * Lay the clips onto one silent track at their start times.
 *
 * adelay per clip plus amix would drift and attenuate; concatenating explicit silence
 * gaps keeps every line exactly where the recorder put it.
 */
export async function buildTrack(clips, totalSec, outPath) {
  if (!clips.length) return null;
  const ffmpeg = resolveFfmpeg();

  const inputs = [];
  const filters = [];
  let cursor = 0;
  let n = 0;

  for (const clip of clips.sort((a, b) => a.startSec - b.startSec)) {
    const gap = Math.max(0, clip.startSec - cursor);
    if (gap > 0.01) {
      filters.push(`anullsrc=r=22050:cl=mono:d=${gap.toFixed(3)}[s${n}]`);
      n++;
    }
    inputs.push('-i', clip.path);
    cursor = clip.startSec + clip.durSec;
  }

  const tail = Math.max(0, totalSec - cursor);
  if (tail > 0.01) filters.push(`anullsrc=r=22050:cl=mono:d=${tail.toFixed(3)}[s${n}]`);

  // Rebuild the concat order: silence and clips interleaved as laid out above.
  const parts = [];
  let silenceIdx = 0;
  let clipIdx = 0;
  cursor = 0;
  for (const clip of clips) {
    const gap = Math.max(0, clip.startSec - cursor);
    if (gap > 0.01) parts.push(`[s${silenceIdx++}]`);
    parts.push(`[${clipIdx++}:a]`);
    cursor = clip.startSec + clip.durSec;
  }
  if (tail > 0.01) parts.push(`[s${silenceIdx}]`);

  const graph = `${filters.join(';')}${filters.length ? ';' : ''}${parts.join('')}concat=n=${parts.length}:v=0:a=1[out]`;

  await run(ffmpeg, [
    '-y', '-hide_banner', '-loglevel', 'error',
    ...inputs,
    '-filter_complex', graph,
    '-map', '[out]',
    '-ar', '22050', '-ac', '1',
    outPath,
  ]);
  return outPath;
}
