/**
 * Turns a walkthrough recording script (walkthroughs/NN-*.md) into the episode
 * object described by spec.schema.json.
 *
 * The scripts are written for a human reading them off a second monitor, so the
 * markdown is loose on purpose: beat text wraps across lines, code fences hang
 * below the beat that introduces them, and section timecodes are decorative to a
 * reader but load-bearing to the recorder. Everything here exists to pull that
 * apart without making the scripts uglier to write.
 */

import { readdir, readFile } from 'node:fs/promises';
import { basename, join } from 'node:path';

const BEAT_KINDS = new Set(['screen', 'say', 'action', 'hold', 'callout']);

/** `# Episode 05 — Reading A Real Workflow` — em dash, en dash and hyphens all tolerated. */
const TITLE_RE = /^#\s+Episode\s+(\d{1,2})\s*(?:[—–]|-{1,2})\s*(.+?)\s*$/;

/** `**Target runtime:** 5:30` */
const META_RE = /^\*\*(.+?):\*\*\s*(.*)$/;

/** `## Cold open — 0:00`, with the timecode optional. */
const HEADING_RE = /^##\s+(.+?)(?:\s*(?:[—–]|-{1,2})\s*(\d+):([0-5]\d))?\s*$/;

/** ``​`[SAY]` "narration"`` — the tag is always fenced in single backticks. */
const BEAT_RE = /^`\[([A-Z]+)\]`\s*(.*)$/;

/** `[HOLD] 8s on the answer.` — leading duration, optional trailing description. */
const HOLD_RE = /^(\d+(?:\.\d+)?)\s*s\b[.,]?\s*(.*)$/i;

const FENCE_RE = /^```/;

const DEFAULT_HOLD_SEC = 3;

/**
 * Narration length at a given speaking rate. The scripts were written to 150wpm
 * (see walkthroughs/README.md), so this is the number the pacing engine trusts.
 */
export function estimateSeconds(text, wpm = 150) {
  const words = countWords(text);
  if (words === 0) return 0.9;
  return round2(Math.max(0.9, (words / wpm) * 60));
}

function countWords(text) {
  const t = String(text || '').trim();
  return t ? t.split(/\s+/).length : 0;
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

/** `4:00` -> 240. Returns null for anything that is not a timecode. */
function parseTimecode(value) {
  const m = /^(\d+):([0-5]\d)$/.exec(String(value || '').trim());
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

/**
 * Slug comes from the filename rather than the title: `05-analyze-a-real-workflow.md`
 * is what the operator types on the CLI and what names the output directory, so the
 * two must not drift apart when a title is reworded.
 */
function slugFromFilename(file) {
  const m = /^(\d{2})-(.+)\.md$/.exec(basename(file));
  return m ? { id: m[1], slug: m[2] } : null;
}

/** Strip the surrounding quotes the scripts wrap narration in. Inner quotes survive. */
function stripQuotes(text) {
  const t = text.trim();
  if (t.length >= 2) {
    const first = t[0];
    const last = t[t.length - 1];
    if ((first === '"' && last === '"') || (first === '“' && last === '”')) {
      return t.slice(1, -1).trim();
    }
  }
  return t;
}

/**
 * Dwell time for the beats that are not narration. These are deliberately generous:
 * a callout the viewer cannot finish reading is worse than one that lingers.
 */
function estimateBeatSeconds(kind, text, code) {
  switch (kind) {
    case 'say':
      return estimateSeconds(text);
    case 'callout':
      return round2(Math.max(2.5, countWords(text) / 2.5));
    case 'action': {
      const codeLines = code.reduce((n, block) => n + block.split('\n').length, 0);
      return round2(2 + 0.6 * codeLines);
    }
    case 'screen':
      return 2.5;
    default:
      return DEFAULT_HOLD_SEC;
  }
}

function finishBeat(beat) {
  if (!beat) return null;
  beat.text = beat.lines.join(' ').replace(/\s+/g, ' ').trim();
  delete beat.lines;
  if (beat.kind === 'say') beat.text = stripQuotes(beat.text);
  if (beat.kind === 'hold') {
    const m = HOLD_RE.exec(beat.text);
    if (m) {
      beat.estSec = Number(m[1]);
      beat.text = m[2].trim();
    } else {
      // 'a beat', 'a moment' — no explicit duration, so fall back rather than fail.
      beat.estSec = DEFAULT_HOLD_SEC;
    }
  } else {
    beat.estSec = estimateBeatSeconds(beat.kind, beat.text, beat.code);
  }
  if (!beat.code.length) delete beat.code;
  return beat;
}

/**
 * @param {string} markdown Raw contents of a walkthrough script.
 * @param {string} sourceFile Path used for slug/id and for error messages.
 * @returns {object} Episode matching spec.schema.json.
 */
export function parseEpisode(markdown, sourceFile, opts = {}) {
  const named = slugFromFilename(sourceFile);
  if (!named) {
    throw new Error(
      `${sourceFile}: filename must look like NN-some-slug.md so the episode id and output directory stay stable`,
    );
  }

  // '[NAME]' is a presenter placeholder in the scripts — a human reading aloud says their
  // own name there. A recording has no human, so it must be resolved or it renders literally
  // into a caption as the characters '[NAME]'.
  const presenter = opts.presenter || 'Claude Code';
  const lines = String(markdown).replace(/\[NAME\]/g, presenter).split(/\r?\n/);
  const episode = {
    id: named.id,
    slug: named.slug,
    title: null,
    sourceFile,
    targetRuntimeSec: null,
    gate: null,
    workflowPath: null,
    segments: [],
  };

  let segment = null;
  let beat = null;
  let inFence = false;
  let fence = null;
  let seenHeading = false;

  const closeBeat = () => {
    const done = finishBeat(beat);
    if (done) segment.beats.push(done);
    beat = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw.trimEnd();
    const lineNo = i + 1;

    // Fences are consumed wholesale and attached to the beat that introduced them,
    // so a code block never gets mistaken for narration.
    if (FENCE_RE.test(line.trim())) {
      if (inFence) {
        inFence = false;
        const body = fence.join('\n').trim();
        if (body) {
          if (beat) beat.code.push(body);
          else if (segment && segment.beats.length) {
            // Every script puts a blank line between the [ACTION] line and its fence,
            // which closes the beat before the fence is seen. Attaching the code is not
            // enough — the beat's duration was computed without it, so a four-line block
            // got the same 2.0s as an empty one while the fixture spent 3s typing it.
            const prev = segment.beats[segment.beats.length - 1];
            (prev.code || (prev.code = [])).push(body);
            if (prev.kind !== 'hold') prev.estSec = estimateBeatSeconds(prev.kind, prev.text, prev.code);
          }
        }
        fence = null;
      } else {
        inFence = true;
        fence = [];
      }
      continue;
    }
    if (inFence) {
      fence.push(raw);
      continue;
    }

    if (!episode.title) {
      const t = TITLE_RE.exec(line);
      if (t) {
        // Zero-pad so '# Episode 5' and a 05-*.md filename cannot disagree.
        const idFromTitle = t[1].padStart(2, '0');
        if (idFromTitle !== episode.id) {
          throw new Error(
            `${sourceFile}:${lineNo}: heading says Episode ${idFromTitle} but the filename says ${episode.id}`,
          );
        }
        episode.title = t[2];
        continue;
      }
    }

    const heading = !line.startsWith('###') && HEADING_RE.exec(line);
    if (heading) {
      if (beat) closeBeat();
      seenHeading = true;
      segment = {
        heading: heading[1].trim(),
        atSec: heading[2] !== undefined ? Number(heading[2]) * 60 + Number(heading[3]) : null,
        beats: [],
      };
      episode.segments.push(segment);
      continue;
    }

    // Metadata only counts above the first section heading; a bold line inside a
    // segment is emphasis in narration, not a field.
    if (!seenHeading) {
      const meta = META_RE.exec(line);
      if (meta) {
        const key = meta[1].trim().toLowerCase();
        const value = meta[2].trim();
        if (key === 'target runtime') episode.targetRuntimeSec = parseTimecode(value);
        else if (key === 'gate') episode.gate = value;
        else if (key === 'workflow') episode.workflowPath = value.replace(/^`|`$/g, '').trim();
        // Other keys (Section, Use when, Prerequisite) are for the human reader.
        continue;
      }
    }

    const beatMatch = BEAT_RE.exec(line);
    if (beatMatch) {
      const kind = beatMatch[1].toLowerCase();
      if (!BEAT_KINDS.has(kind)) {
        throw new Error(
          `${sourceFile}:${lineNo}: unknown beat tag [${beatMatch[1]}] — expected one of ${[...BEAT_KINDS].join(', ').toUpperCase()}`,
        );
      }
      if (!segment) {
        throw new Error(`${sourceFile}:${lineNo}: beat [${beatMatch[1]}] appears before any '## ' section heading`);
      }
      if (beat) closeBeat();
      beat = { kind, lines: beatMatch[2] ? [beatMatch[2]] : [], code: [] };
      continue;
    }

    // A blank line or a horizontal rule ends the current beat; anything else is a
    // wrapped continuation of it.
    if (!line.trim() || /^-{3,}$/.test(line.trim())) {
      if (beat) closeBeat();
      continue;
    }
    if (beat) beat.lines.push(line.trim());
  }

  if (beat) closeBeat();

  validateEpisode(episode);
  return episode;
}

/**
 * Hand-rolled against spec.schema.json's required fields — a malformed script must
 * fail loudly here rather than silently record an empty video.
 */
export function validateEpisode(episode) {
  const where = episode.sourceFile || episode.id;
  if (!episode.title) throw new Error(`${where}: no '# Episode NN — Title' heading found`);
  if (!/^\d{2}$/.test(episode.id)) throw new Error(`${where}: episode id '${episode.id}' is not two digits`);
  if (!episode.segments.length) throw new Error(`${where}: no '## ' sections found — nothing to record`);

  let beats = 0;
  for (const segment of episode.segments) {
    if (!segment.heading) throw new Error(`${where}: a section has an empty heading`);
    for (const b of segment.beats) {
      if (!BEAT_KINDS.has(b.kind)) throw new Error(`${where}: beat of unknown kind '${b.kind}' in '${segment.heading}'`);
      if (b.kind !== 'hold' && !b.text && !(b.code && b.code.length)) {
        throw new Error(`${where}: empty [${b.kind.toUpperCase()}] beat in section '${segment.heading}'`);
      }
      if (typeof b.estSec !== 'number' || !(b.estSec > 0)) {
        throw new Error(`${where}: beat in '${segment.heading}' has no usable duration`);
      }
      beats++;
    }
  }
  if (!beats) throw new Error(`${where}: parsed ${episode.segments.length} sections but zero beats`);
  return episode;
}

/**
 * @param {string} dir Directory holding the NN-*.md scripts.
 * @param {string[]|null} filter Episode ids to keep, e.g. ['00','05']. Null keeps all.
 */
export async function loadEpisodes(dir, filter = null, opts = {}) {
  const entries = await readdir(dir);
  const files = entries.filter((f) => /^\d{2}-.+\.md$/.test(f)).sort();
  if (!files.length) throw new Error(`${dir}: no walkthrough scripts (NN-*.md) found`);

  const wanted = filter && filter.length ? new Set(filter.map((f) => String(f).padStart(2, '0'))) : null;
  const episodes = [];
  for (const file of files) {
    const id = file.slice(0, 2);
    if (wanted && !wanted.has(id)) continue;
    const full = join(dir, file);
    episodes.push(parseEpisode(await readFile(full, 'utf8'), full, opts));
  }

  if (wanted) {
    const found = new Set(episodes.map((e) => e.id));
    const missing = [...wanted].filter((id) => !found.has(id));
    if (missing.length) throw new Error(`no script found for episode(s): ${missing.join(', ')}`);
  }
  return episodes;
}
