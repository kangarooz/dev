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
    for (const file of ['index.html', 'workflow.html']) {
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
    const origin = await this.start();
    // Episode 00 opens on '[SCREEN] Empty terminal' — every other episode opens in the app.
    const first = episode.workflowPath ? 'workflow.html' : opensInTerminal(episode) ? 'terminal.html' : 'index.html';
    await this.#goto(page, first);
    if (episode.workflowPath) {
      await page.evaluate((p) => window.fixture?.setPath?.(p), episode.workflowPath).catch(() => {});
    }
  }

  async #goto(page, file) {
    if (this.current === file) return;
    await page.goto(`${this.origin}/${file}`, { waitUntil: 'domcontentloaded', timeout: 15000 });
    this.current = file;
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

    // Pointing at a step id or state key in the JSON viewer.
    if (ctx.episode.workflowPath) {
      const needle = pickNeedle(text, code);
      if (needle) {
        const hits = await page.evaluate((n) => window.fixture?.highlight?.(n) ?? 0, needle).catch(() => 0);
        if (hits) return true;
      }
      if (has('scroll')) {
        await page.mouse.wheel(0, 320);
        return true;
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
    if (this.current === 'terminal.html' && code && !isShell(code)) {
      await this.#goto(page, 'index.html');
    }

    if (has('dropdown', 'select `workflow_builder`', 'workflow_builder')) {
      await page.evaluate(() => window.fixture?.selectBuilder?.('workflow_builder')).catch(() => {});
      return true;
    }
    if (has('activate')) {
      await page.evaluate(() => window.fixture?.activate?.()).catch(() => {});
      return true;
    }
    if (code && has('paste', 'type', 'send', 'prompt', 'follow up', 'follow-up')) {
      await this.#compose(page, code);
      return true;
    }
    if (has('send')) {
      await page.evaluate(() => window.fixture?.send?.()).catch(() => {});
      return true;
    }
    if (has('scroll')) {
      await page.evaluate(() => {
        const t = document.getElementById('transcript');
        if (t) t.scrollTop = Math.min(t.scrollTop + 260, t.scrollHeight);
      }).catch(() => {});
      return true;
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
    if (this.server) {
      await new Promise((resolve) => this.server.close(resolve));
      this.server = null;
      this.origin = null;
    }
  }
}

/** Shell, or a prompt for the builder? The two belong on entirely different screens. */
function isShell(code) {
  return /^\s*(git|cd|ls|npm|npx|node|pip|python3?|export|source|mkdir|cat|exec|py)\b/m.test(code);
}

/** Episodes whose opening beats are terminal work rather than app work. */
function opensInTerminal(episode) {
  const first = episode.segments[0]?.beats || [];
  return first.some((b) => /terminal|clone|repo/i.test(b.text || '')) ||
    episode.segments.slice(0, 3).some((s) => s.beats.some((b) => b.code && isShell(b.code.join('\n'))));
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
