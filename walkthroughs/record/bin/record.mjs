#!/usr/bin/env node
/**
 * CLI for the walkthrough recorder.
 *
 * Designed to be runnable in one shot from a phone over SSH — every command has
 * working defaults, nothing prompts except `auth`, and progress is printed as plain
 * lines rather than anything that needs a real terminal.
 */

import { parseArgs } from 'node:util';
import { mkdir, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline/promises';

import { loadEpisodes } from '../lib/parse.mjs';
import { planEpisode } from '../lib/pace.mjs';
import { recordEpisode } from '../lib/record.mjs';
import { toMp4, concat, mux } from '../lib/post.mjs';
import { buildTrack, narrationAvailable, resolveTts } from '../lib/narrate.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const PROJECT = join(HERE, '..');
const SCRIPTS_DIR = join(PROJECT, '..');
const DEFAULT_OUT = join(PROJECT, 'out');
const AUTH_DIR = join(PROJECT, 'auth');

const TARGETS = {
  fixture: () => import('../lib/targets/fixture.mjs'),
  lol: () => import('../lib/targets/lol.mjs'),
  gitlab: () => import('../lib/targets/gitlab.mjs'),
};

const OPTIONS = {
  target: { type: 'string', default: 'fixture' },
  episode: { type: 'string' },
  all: { type: 'boolean', default: false },
  out: { type: 'string' },
  fast: { type: 'boolean', default: false },
  headed: { type: 'boolean', default: false },
  'base-url': { type: 'string' },
  'repo-dir': { type: 'string' },
  presenter: { type: 'string' },
  narrate: { type: 'boolean', default: false },
  mp4: { type: 'boolean', default: false },
  'burn-captions': { type: 'boolean', default: false },
  concat: { type: 'boolean', default: false },
  help: { type: 'boolean', default: false, short: 'h' },
};

const USAGE = `
Socrates walkthrough recorder

  record   [--target fixture|lol|gitlab] [--episode 00,05 | --all] [--out DIR]
           [--fast] [--headed] [--base-url URL] [--repo-dir PATH]
           [--mp4] [--burn-captions] [--concat] [--presenter NAME] [--narrate]
  auth     --target lol|gitlab [--base-url URL]   save a login session for later runs
  list                                            show the episodes and their timings
  check    --target <t>                           preflight only: is the target reachable

Examples
  npm run record -- --target fixture --all --mp4
  npm run record -- --target lol --episode 00 --headed
  node bin/record.mjs check --target lol
`;

async function loadTarget(name, opts) {
  const loader = TARGETS[name];
  if (!loader) {
    throw new Error(`unknown target '${name}' — expected one of: ${Object.keys(TARGETS).join(', ')}`);
  }
  const mod = await loader();
  const Ctor = mod.default;
  return new Ctor(opts);
}

function parseEpisodeFilter(value, all) {
  if (all || !value) return null;
  return value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => s.padStart(2, '0'));
}

function fmt(seconds) {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

async function cmdList() {
  const episodes = await loadEpisodes(SCRIPTS_DIR);
  console.log(`\n${episodes.length} episodes in ${SCRIPTS_DIR}\n`);
  console.log('  id  planned  target   beats  title');
  let total = 0;
  for (const ep of episodes) {
    const plan = planEpisode(ep, { fast: false });
    total += plan.totalSec;
    console.log(
      `  ${ep.id}  ${fmt(plan.totalSec).padStart(7)}  ${(ep.targetRuntimeSec ? fmt(ep.targetRuntimeSec) : '—').padStart(6)}   ${String(plan.beats.length).padStart(4)}   ${ep.title}`,
    );
  }
  console.log(`\n  series runtime: ${fmt(total)}\n`);
}

async function cmdCheck(values) {
  const target = await loadTarget(values.target, targetOpts(values));
  try {
    await target.preflight();
    console.log(`${values.target}: reachable`);
    return 0;
  } catch (err) {
    console.error(`${values.target}: NOT reachable\n\n${err.message}\n`);
    return 2;
  } finally {
    if (typeof target.close === 'function') await Promise.resolve(target.close(null)).catch(() => {});
  }
}

function targetOpts(values) {
  return {
    baseUrl: values['base-url'],
    repoDir: values['repo-dir'],
    log: (s) => console.log(`    ${s}`),
  };
}

async function cmdAuth(values) {
  const { chromium } = await import('playwright');
  await mkdir(AUTH_DIR, { recursive: true });
  const statePath = join(AUTH_DIR, `${values.target}.json`);
  const baseUrl = values['base-url'] || (values.target === 'gitlab' ? 'https://gitlab.com' : 'https://lol.legionintel.com');

  console.log(`\nOpening a browser at ${baseUrl}`);
  console.log('Log in as you normally would, then come back here and press Enter.');
  console.log('The session is saved locally so later runs need no interaction.\n');

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(baseUrl).catch((err) => console.error(`  navigation failed: ${err.message}`));

  const rl = createInterface({ input: process.stdin, output: process.stdout });
  await rl.question('Press Enter once you are logged in… ');
  rl.close();

  await context.storageState({ path: statePath });
  await browser.close();
  console.log(`\nSaved ${statePath}\n`);
  return 0;
}

async function cmdRecord(values) {
  const outDir = resolve(values.out || DEFAULT_OUT);
  const filter = parseEpisodeFilter(values.episode, values.all);
  const episodes = await loadEpisodes(SCRIPTS_DIR, filter, { presenter: values.presenter });
  const target = await loadTarget(values.target, targetOpts(values));
  const storageState = join(AUTH_DIR, `${values.target}.json`);

  try {
    await target.preflight();
  } catch (err) {
    console.error(`\n${values.target}: cannot record — target not reachable.\n\n${err.message}\n`);
    return 2;
  }

  await mkdir(outDir, { recursive: true });
  if (values.narrate) {
    const engine = resolveTts();
    if (!engine) {
      console.error('\n--narrate: no speech engine found. Install espeak-ng, or use macOS `say`.\n');
      return 2;
    }
    if (values.fast) console.log('  note: --fast skips narration (nothing to sync to)');
    else console.log(`  narrating with ${engine.name}`);
  }
  console.log(`\nRecording ${episodes.length} episode(s) against '${values.target}' into ${outDir}${values.fast ? ' [fast]' : ''}\n`);

  const results = [];
  for (const ep of episodes) {
    const plan = planEpisode(ep, { fast: values.fast });
    process.stdout.write(`  ${ep.id} ${ep.title} — ${plan.beats.length} beats, ~${fmt(plan.totalSec)} … `);
    const startedAt = Date.now();
    try {
      const res = await recordEpisode(ep, {
        target,
        outDir,
        fast: values.fast,
        headed: values.headed,
        storageState: existsSync(storageState) ? storageState : null,
        narrate: values.narrate,
      });
      let mp4 = null;
      if (values.mp4 && res.videoPath) {
        mp4 = res.videoPath.replace(/\.webm$/, '.mp4');
        await toMp4(res.videoPath, mp4, { vttPath: res.vttPath, burnCaptions: values['burn-captions'] });
        if (res.narration?.clips?.length) {
          const track = join(dirname(mp4), 'narration.wav');
          await buildTrack(res.narration.clips, res.totalSec, track);
          const withAudio = mp4.replace(/\.mp4$/, '-narrated.mp4');
          await mux(mp4, track, withAudio);
          mp4 = withAudio;
        }
      }
      const took = (Date.now() - startedAt) / 1000;
      console.log(res.ok ? `ok (${fmt(took)})` : `PARTIAL (${fmt(took)}) — ${res.error}`);
      results.push({ ep, ...res, mp4 });
    } catch (err) {
      console.log('FAILED');
      console.error(`      ${err.message}`);
      results.push({ ep, ok: false, error: err.message });
    }
  }

  if (values.concat) {
    const mp4s = results.map((r) => r.mp4).filter(Boolean);
    if (mp4s.length > 1) {
      const full = join(outDir, 'full-series.mp4');
      await concat(mp4s, full);
      console.log(`\n  series: ${full}`);
    } else {
      console.log('\n  --concat skipped: needs at least two --mp4 outputs');
    }
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n  ${results.length - failed.length}/${results.length} recorded${failed.length ? `, ${failed.length} with problems` : ''}`);
  for (const r of results) {
    if (r.videoPath) console.log(`    ${r.ep.id}  ${r.mp4 || r.videoPath}`);
  }
  console.log('');
  return failed.length ? 1 : 0;
}

async function main() {
  const argv = process.argv.slice(2);
  const command = argv[0] && !argv[0].startsWith('-') ? argv.shift() : 'record';

  let parsed;
  try {
    parsed = parseArgs({ args: argv, options: OPTIONS, allowPositionals: false });
  } catch (err) {
    console.error(`\n${err.message}\n${USAGE}`);
    process.exit(2);
  }
  const { values } = parsed;
  if (values.help) {
    console.log(USAGE);
    process.exit(0);
  }

  switch (command) {
    case 'list':
      await cmdList();
      return 0;
    case 'check':
      return cmdCheck(values);
    case 'auth':
      return cmdAuth(values);
    case 'record':
      return cmdRecord(values);
    default:
      console.error(`unknown command '${command}'\n${USAGE}`);
      return 2;
  }
}

main()
  .then((code) => process.exit(code || 0))
  .catch((err) => {
    console.error(`\n${err.message}\n`);
    process.exit(1);
  });
