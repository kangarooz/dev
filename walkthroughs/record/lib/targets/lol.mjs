/**
 * The live Legion platform (LoL) target.
 *
 * This is the one that produces the real training videos. It is written to run on a
 * machine that is on the VPN — from a container behind an egress policy it will fail
 * preflight, and it says so in the three terms that are actually ever wrong rather
 * than surfacing a DNS error.
 */

import { Target } from './base.mjs';
import { probe, egressNote } from './probe.mjs';

const DEFAULT_BASE = 'https://lol.legionintel.com';
const PREFLIGHT_TIMEOUT_MS = 8000;

/**
 * Selector chains, most specific first. The real DOM is not available from here, so
 * these are deliberately role-first and forgiving: a miss returns false and the beat
 * degrades to a dwell rather than aborting a ten-minute take.
 */
const COMPOSER = [
  'textarea[placeholder*="Ask" i]',
  'textarea[placeholder*="message" i]',
  '[contenteditable="true"][role="textbox"]',
  '[role="textbox"]',
  'textarea',
];
const SEND = [
  'button:has-text("Send")',
  'button[type="submit"]',
  'button[aria-label*="send" i]',
];
const BUILDER_SELECT = ['select', '[role="combobox"]', 'button:has-text("Select")'];
const ACTIVATE = ['button:has-text("Activate")', 'button:has-text("Activate Builder")'];

export default class LolTarget extends Target {
  static id = 'lol';

  constructor(opts = {}) {
    super(opts);
    this.baseUrl = (opts.baseUrl || DEFAULT_BASE).replace(/\/+$/, '');
  }

  async preflight() {
    const result = await probe(this.baseUrl, { timeoutMs: PREFLIGHT_TIMEOUT_MS });
    if (result.ok) return;

    if (result.kind === 'egress') {
      throw new Error(
        `${this.baseUrl} is blocked by this environment's network policy.\n\n` +
          `The gateway answered: ${result.reason}\n\n${egressNote()}`,
      );
    }
    if (result.kind === 'auth') {
      throw new Error(
        `${this.baseUrl} is reachable but not authenticated (${result.reason}).\n\n` +
          'Run `npm run auth -- --target lol` once to save a session, then record again.',
      );
    }
    throw new Error(
      `cannot reach ${this.baseUrl} (${result.reason}).\n\n` +
        'In order of likelihood:\n' +
        '  1. VPN is not connected — this host is internal-only.\n' +
        '  2. Wrong base URL — pass --base-url if you are recording against a dev deployment.\n' +
        '  3. No saved login — run `npm run auth -- --target lol` once, then re-run.\n\n' +
        egressNote(),
    );
  }

  async open(page, episode) {
    const url = episode.workflowPath ? `${this.baseUrl}/workflows` : this.baseUrl;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    // networkidle can never settle on an app that polls; bound it and move on.
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});

    if (/sign[_-]?in|login|auth/i.test(page.url())) {
      throw new Error(
        `${this.baseUrl} redirected to a login page.\n` +
          'Run `npm run auth -- --target lol` to save a session, then record again.',
      );
    }
  }

  async action(page, beat, ctx) {
    const text = (beat.text || '').toLowerCase();
    const code = (beat.code || []).join('\n').trim();
    const has = (...words) => words.some((w) => text.includes(w));

    if (has('workflow_builder', 'dropdown')) {
      const el = await first(page, BUILDER_SELECT);
      if (!el) return false;
      // A native <select> takes selectOption; a custom combobox needs a click.
      const tag = await el.evaluate((n) => n.tagName.toLowerCase()).catch(() => '');
      if (tag === 'select') await el.selectOption({ label: 'workflow_builder' }).catch(() => {});
      else await el.click().catch(() => {});
      return true;
    }

    if (has('activate')) {
      const el = await first(page, ACTIVATE);
      if (!el) return false;
      await el.click().catch(() => {});
      return true;
    }

    if (code && has('paste', 'type', 'send', 'prompt', 'follow up', 'follow-up')) {
      const box = await first(page, COMPOSER);
      if (!box) return false;
      await box.click().catch(() => {});
      await box.fill('').catch(() => {});
      // Type the opening stretch at human cadence so the video shows text arriving,
      // then fill the rest — a 400-character prompt should not cost 40s of runtime.
      await page.keyboard.type(code.slice(0, 140), { delay: 18 }).catch(() => {});
      if (code.length > 140) await box.fill(code).catch(() => {});
      await page.waitForTimeout(300);
      const send = await first(page, SEND);
      if (send) await send.click().catch(() => {});
      else await page.keyboard.press('Meta+Enter').catch(() => {});
      return true;
    }

    if (has('scroll')) {
      await page.mouse.wheel(0, 340);
      return true;
    }

    return false;
  }
}

/** First selector in the chain that resolves to a visible element, or null. */
async function first(page, selectors) {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if (await locator.isVisible().catch(() => false)) return locator;
  }
  return null;
}
