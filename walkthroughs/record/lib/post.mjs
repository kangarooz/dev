/**
 * Post-processing: Playwright records VP8 in a .webm, which is not what anyone wants
 * to drop into Slack or embed in Confluence. This turns a take into an H.264 mp4 that
 * plays everywhere, plus a WebVTT track so the narration is searchable and the video
 * is usable with the sound off.
 */

import { spawn, spawnSync } from 'node:child_process';
import { readdirSync, existsSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';

let cachedFfmpeg = null;
let cachedCapabilities = null;

/**
 * Resolution order matters. Playwright also ships an ffmpeg, but it is stripped down
 * to exactly what screen capture needs — VP8 into WebM, and the scale filter. It
 * cannot produce H.264 or an mp4 container, so it is the last resort rather than the
 * first, and `ffmpegCapabilities()` exists to tell that case apart from a real build.
 */
export function resolveFfmpeg() {
  if (cachedFfmpeg) return cachedFfmpeg;

  if (process.env.PW_FFMPEG && existsSync(process.env.PW_FFMPEG)) {
    cachedFfmpeg = process.env.PW_FFMPEG;
    return cachedFfmpeg;
  }

  // ffmpeg-static, when installed, is a full build and the one we want.
  const vendored = join(process.cwd(), 'node_modules', 'ffmpeg-static', 'ffmpeg');
  const local = new URL('../node_modules/ffmpeg-static/ffmpeg', import.meta.url).pathname;
  for (const candidate of [local, vendored]) {
    if (existsSync(candidate)) {
      cachedFfmpeg = candidate;
      return cachedFfmpeg;
    }
  }

  for (const dir of (process.env.PATH || '').split(':')) {
    if (dir && existsSync(join(dir, 'ffmpeg'))) {
      cachedFfmpeg = join(dir, 'ffmpeg');
      return cachedFfmpeg;
    }
  }

  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, '/opt/pw-browsers'].filter(Boolean);
  for (const root of roots) {
    if (!existsSync(root)) continue;
    for (const entry of readdirSync(root)) {
      if (!entry.startsWith('ffmpeg')) continue;
      const dir = join(root, entry);
      let bins = [];
      try {
        bins = readdirSync(dir).filter((f) => f.startsWith('ffmpeg'));
      } catch {
        continue;
      }
      if (bins.length) {
        cachedFfmpeg = join(dir, bins[0]);
        return cachedFfmpeg;
      }
    }
  }

  cachedFfmpeg = 'ffmpeg';
  return cachedFfmpeg;
}

/**
 * Ask the resolved binary what it can actually do, so callers degrade with an
 * explanation instead of surfacing `Unrecognized option 'preset'` from a build that
 * was never going to work.
 */
export function ffmpegCapabilities() {
  if (cachedCapabilities) return cachedCapabilities;
  const bin = resolveFfmpeg();
  const read = (args) => {
    const r = spawnSync(bin, args, { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
    return `${r.stdout || ''}${r.stderr || ''}`;
  };
  const encoders = read(['-hide_banner', '-encoders']);
  const muxers = read(['-hide_banner', '-muxers']);
  const filters = read(['-hide_banner', '-filters']);
  cachedCapabilities = {
    bin,
    h264: /libx264/.test(encoders),
    mp4: /\bmp4\b/.test(muxers),
    subtitles: /\bsubtitles\b/.test(filters),
  };
  cachedCapabilities.canMp4 = cachedCapabilities.h264 && cachedCapabilities.mp4;
  return cachedCapabilities;
}

function run(bin, args, { onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    const errTail = [];
    child.stderr.on('data', (chunk) => {
      const text = String(chunk);
      // ffmpeg reports everything on stderr, so keep a rolling tail for diagnostics
      // rather than buffering an entire encode's worth of output.
      for (const line of text.split(/\r?\n|\r/)) {
        if (!line.trim()) continue;
        errTail.push(line);
        if (errTail.length > 40) errTail.shift();
        const m = /time=(\d+):(\d\d):(\d\d(?:\.\d+)?)/.exec(line);
        if (m && onProgress) onProgress(Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]));
      }
    });
    child.on('error', (err) =>
      reject(new Error(`${bin}: ${err.message}${err.code === 'ENOENT' ? ' — install ffmpeg or run inside the container that bundles it' : ''}`)),
    );
    child.on('close', (code) => {
      if (code === 0) return resolve();
      reject(new Error(`ffmpeg exited ${code}\n${errTail.slice(-20).join('\n')}`));
    });
  });
}

function vttTime(seconds) {
  const s = Math.max(0, seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const whole = Math.floor(sec);
  const ms = Math.round((sec - whole) * 1000);
  // Carry a rounded-up millisecond rather than emitting '…:59.1000'.
  const carrySec = ms === 1000 ? whole + 1 : whole;
  const carryMs = ms === 1000 ? 0 : ms;
  return [
    String(h).padStart(2, '0'),
    String(m).padStart(2, '0'),
    String(carrySec).padStart(2, '0'),
  ].join(':') + '.' + String(carryMs).padStart(3, '0');
}

/** Wrap a cue to at most two readable lines; longer cues get truncated rather than wrapped to a wall. */
function wrapCue(text, width = 42, maxLines = 2) {
  const words = String(text).split(/\s+/).filter(Boolean);
  const lines = [];
  let line = '';
  for (const word of words) {
    if (!line.length) line = word;
    else if (line.length + 1 + word.length <= width) line += ' ' + word;
    else {
      lines.push(line);
      line = word;
      if (lines.length === maxLines) break;
    }
  }
  if (lines.length < maxLines && line) lines.push(line);
  const used = lines.join(' ').split(/\s+/).length;
  if (used < words.length) lines[lines.length - 1] += '…';
  return lines.join('\n');
}

/**
 * @param {{beats: Array<{kind: string, text: string, startSec: number, durSec: number}>}} plan
 * @param {string} outPath
 */
export function writeVtt(plan, outPath) {
  const cues = ['WEBVTT', ''];
  let n = 0;
  for (const beat of plan.beats) {
    if (beat.kind !== 'say' && beat.kind !== 'screen') continue;
    const text = (beat.text || '').trim();
    if (!text) continue;
    n++;
    cues.push(String(n));
    cues.push(`${vttTime(beat.startSec)} --> ${vttTime(beat.startSec + beat.durSec)}`);
    cues.push(wrapCue(text));
    cues.push('');
  }
  writeFileSync(outPath, cues.join('\n'), 'utf8');
  return outPath;
}

/** Filter-graph paths are colon-delimited, so a Windows drive letter or a stray colon breaks the graph. */
function escapeFilterPath(p) {
  return p.replace(/\\/g, '/').replace(/:/g, '\\:').replace(/'/g, "\\'");
}

/**
 * @param {string} webmPath Playwright's raw capture.
 * @param {string} outPath  Destination .mp4.
 * @param {{vttPath?: string, burnCaptions?: boolean, onProgress?: (s: number) => void}} opts
 */
export async function toMp4(webmPath, outPath, opts = {}) {
  const caps = ffmpegCapabilities();
  if (!caps.canMp4) {
    throw new Error(
      `${caps.bin} cannot produce mp4 (H.264 ${caps.h264 ? 'ok' : 'missing'}, mp4 muxer ${caps.mp4 ? 'ok' : 'missing'}).\n` +
        "This is Playwright's cut-down build. Run `npm install ffmpeg-static` in walkthroughs/record, " +
        'or point PW_FFMPEG at a full ffmpeg. The .webm capture is unaffected and plays in any browser.',
    );
  }
  const ffmpeg = caps.bin;
  // Playwright's capture size is not guaranteed even, and libx264 refuses odd
  // dimensions outright — so normalise before anything else in the chain.
  let filter = 'scale=trunc(iw/2)*2:trunc(ih/2)*2';
  if (opts.burnCaptions && opts.vttPath && existsSync(opts.vttPath) && caps.subtitles) {
    filter += `,subtitles='${escapeFilterPath(opts.vttPath)}':force_style='FontSize=18,Outline=1,Shadow=0,MarginV=28'`;
  }

  const args = [
    '-y', '-hide_banner', '-loglevel', 'info',
    '-i', webmPath,
    '-vf', filter,
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
    '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-level', '4.0',
    // Playwright's video track has no audio, so audio flags here would only fail.
    '-an',
    '-movflags', '+faststart',
    outPath,
  ];
  await run(ffmpeg, args, { onProgress: opts.onProgress });
  return outPath;
}

/**
 * Stream-copy concat. Safe because every input came out of toMp4 with identical
 * encoder settings; re-encoding a full series would cost minutes for nothing.
 */
export async function concat(mp4Paths, outPath) {
  if (!mp4Paths.length) throw new Error('concat: no inputs');
  if (mp4Paths.length === 1) {
    await run(resolveFfmpeg(), ['-y', '-hide_banner', '-loglevel', 'error', '-i', mp4Paths[0], '-c', 'copy', outPath]);
    return outPath;
  }
  const dir = mkdtempSync(join(tmpdir(), 'wt-concat-'));
  const listFile = join(dir, 'list.txt');
  try {
    writeFileSync(listFile, mp4Paths.map((p) => `file '${p.replace(/'/g, "'\\''")}'`).join('\n'), 'utf8');
    await run(resolveFfmpeg(), [
      '-y', '-hide_banner', '-loglevel', 'error',
      '-f', 'concat', '-safe', '0', '-i', listFile,
      '-c', 'copy', '-movflags', '+faststart', outPath,
    ]);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
  return outPath;
}

/** Probe a finished file so callers can assert on it instead of trusting the encoder. */
export async function probe(path) {
  const ffmpeg = resolveFfmpeg();
  const probeBin = join(dirname(ffmpeg), 'ffprobe');
  const bin = existsSync(probeBin) ? probeBin : ffmpeg;
  return new Promise((resolve) => {
    const args = bin === ffmpeg ? ['-hide_banner', '-i', path] : ['-hide_banner', '-i', path];
    const child = spawn(bin, args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let out = '';
    child.stderr.on('data', (c) => { out += String(c); });
    child.on('error', () => resolve(''));
    child.on('close', () => resolve(out));
  });
}
