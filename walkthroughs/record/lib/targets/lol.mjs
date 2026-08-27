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

/**
 * The Socrates prompts, keyed by episode. The scripts tell the presenter to paste
 * "prompt 5"; the text itself lives in the Confluence guide, so the target carries it.
 * Abridged to the opening paragraph — enough to be recognisable on camera.
 */
const PROMPTS = {
  '01': 'Teach me the most useful onboarding path for a brand-new Solution Architect learning Legion workflows.',
  '02': 'Teach me the simple FAQ workflow as if I am brand new to Legion. I want a real teaching walkthrough, not a short summary.',
  '03': 'Explain state management in Legion workflows for a new Solution Architect in a way that makes it feel concrete and usable.',
  '04': 'Teach me the difference between depends_on and conditions in Legion workflows like I am new but technical.',
  '05': 'Analyze Dev/templated-writing/document-creation/workflow.json for a brand-new Solution Architect.',
  '06': 'Compare native:lexical_search and native:semantic_search in a way that helps me actually choose between them.',
  '07': 'Teach me how to read workflow execution logs like a new Solution Architect who wants to debug with evidence.',
  '08': 'Show me two workflows from this repo that use native:chat in meaningfully different ways.',
  '09': 'Analyze Modules/IT HelpDesk/it_helpdesk_tier0_agent.json as a current RAG workflow that uses unified_search.',
  '10': 'Compare the hybrid search pattern, native:full_text_retrieval, and native:unified_search.',
  '11': 'Analyze On-Prem Jira Assistant with the mindset of learning from a complex workflow without copying it blindly.',
  '12': 'Review a first-draft FAQ workflow that uses native:semantic_search, native:chat, and native:send-message.',
};
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

    // No code fence required. The scripts say "Fresh session, bootstrap, paste prompt 3"
    // with the prompt text living in the Confluence guide, not in the script — gating on
    // `code` meant the most important action in every episode never fired on the live
    // app either. PROMPTS supplies the text when the beat does not.
    if (has('paste', 'type', 'send', 'prompt', 'follow up', 'follow-up')) {
      const text = code || PROMPTS[ctx?.episode?.id] || '';
      if (!text) return false;
      const box = await first(page, COMPOSER);
      if (!box) return false;
      await box.click().catch(() => {});
      await box.fill('').catch(() => {});
      // Type the opening stretch at human cadence so the video shows text arriving,
      // then fill the rest — a 400-character prompt should not cost 40s of runtime.
      await page.keyboard.type(text.slice(0, 140), { delay: 18 }).catch(() => {});
      if (text.length > 140) await box.fill(text).catch(() => {});
      await page.waitForTimeout(300);
      const send = await first(page, SEND);
      if (send) await send.click().catch(() => {});
      else await page.keyboard.press('Meta+Enter').catch(() => {});
      return true;
    }

    if (has('scroll')) {
      // A wheel event with the pointer still at (0,0) lands on the header and scrolls
      // nothing; move to the middle of the viewport first.
      const size = page.viewportSize() || { width: 1280, height: 720 };
      await page.mouse.move(Math.round(size.width / 2), Math.round(size.height / 2)).catch(() => {});
      const before = await page.evaluate(() => document.scrollingElement?.scrollTop ?? 0).catch(() => 0);
      await page.mouse.wheel(0, 340).catch(() => {});
      await page.waitForTimeout(220);
      const after = await page.evaluate(() => document.scrollingElement?.scrollTop ?? 0).catch(() => 0);
      return after !== before;
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
