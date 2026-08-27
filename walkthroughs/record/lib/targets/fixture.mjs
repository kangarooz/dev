/**
 * The offline target: a local stand-in for the agent-builder UI the scripts describe.
 *
 * This exists so the whole pipeline is provable — and recordable — with no VPN, no
 * credentials and no network at all. A take against the fixture is structurally the
 * same video as a take against the real app: same beats, same pacing, same overlays.
 * Only the pixels behind them are a reconstruction.
 */

import { createServer } from 'node:http';
import { readFile, access } from 'node:fs/promises';
import { extname, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Target } from './base.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = join(HERE, '..', '..', 'fixture');

const MIME = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json' };

export default class FixtureTarget extends Target {
  static id = 'fixture';

  constructor(opts = {}) {
    super(opts);
    this.server = null;
    this.origin = null;
    this.current = null;
  }

  async preflight() {
    for (const file of ['index.html', 'workflow.html', 'terminal.html', 'logs.html']) {
      try {
        await access(join(FIXTURE_DIR, file));
      } catch {
        throw new Error(`fixture: missing ${join(FIXTURE_DIR, file)} — the offline fixture site is incomplete`);
      }
    }
  }

  /**
   * Served over http rather than file:// so the page has a real origin: navigation,
   * history and storage all behave the way they will against the real app.
   */
  async start() {
    if (this.server) return this.origin;
    this.server = createServer(async (req, res) => {
      const name = (req.url || '/').split('?')[0].replace(/^\/+/, '') || 'index.html';
      try {
        const body = await readFile(join(FIXTURE_DIR, name));
        res.writeHead(200, { 'content-type': MIME[extname(name)] || 'application/octet-stream' });
        res.end(body);
      } catch {
        res.writeHead(404, { 'content-type': 'text/plain' });
        res.end('not found');
      }
    });
    // Port 0: the OS picks a free port, so concurrent runs never collide.
    await new Promise((resolve) => this.server.listen(0, '127.0.0.1', resolve));
    this.origin = `http://127.0.0.1:${this.server.address().port}`;
    return this.origin;
  }

  async open(page, episode) {
    await this.start();
    this.episode = episode;
    // Always navigate, never dedupe: this is a brand-new page for a new episode, and
    // the cached screen name says nothing about what THIS page has loaded.
    await this.#goto(page, withEpisode(openingScreen(episode), episode), { force: true });
    if (episode.workflowPath) {
      await page.evaluate((p) => window.fixture?.setPath?.(p), episode.workflowPath).catch(() => {});
    }
    await this.#assertRendered(page);
  }

  /**
   * Navigate, skipping the load when we are already on that screen mid-episode.
   *
   * `force` exists because the dedupe is only safe WITHIN one episode. The target
   * instance is reused across all 13, so a stale `this.current` from the previous
   * episode would match the next episode's opening screen and skip the only
   * navigation a fresh page ever gets — recording a blank white page while reporting
   * success. close() clears it too; this is the belt to that braces.
   */
  async #goto(page, file, { force = false } = {}) {
    if (!force && this.current === file) return;
    // this.current keeps the query string so a dataset switch re-navigates; screen
    // checks elsewhere compare against the bare filename via screenIs().
    await page.goto(`${this.origin}/${file}`, { waitUntil: 'domcontentloaded', timeout: 15000 });
    this.current = file;
  }

  /**
   * A fixture screen that loads but renders nothing is the failure this cannot ship
   * with: the page still has a header, so the recorder's generic blank-page check
   * passes and four minutes of empty JSON viewer get filmed as a success. A stray
   * missing comma in the dataset literal did exactly that. Each screen therefore has
   * to prove it drew its own content.
   */
  async #assertRendered(page) {
    const screen = (this.current || '').split('?')[0];
    const counts = {
      'workflow.html': () => document.querySelectorAll('tr').length,
      'logs.html': () => document.querySelectorAll('.step').length,
      'terminal.html': () => document.querySelectorAll('.prompt').length,
      'index.html': () => document.querySelectorAll('.sec').length,
    };
    if (!counts[screen]) return;
    const n = await page.evaluate(counts[screen]).catch(() => 0);
    if (!n) {
      const errors = await page.evaluate(() => window.__fixtureError || null).catch(() => null);
      throw new Error(
        `fixture: ${screen} loaded but rendered no content${errors ? ` (${errors})` : ''} — ` +
          'usually a syntax error in the page\'s inline script',
      );
    }
  }

  /**
   * Drive the fixture for the beats the scripts actually contain. Anything not
   * recognised returns false, and the recorder falls back to dwelling on the current
   * screen — a beat we cannot act out is still a beat the narration covers.
   */
  async action(page, beat, ctx) {
    const text = (beat.text || '').toLowerCase();
    const code = (beat.code || []).join('\n').trim();
    const has = (...words) => words.some((w) => text.includes(w));

    // The run inspector: episode 07 is entirely about reading execution logs, so its
    // beats drive a real run rather than narrating over an unrelated screen.
    if (screenIs(this.current, 'logs.html')) {
      if (has('run level', 'execution level', 'execution-level', 'overall execution', 'failed run')) {
        await page.evaluate(() => window.fixture?.selectRun?.('bad')).catch(() => {});
        return true;
      }
      if (has('successful run')) {
        await page.evaluate(() => window.fixture?.selectRun?.('ok')).catch(() => {});
        return true;
      }
      if (has('skipped')) {
        await page.evaluate(() => window.fixture?.showSkipped?.()).catch(() => {});
        return true;
      }
      if (has('step level', 'input', 'output', 'symptom', 'debugging workflow', 'fingerprint')) {
        await page.evaluate(() => window.fixture?.showCulprit?.()).catch(() => {});
        return true;
      }
      if (has('scroll')) {
        return scrollViewport(page, 300);
      }
      return false;
    }

    // Pointing at a step id or state key in the JSON viewer.
    if (ctx.episode.workflowPath) {
      const needle = pickNeedle(text, code);
      if (needle) {
        const hits = await page.evaluate((n) => window.fixture?.highlight?.(n) ?? 0, needle).catch(() => 0);
        if (hits) return true;
      }
      if (has('scroll')) {
        return scrollViewport(page, 320);
      }
      return false;
    }

    // Shell work belongs on a terminal, not in the agent's composer. Episode 00 clones
    // the repo and lists the skills directory before the app is ever opened.
    if (code && isShell(code)) {
      await this.#goto(page, 'terminal.html');
      await this.#terminal(page, code);
      return true;
    }
    // Episode 00 spends its back half in the app: starting the session, pasting the
    // bootstrap, the stop instruction. None of those beats carry a code fence, so
    // requiring one pinned the camera to a static shell prompt for 2:20 of a 4:14
    // episode while the narration described a screen the viewer never saw.
    if (screenIs(this.current, 'terminal.html') && (has('session', 'paste', 'bootstrap', 'agent', 'send', 'skills are visible') || (code && !isShell(code)))) {
      await this.#goto(page, withEpisode('index.html', ctx.episode), { force: true });
    }

    if (has('dropdown', 'select `workflow_builder`', 'workflow_builder')) {
      await page.evaluate(() => window.fixture?.selectBuilder?.('workflow_builder')).catch(() => {});
      return true;
    }
    if (has('activate')) {
      await page.evaluate(() => window.fixture?.activate?.()).catch(() => {});
      return true;
    }

    // Pasting a prompt. The scripts say "Fresh session, bootstrap, then paste prompt 3"
    // and carry NO code fence, so gating this on `code` meant the single most important
    // action in every episode never happened. Fall back to the episode's real prompt
    // text from the Socrates guide.
    if (has('paste', 'type', 'send', 'prompt', 'follow up', 'follow-up')) {
      const text = code || (await page.evaluate(() => window.fixture?.prompt?.() || '').catch(() => ''));
      if (text) {
        await this.#compose(page, text);
        return true;
      }
    }

    if (has('scroll')) {
      // Only claim the beat if the screen genuinely moved. Returning true on a no-op
      // suppresses the compensating caption in record.mjs, leaving the viewer with a
      // frozen frame and no words — strictly worse than an unhandled beat.
      const moved = await page.evaluate(() => {
        const t = document.getElementById('transcript');
        if (!t) return false;
        const before = t.scrollTop;
        t.scrollTop = Math.min(before + 260, t.scrollHeight);
        return t.scrollTop !== before;
      }).catch(() => false);
      return moved;
    }
    return false;
  }

  /** Type each command at the prompt, then run it, so the video shows the keystrokes. */
  async #terminal(page, code) {
    for (const command of code.split('\n').map((s) => s.trim()).filter(Boolean)) {
      await page.evaluate(() => window.fixture?.setCommand?.('')).catch(() => {});
      // Drive the live prompt through the fixture's own API. Reaching into the DOM for
      // '.cmd:last-of-type' looks equivalent and is not: :last-of-type is scoped to
      // siblings, so it matches an already-rendered line and every keystroke lands there.
      for (let i = 1; i <= command.length; i++) {
        await page.evaluate((prefix) => window.fixture?.setCommand?.(prefix), command.slice(0, i)).catch(() => {});
        await page.waitForTimeout(18);
      }
      await page.waitForTimeout(220);
      await page.evaluate((c) => window.fixture?.run?.(c), command).catch(() => {});
      await page.waitForTimeout(320);
    }
  }

  /**
   * Type visibly, then send. The first stretch is keystroke-by-keystroke because a
   * training video should show text being entered; the remainder is filled in one go
   * so a 400-character prompt does not eat forty seconds of runtime.
   */
  async #compose(page, code) {
    const VISIBLE = 140;
    const box = page.locator('#composer');
    await box.click({ timeout: 5000 }).catch(() => {});
    await box.fill('').catch(() => {});
    await page.keyboard.type(code.slice(0, VISIBLE), { delay: 16 }).catch(() => {});
    if (code.length > VISIBLE) {
      await box.fill(code).catch(() => {});
    }
    await page.waitForTimeout(350);
    await page.evaluate(() => window.fixture?.send?.()).catch(() => {});
  }

  async close() {
    // Cleared unconditionally: the recorder calls close() after every episode, and a
    // screen name surviving into the next one is what makes #goto skip the navigation
    // a fresh page needs.
    this.current = null;
    if (this.server) {
      await new Promise((resolve) => this.server.close(resolve));
      this.server = null;
      this.origin = null;
    }
  }
}

/** The app screen serves per-episode prompts and answers, so it needs to know which. */
function withEpisode(file, episode) {
  if (!file.startsWith('index.html') || !episode?.id) return file;
  return `index.html${file.includes('?') ? '&' : '?'}ep=${episode.id}`;
}

/** Compare a screen against a bare filename, ignoring any query string. */
function screenIs(current, file) {
  return typeof current === 'string' && current.split('?')[0] === file;
}

/**
 * Scroll the pane under the cursor, and report whether anything actually moved.
 *
 * `page.mouse.wheel` dispatches at the pointer's current position, which starts at
 * (0,0) — over the 46px header, not the scroller. Without a move() first the wheel
 * event lands on a non-scrolling element and every scroll beat silently does nothing.
 */
async function scrollViewport(page, delta) {
  const size = page.viewportSize() || { width: 1280, height: 720 };
  await page.mouse.move(Math.round(size.width / 2), Math.round(size.height / 2)).catch(() => {});
  const before = await page.evaluate(() => document.scrollingElement?.scrollTop ?? 0).catch(() => 0);
  await page.mouse.wheel(0, delta).catch(() => {});
  await page.waitForTimeout(220);
  const after = await page.evaluate(() => document.scrollingElement?.scrollTop ?? 0).catch(() => 0);
  return after !== before;
}

/** Shell, or a prompt for the builder? The two belong on entirely different screens. */
function isShell(code) {
  return /^\s*(git|cd|ls|npm|npx|node|pip|python3?|export|source|mkdir|cat|exec|py)\b/m.test(code);
}

/**
 * Which screen an episode opens on. Keyed on what the episode is about rather than its
 * number, so a renumbered or added script still lands somewhere sensible.
 */
function workflowDataset(episode) {
  const p = episode.workflowPath || '';
  if (/jira/i.test(p)) return 'jira-assistant';
  // Episode 09's subject is unified_search and attributions. Showing it the
  // document-creation file while the header claims it is it_helpdesk_tier0_agent.json
  // puts the narration and the picture in direct contradiction.
  if (/helpdesk|unified/i.test(p)) return 'it-helpdesk';
  return null;
}

function openingScreen(episode) {
  if (episode.workflowPath) {
    const dataset = workflowDataset(episode);
    return dataset ? `workflow.html?w=${dataset}` : 'workflow.html';
  }

  const all = episode.segments.flatMap((s) => s.beats).map((b) => b.text || '').join(' ').toLowerCase();
  const title = (episode.title || '').toLowerCase();

  // Reading logs is a different screen from talking to the builder.
  if (/\blogs?\b/.test(title) || /debugger|execution log|failed run|step level/.test(all)) return 'logs.html';

  // Episodes whose subject is the SHAPE of a workflow belong on the JSON, not on an
  // empty chat window. 03 walks initial_state and persist_keys, 04 needs a step
  // carrying depends_on and conditions together, 08 compares two native:chat roles —
  // all three are visible in the document-creation file and in none of the chat UI.
  // Reviewing your own draft: show a draft that actually has the flaw being reviewed.
  if (/review|first draft/.test(title) && /draft/.test(all)) return 'workflow.html?w=faq-draft';

  if (/state management|making state|depends_on|conditions|native:chat/.test(`${title} ${all}`.slice(0, 4000))
      && /initial_state|persist_keys|depends_on|conditions|native:chat/.test(all)) {
    return 'workflow.html';
  }

  const first = episode.segments[0]?.beats || [];
  const opensOnTerminal =
    first.some((b) => /terminal|clone|repo/i.test(b.text || '')) ||
    episode.segments.slice(0, 3).some((s) => s.beats.some((b) => b.code && isShell(b.code.join('\n'))));
  return opensOnTerminal ? 'terminal.html' : 'index.html';
}

/** Pull a step id or state key out of the beat so the viewer sees the line being named. */
function pickNeedle(text, code) {
  const fromCode = /([a-z_]{4,})/.exec(code || '');
  const known = [
    'attach_sources', 'write_document', 'outline_subagent', 'extract_facts', 'intake_sources',
    'deliver', 'have_facts', 'depends_on', 'conditions', 'initial_state', 'persist_keys',
    'native:chat', 'native:subagent', 'native:attributions', 'document_outline', 'cited_document',
  ];
  for (const k of known) if (text.includes(k)) return k;
  if (text.includes('subagent')) return 'native:subagent';
  if (text.includes('state flow') || text.includes('state key')) return '$state.';
  if (text.includes('architecture') || text.includes('pattern')) return 'steps';
  return fromCode ? fromCode[1] : null;
}
