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
 * Engine preference, best first:
 *   kokoro     Neural, open weights, runs locally. What these videos are narrated with.
 *   say        macOS built-in. Decent.
 *   espeak-ng  Formant synthesis. Robotic, but available everywhere and offline.
 *
 * Kokoro needs two model files (~350MB total). Set KOKORO_MODEL / KOKORO_VOICES if they
 * are not in /opt/kokoro, and KOKORO_VOICE / KOKORO_SPEED to change how it reads.
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveFfmpeg } from './post.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const KOKORO_SCRIPT = join(HERE, 'tts', 'kokoro.py');

let cachedEngine = null;

function kokoroReady() {
  const model = process.env.KOKORO_MODEL || '/opt/kokoro/kokoro-v1.0.onnx';
  const voices = process.env.KOKORO_VOICES || '/opt/kokoro/voices-v1.0.bin';
  if (!existsSync(model) || !existsSync(voices) || !existsSync(KOKORO_SCRIPT)) return null;

  // The files existing proves nothing about the runtime; check the import too, so a
  // half-finished setup falls back instead of failing mid-episode.
  const python = process.env.PYTHON_BIN || 'python3';
  const probe = spawnSync(python, ['-c', 'import kokoro_onnx, soundfile'], { encoding: 'utf8' });
  if (probe.status !== 0) return null;
  return { name: 'kokoro', bin: python, batch: true, script: KOKORO_SCRIPT };
}

/**
 * @returns {{name: string, bin: string, batch?: boolean}|null} null when nothing usable.
 */
export function resolveTts() {
  if (cachedEngine !== null) return cachedEngine;

  if (process.env.TTS_BIN && existsSync(process.env.TTS_BIN)) {
    cachedEngine = { name: process.env.TTS_ENGINE || 'espeak-ng', bin: process.env.TTS_BIN };
    return cachedEngine;
  }

  const kokoro = kokoroReady();
  if (kokoro) {
    cachedEngine = kokoro;
    return cachedEngine;
  }

  const dirs = (process.env.PATH || '').split(':').filter(Boolean);
  // 'say' before espeak: on macOS it is markedly better.
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

/**
 * One process for the whole episode. Returns path -> measured duration; a line the
 * engine could not render is simply absent from the map.
 */
async function synthesizeBatch(engine, items) {
  const out = new Map();
  if (!items.length) return out;

  const payload = JSON.stringify(items.map((i) => ({ text: i.beat.text, path: i.path })));
  const result = await new Promise((resolve) => {
    const child = spawn(engine.bin, [engine.script], { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (c) => { stdout += String(c); });
    child.stderr.on('data', (c) => {
      stderr += String(c);
      if (stderr.length > 4000) stderr = stderr.slice(-4000);
    });
    child.on('error', () => resolve(null));
    child.on('close', () => resolve({ stdout, stderr }));
    child.stdin.end(payload);
  });

  if (!result) return out;
  let parsed;
  try {
    parsed = JSON.parse(result.stdout.trim().split('\n').pop() || '{}');
  } catch {
    return out;
  }
  if (parsed.error) return out;
  for (const entry of parsed.ok || []) out.set(entry.path, entry.durSec);
  return out;
}

/** One process per line. Fine for `say` and espeak, which start instantly. */
async function synthesizeEach(engine, items, wpm) {
  const out = new Map();
  for (const item of items) {
    try {
      await synthesize(engine, item.beat.text, item.path, wpm);
    } catch {
      continue;
    }
    const measured = audioDuration(item.path);
    if (measured) out.set(item.path, measured);
  }
  return out;
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
 * Synthesize every spoken beat in an episode and record the measured lengths on it.
 *
 * Called BEFORE recording: planEpisode reads `narratedSec` on the next pass, so real
 * audio durations flow back through the segment-timecode logic instead of bypassing
 * it. Returns the clips; placeClips() gives them start times once the plan is redone.
 *
 * @param {object} episode Mutated: spoken beats gain narratedSec and narrationPath.
 * @param {string} dir Where to write the wavs.
 * @param {{wpm?: number, padSec?: number}} [opts]
 */
export async function narrateEpisode(episode, dir, opts = {}) {
  const engine = resolveTts();
  if (!engine) return { available: false, engine: null, clips: [] };

  const wpm = opts.wpm || 150;
  // A line of speech with no gap after it runs straight into the next. This is the
  // breath between sentences.
  const pad = opts.padSec ?? 0.35;

  mkdirSync(dir, { recursive: true });

  // Walk the episode's own beats, not a plan's copies, so the measured durations are
  // written where planEpisode will read them on the next pass.
  const spoken = [];
  let n = 0;
  for (const segment of episode.segments || []) {
    for (const beat of segment.beats || []) {
      const index = n++;
      if (beat.kind !== 'say' || !beat.text) continue;
      spoken.push({ index, beat, path: join(dir, `beat-${String(index).padStart(3, '0')}.wav`) });
    }
  }

  // Batch engines load a large model once for the whole episode; per-line engines are
  // cheap to spawn repeatedly. Both yield the same path -> measured duration map.
  const measured = engine.batch
    ? await synthesizeBatch(engine, spoken)
    : await synthesizeEach(engine, spoken, wpm);

  const clips = [];
  for (const item of spoken) {
    const durSec = measured.get(item.path);
    if (!durSec) continue; // a line that failed keeps its estimate and plays silent
    item.beat.narratedSec = Math.round((durSec + pad) * 100) / 100;
    item.beat.narrationPath = item.path;
    clips.push({ path: item.path, durSec });
  }

  return { available: true, engine: engine.name, clips };
}

/**
 * Attach start times to clips from a re-planned timeline.
 *
 * Call after planEpisode has run again over the narrated durations: the plan is what
 * knows where each beat actually lands once segment timecodes have been honoured.
 */
export function placeClips(plan, clips) {
  const byPath = new Map(clips.map((c) => [c.path, c]));
  const placed = [];
  for (const beat of plan.beats) {
    const clip = beat.narrationPath && byPath.get(beat.narrationPath);
    if (clip) placed.push({ ...clip, startSec: beat.startSec });
  }
  return placed.sort((a, b) => a.startSec - b.startSec);
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
