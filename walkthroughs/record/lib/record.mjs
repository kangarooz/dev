/**
 * Records one episode.
 *
 * The recorder owns the timeline, the overlays and the capture; the target owns only
 * what is behind them. That split is what lets the same 13 scripts produce a real
 * training video against the live app and a reviewable one against the offline
 * fixture, without the scripts or this file knowing the difference.
 */

import { chromium } from 'playwright';
import { mkdir, rename, writeFile, readdir, access } from 'node:fs/promises';
import { existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { planEpisode } from './pace.mjs';
import { installOverlay, showCallout, showCaption, showChapter, clearOverlay } from './overlay.mjs';
import { writeVtt } from './post.mjs';
import { narratePlan } from './narrate.mjs';

const DEFAULT_VIEWPORT = { width: 1280, height: 720 };

/** Chapter card at the top of each new section, kept short so it never eats the narration. */
const SEGMENT_CARD_MS = 1400;

export async function recordEpisode(episode, opts = {}) {
  const {
    target,
    outDir,
    fast = false,
    headed = false,
    storageState = null,
    viewport = DEFAULT_VIEWPORT,
    onBeat = null,
    narrate = false,
  } = opts;

  if (!target) throw new Error('recordEpisode: no target supplied');

  // Preflight before Chromium launches: an unreachable target should cost nothing
  // and should explain itself, not surface as a navigation timeout two minutes in.
  await target.preflight();

  const episodeDir = join(outDir, `${episode.id}-${episode.slug}`);
  await mkdir(episodeDir, { recursive: true });

  const plan = planEpisode(episode, { fast });

  // Narration must be synthesized BEFORE recording: it replaces each spoken beat's
  // estimated duration with the measured length of its audio, and the recorder paces
  // to those numbers. Doing it afterwards would leave picture and voice out of step.
  let narration = { available: false, engine: null, clips: [] };
  if (narrate && !fast) {
    narration = await narratePlan(plan, join(episodeDir, '.audio'));
  }

  const vttPath = join(episodeDir, 'episode.vtt');
  writeVtt(plan, vttPath);

  const videoDir = join(episodeDir, '.capture');
  await mkdir(videoDir, { recursive: true });

  const started = Date.now();
  let browser = null;
  let context = null;
  let page = null;
  let failure = null;
  let videoPath = null;

  try {
    browser = await launchChromium(headed);
    const contextOpts = { viewport, recordVideo: { dir: videoDir, size: viewport } };
    if (storageState && (await exists(storageState))) contextOpts.storageState = storageState;
    context = await browser.newContext(contextOpts);
    page = await context.newPage();

    await target.open(page, episode);
    await assertNotBlank(page, episode);
    await installOverlay(page);
    await showChapter(page, episode.title, `Episode ${episode.id}`, 2600);
    await page.waitForTimeout(fast ? 300 : 2600);

    let currentSegment = null;
    for (let i = 0; i < plan.beats.length; i++) {
      const beat = plan.beats[i];
      const ms = Math.max(120, Math.round(beat.durSec * 1000));

      if (beat.segmentHeading !== currentSegment) {
        currentSegment = beat.segmentHeading;
        // Re-installing is cheap and idempotent; a target may have navigated.
        await installOverlay(page).catch(() => {});
        await showChapter(page, currentSegment, null, SEGMENT_CARD_MS);
        await page.waitForTimeout(fast ? 150 : SEGMENT_CARD_MS);
      }

      if (onBeat) onBeat(beat, i, plan.beats.length);
      await runBeat(page, target, beat, { episode, index: i }, ms, fast);
    }

    await clearOverlay(page).catch(() => {});
    await page.waitForTimeout(fast ? 150 : 900);
  } catch (err) {
    // A crashed take still has value: keep whatever was captured and record why.
    failure = err;
  } finally {
    // The video file is only finalised on context.close(), and its name is chosen by
    // Playwright — so resolve the path first, then close, then move it.
    let rawVideoPath = null;
    try {
      if (page) rawVideoPath = await page.video()?.path();
    } catch {
      rawVideoPath = null;
    }
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    if (typeof target.close === 'function') await Promise.resolve(target.close(page)).catch(() => {});

    videoPath = await settleVideo(videoDir, episodeDir, rawVideoPath);
  }

  const elapsed = (Date.now() - started) / 1000;
  const manifest = {
    ok: !failure,
    error: failure ? String(failure.message || failure) : null,
    episode: { id: episode.id, slug: episode.slug, title: episode.title, sourceFile: episode.sourceFile },
    target: target.constructor.id,
    beats: plan.beats.length,
    plannedSec: plan.totalSec,
    actualSec: Math.round(elapsed * 100) / 100,
    drift: plan.drift,
    narration: { requested: !!narrate, available: narration.available, engine: narration.engine, clips: narration.clips.length },
    videoPath,
    vttPath,
    recordedAt: new Date().toISOString(),
  };
  const manifestPath = join(episodeDir, 'manifest.json');
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n', 'utf8');

  if (failure && !videoPath) throw failure;
  return {
    videoPath, vttPath, manifestPath, totalSec: plan.totalSec,
    ok: !failure, error: manifest.error,
    narration, plan,
  };
}

/**
 * Prefer whatever browser this Playwright install manages. In a preconfigured
 * container the two can disagree — the npm package is newer than the browsers baked
 * into the image — so fall back to the Chromium that is actually on disk instead of
 * telling the operator to download one. PW_CHROMIUM overrides both.
 */
async function launchChromium(headed) {
  const opts = { headless: !headed };
  if (process.env.PW_CHROMIUM) return chromium.launch({ ...opts, executablePath: process.env.PW_CHROMIUM });

  try {
    return await chromium.launch(opts);
  } catch (err) {
    const fallback = findBundledChromium();
    if (!fallback) throw err;
    return chromium.launch({ ...opts, executablePath: fallback });
  }
}

function findBundledChromium() {
  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, '/opt/pw-browsers'].filter(Boolean);
  for (const root of roots) {
    for (const candidate of [join(root, 'chromium'), join(root, 'chromium', 'chrome-linux', 'chrome')]) {
      if (existsSync(candidate)) return candidate;
    }
    let entries = [];
    try {
      entries = readdirSync(root).filter((e) => e.startsWith('chromium-'));
    } catch {
      continue;
    }
    for (const entry of entries.sort().reverse()) {
      for (const rel of [['chrome-linux', 'chrome'], ['chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium']]) {
        const candidate = join(root, entry, ...rel);
        if (existsSync(candidate)) return candidate;
      }
    }
  }
  return null;
}

/**
 * Refuse to film nothing.
 *
 * The failure worth guarding against is not a crash — it is a take that reports
 * success having recorded a blank page for four minutes. That happened for real: a
 * target cached the screen it was on, the next episode's navigation was skipped as
 * redundant, and the recorder cheerfully filmed an empty document. Cheap to check
 * once at the top of an episode, and it converts an invisible failure into a loud one.
 */
async function assertNotBlank(page, episode) {
  const state = await page
    .evaluate(() => ({
      url: location.href,
      text: (document.body?.innerText || '').trim().length,
      nodes: document.body ? document.body.querySelectorAll('*').length : 0,
    }))
    .catch(() => null);

  if (!state) throw new Error(`${episode.id}: page could not be inspected after target.open()`);
  if (state.url === 'about:blank' || (state.text === 0 && state.nodes < 3)) {
    throw new Error(
      `${episode.id}: target.open() left an empty page (${state.url}, ${state.nodes} nodes) — ` +
        'refusing to record a blank take',
    );
  }
}

async function runBeat(page, target, beat, ctx, ms, fast) {
  try {
    switch (beat.kind) {
      case 'callout':
        await showCallout(page, beat.text, ms);
        break;

      case 'hold':
        // A HOLD means "let the viewer read the screen" — overlays would defeat it.
        await clearOverlay(page);
        break;

      case 'action': {
        const handled = await target.action?.(page, beat, ctx);
        const label = beat.code?.length ? beat.code.join('\n').split('\n').slice(0, 3).join(' ') : beat.text;
        if (!handled && label) await showCaption(page, label, ms);
        break;
      }

      case 'say':
      case 'screen':
      default:
        if (beat.text) await showCaption(page, beat.text, ms);
        break;
    }
  } catch {
    // A beat that fails to render must not end the take; the narration timeline
    // still advances and the rest of the episode still records.
  }
  await page.waitForTimeout(fast ? Math.min(ms, 400) : ms);
}

/** Move Playwright's generated .webm to a deterministic name we can hand to ffmpeg. */
async function settleVideo(videoDir, episodeDir, rawVideoPath) {
  const target = join(episodeDir, 'episode.webm');
  let source = rawVideoPath;
  if (!source || !(await exists(source))) {
    try {
      const found = (await readdir(videoDir)).filter((f) => f.endsWith('.webm'));
      source = found.length ? join(videoDir, found[0]) : null;
    } catch {
      source = null;
    }
  }
  if (!source || !(await exists(source))) return null;
  try {
    await rename(source, target);
    return target;
  } catch {
    return source;
  }
}

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}
