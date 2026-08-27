/**
 * overlay.mjs — the in-page presentation layer.
 *
 * Everything the viewer sees that is not the app itself — chapter cards, narration captions,
 * six-word callouts — is drawn here, inside the page Chromium is recording, so it lands in
 * the video natively and no compositing step is needed afterwards.
 *
 * Two things drive the design:
 *
 * 1. It must be invisible to the host app and immune to it. The overlay lives in a shadow
 *    root on a host element bolted to <html> with inline !important geometry, so no app
 *    stylesheet can move it, restyle it, or be broken by it. Nothing accepts pointer events,
 *    so the target adapter can keep clicking through it.
 * 2. It must survive navigation. A beat can straddle a page load; that must degrade to a
 *    missing overlay frame, never to a thrown error that kills a ten-minute recording. So the
 *    injection is registered with addInitScript (re-runs on every document) and every call
 *    re-injects and retries once before quietly giving up.
 */

/**
 * Pages whose init script is already registered. Playwright has no removeInitScript, so
 * re-registering on every installOverlay() call would stack duplicate injections on each
 * navigation — harmless thanks to the window guard, but wasteful and confusing in traces.
 */
const REGISTERED = new WeakSet();

const DEFAULT_MS = { callout: 2600, caption: 3000, chapter: 3500 };

/**
 * Everything the overlay is, as one self-contained function evaluated in the page.
 *
 * Runs both as an init script (document-start, before app JS) and directly against the
 * current document. Idempotent: a second run sees window.__wtOverlay__ and returns.
 * Must not reference anything from module scope — Playwright serializes it as source.
 */
function overlayBootstrap() {
  const KEY = '__wtOverlay__';
  if (window[KEY]) return true;

  const FADE_MS = 180; // in/out for caption + callout
  const CHAPTER_FADE_MS = 320; // title cards move slower on purpose — this is training, not an ad
  const CLEAR_FADE_MS = 140; // hard-ish cut used by clearOverlay
  const LAYERS = ['caption', 'callout', 'chapter'];

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    .layer {
      position: absolute;
      opacity: 0;
      pointer-events: none;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto,
                   "Helvetica Neue", Arial, "Noto Sans", sans-serif;
      font-variant-ligatures: none;
      -webkit-font-smoothing: antialiased;
      transition: opacity ${FADE_MS}ms ease, transform ${FADE_MS}ms ease;
    }
    .layer.hidden { display: none; }
    /* The only thing that actually makes a layer visible. Every .on rule below animates
       transform; without this one they all animate into a fully transparent element. */
    .layer.on { opacity: 1; }
    .layer.fast-out { transition-duration: ${CLEAR_FADE_MS}ms; }

    /* ---- chapter: full-bleed title card, painted above everything else ---- */
    .chapter {
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      background: radial-gradient(120% 120% at 50% 38%, #171b22 0%, #0a0c10 72%);
      transition: opacity ${CHAPTER_FADE_MS}ms ease;
    }
    .chapter-inner {
      max-width: 74%;
      text-align: center;
      transform: translateY(8px);
      transition: transform ${CHAPTER_FADE_MS}ms cubic-bezier(0.22, 0.61, 0.36, 1);
    }
    .chapter.on .chapter-inner { transform: none; }
    .chapter-title {
      font-size: clamp(30px, 7.4vh, 78px);
      font-weight: 600;
      line-height: 1.1;
      letter-spacing: -0.015em;
      color: #f5f7fa;
    }
    .chapter-rule {
      width: 68px; height: 3px; border-radius: 2px;
      margin: 3.4vh auto;
      background: #5b93ff;
    }
    .chapter-sub {
      font-size: clamp(15px, 3vh, 30px);
      font-weight: 400;
      line-height: 1.4;
      color: #a6aebc;
    }
    .chapter-sub.empty, .chapter-rule.empty { display: none; }

    /* ---- caption: narration subtitle, bottom-centre, never past the bottom fifth ---- */
    .caption {
      left: 50%;
      bottom: 3.2vh;
      width: min(84vw, 1180px);
      max-height: 16.4vh;
      display: flex;
      justify-content: center;
      transform: translate(-50%, 10px);
    }
    .caption.on { transform: translate(-50%, 0); }
    .caption-text {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 3;              /* hard cap: three lines, then ellipsis */
      overflow: hidden;
      max-height: 16.4vh;
      padding: 1vh 1.8vw;
      border-radius: 10px;
      background: rgba(8, 10, 14, 0.86);
      box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.14), 0 6px 26px rgba(0, 0, 0, 0.4);
      color: #ffffff;
      font-size: clamp(18px, 3.4vh, 34px);
      font-weight: 450;
      line-height: 1.32;
      text-align: center;
      text-shadow: 0 1px 2px rgba(0, 0, 0, 0.55);
    }

    /* ---- callout: six words or fewer, bottom-right ---- */
    .callout { right: 3.4vw; bottom: 4.4vh; transform: translateY(10px); }
    .callout.on { transform: none; }
    .callout.raised { bottom: 22.5vh; } /* lifted clear of a visible caption */
    .callout-box {
      max-width: 46vw;
      padding: 1.2vh 1.7vw;
      border-left: 6px solid #ffc44d;
      border-radius: 8px;
      /* Near-black card with a light ring: legible over a dark terminal AND a light web UI. */
      background: #10131a;
      box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.9), 0 10px 30px rgba(0, 0, 0, 0.38);
      color: #ffffff;
      font-size: clamp(16px, 3vh, 30px);
      font-weight: 600;
      line-height: 1.25;
      letter-spacing: 0.005em;
      white-space: nowrap;
    }
    .callout-box.wrapped { white-space: normal; }
  `;

  let host = null;
  let els = null;
  const timers = { caption: 0, callout: 0, chapter: 0 };
  const hideTimers = { caption: 0, callout: 0, chapter: 0 };

  function build() {
    host = document.createElement('div');
    host.id = 'wt-overlay-host';
    host.setAttribute('dir', 'ltr');
    host.setAttribute('aria-hidden', 'true');
    // Inline + !important is the only geometry the host app cannot override; `all: initial`
    // first wipes anything it inherits, the rest of the block then wins over it.
    host.style.cssText = [
      'all: initial !important',
      'position: fixed !important',
      'top: 0 !important',
      'left: 0 !important',
      'right: 0 !important',
      'bottom: 0 !important',
      'width: 100% !important',
      'height: 100% !important',
      'margin: 0 !important',
      'padding: 0 !important',
      'border: 0 !important',
      'display: block !important',
      'visibility: visible !important',
      'opacity: 1 !important',
      'pointer-events: none !important',
      'direction: ltr !important',
      'transform: none !important',
      'filter: none !important',
      'clip-path: none !important',
      'z-index: 2147483647 !important',
    ].join('; ');

    // Closed mode: the app's own scripts cannot reach in through element.shadowRoot; we hold
    // the only reference. Playwright still screenshots and records it normally.
    const root = host.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    style.textContent = CSS;
    root.appendChild(style);

    const caption = document.createElement('div');
    caption.className = 'layer caption hidden';
    const captionText = document.createElement('div');
    captionText.className = 'caption-text';
    caption.appendChild(captionText);

    const callout = document.createElement('div');
    callout.className = 'layer callout hidden';
    const calloutBox = document.createElement('div');
    calloutBox.className = 'callout-box';
    callout.appendChild(calloutBox);

    const chapter = document.createElement('div');
    chapter.className = 'layer chapter hidden';
    const chapterInner = document.createElement('div');
    chapterInner.className = 'chapter-inner';
    const chapterTitle = document.createElement('div');
    chapterTitle.className = 'chapter-title';
    const chapterRule = document.createElement('div');
    chapterRule.className = 'chapter-rule';
    const chapterSub = document.createElement('div');
    chapterSub.className = 'chapter-sub';
    chapterInner.appendChild(chapterTitle);
    chapterInner.appendChild(chapterRule);
    chapterInner.appendChild(chapterSub);
    chapter.appendChild(chapterInner);

    // Chapter last so a title card paints over a lingering caption or callout.
    root.appendChild(caption);
    root.appendChild(callout);
    root.appendChild(chapter);

    els = { caption, callout, chapter, captionText, calloutBox, chapterTitle, chapterRule, chapterSub };
  }

  /** Attach (or re-attach) the host. Returns false only when there is no document yet. */
  function mount() {
    const docEl = document.documentElement;
    if (!docEl) return false;
    if (!host) build();
    // An SPA that rewrites <html> can drop us; cheap to notice and put back.
    if (host.parentNode !== docEl) docEl.appendChild(host);
    return true;
  }

  function clearTimers(layer) {
    if (timers[layer]) { clearTimeout(timers[layer]); timers[layer] = 0; }
    if (hideTimers[layer]) { clearTimeout(hideTimers[layer]); hideTimers[layer] = 0; }
  }

  /** The callout sits low; lift it when a caption is occupying the bottom band. */
  function syncCalloutOffset() {
    if (!els) return;
    els.callout.classList.toggle('raised', !els.caption.classList.contains('hidden'));
  }

  function hide(layer, fadeMs) {
    if (!els) return;
    const el = els[layer];
    clearTimers(layer);
    el.classList.remove('on');
    hideTimers[layer] = setTimeout(() => {
      el.classList.add('hidden');
      el.classList.remove('fast-out');
      hideTimers[layer] = 0;
      syncCalloutOffset();
    }, fadeMs);
  }

  /**
   * Show a layer for `ms` of total screen time. Returns immediately — the recorder owns
   * pacing and sleeps for the beat itself; blocking here would double-count the duration.
   */
  function show(layer, ms, fadeMs) {
    if (!mount()) return false;
    const el = els[layer];
    clearTimers(layer);
    el.classList.remove('fast-out');
    el.classList.remove('hidden');
    void el.offsetWidth; // flush the display change so the opacity transition actually runs
    el.classList.add('on');
    syncCalloutOffset();

    const total = typeof ms === 'number' && isFinite(ms) && ms > 0 ? ms : 2500;
    // Start the fade early so the layer is gone at ~ms, not ms + fade.
    timers[layer] = setTimeout(() => hide(layer, fadeMs), Math.max(60, total - fadeMs));
    return true;
  }

  const api = {
    /** Re-attach after a DOM swap without disturbing what is showing. */
    ensure() { return mount(); },

    callout(arg) {
      if (!mount()) return false;
      const text = String((arg && arg.text) || '');
      els.calloutBox.textContent = text;
      // Six words is the house rule, but nothing enforces it upstream; wrap rather than
      // let a long one run off the right edge.
      els.calloutBox.classList.toggle('wrapped', text.length > 34);
      return show('callout', arg && arg.ms, FADE_MS);
    },

    caption(arg) {
      if (!mount()) return false;
      els.captionText.textContent = String((arg && arg.text) || '');
      return show('caption', arg && arg.ms, FADE_MS);
    },

    chapter(arg) {
      if (!mount()) return false;
      const title = String((arg && arg.title) || '');
      const subtitle = String((arg && arg.subtitle) || '');
      els.chapterTitle.textContent = title;
      els.chapterSub.textContent = subtitle;
      els.chapterSub.classList.toggle('empty', subtitle === '');
      els.chapterRule.classList.toggle('empty', subtitle === '');
      return show('chapter', arg && arg.ms, CHAPTER_FADE_MS);
    },

    /**
     * Take everything down and resolve once the frame is actually clean, so a caller can
     * screenshot or cut immediately after awaiting.
     */
    clear() {
      if (!els) return Promise.resolve(true);
      let showing = false;
      for (const layer of LAYERS) {
        clearTimers(layer);
        const el = els[layer];
        if (!el.classList.contains('hidden')) {
          showing = true;
          el.classList.add('fast-out');
          el.classList.remove('on');
        }
      }
      if (!showing) return Promise.resolve(true);
      return new Promise((resolve) => {
        setTimeout(() => {
          for (const layer of LAYERS) {
            els[layer].classList.add('hidden');
            els[layer].classList.remove('fast-out');
          }
          syncCalloutOffset();
          resolve(true);
        }, CLEAR_FADE_MS);
      });
    },
  };

  window[KEY] = api;

  // As an init script this can run before <html> exists; mount when the parser catches up.
  if (!mount()) {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  }
  return true;
}

/**
 * Call one overlay method inside the page. Defined at module scope (not inline) so Playwright
 * serializes the same source every time.
 */
async function invokeOverlay(arg) {
  const api = window.__wtOverlay__;
  if (!api || typeof api[arg.method] !== 'function') return 'missing';
  const result = await api[arg.method](arg.payload);
  return result === false ? 'missing' : 'ok';
}

/**
 * page.evaluate that never throws. A navigation mid-beat destroys the execution context;
 * that is expected, not exceptional, so re-inject into the new document and try once more.
 */
async function evaluateSafely(page, fn, arg) {
  if (!page || (typeof page.isClosed === 'function' && page.isClosed())) return null;
  try {
    return await page.evaluate(fn, arg);
  } catch {
    if (typeof page.isClosed === 'function' && page.isClosed()) return null;
    try {
      await page.waitForLoadState('domcontentloaded', { timeout: 2000 }).catch(() => {});
      await page.evaluate(overlayBootstrap);
      return await page.evaluate(fn, arg);
    } catch {
      return null; // a beat without its overlay beats a dead recording
    }
  }
}

async function callOverlay(page, method, payload) {
  let result = await evaluateSafely(page, invokeOverlay, { method, payload });
  if (result !== 'ok') {
    // 'missing' means this document has no overlay yet (fresh navigation, or the init script
    // ran before <html> existed and nothing has mounted it since).
    await installOverlay(page);
    result = await evaluateSafely(page, invokeOverlay, { method, payload });
  }
  return result === 'ok';
}

/** Narration arrives from markdown with hard line wraps; collapse them for display. */
function tidy(text) {
  return String(text ?? '').replace(/\s+/g, ' ').trim();
}

function duration(ms, fallback) {
  const n = Number(ms);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

/**
 * Install the overlay into a page. Idempotent, and safe (expected, even) after every
 * navigation. Registers the injection for future documents AND mounts it into the current one.
 *
 * @param {import('playwright').Page} page
 * @returns {Promise<boolean>} true if the overlay is live in the current document.
 */
export async function installOverlay(page) {
  if (!page || (typeof page.isClosed === 'function' && page.isClosed())) return false;
  if (!REGISTERED.has(page)) {
    try {
      await page.addInitScript(overlayBootstrap);
      REGISTERED.add(page);
    } catch {
      // Page closing, or context already gone — the direct injection below still gets a shot.
    }
  }
  return (await evaluateSafely(page, overlayBootstrap, undefined)) === true;
}

/**
 * The six-words-or-fewer overlay from the scripts. Bottom-right, auto-clears after `ms`.
 * @param {import('playwright').Page} page
 * @param {string} text
 * @param {number} [ms=2600] Total time on screen, including fades.
 */
export async function showCallout(page, text, ms) {
  return callOverlay(page, 'callout', { text: tidy(text), ms: duration(ms, DEFAULT_MS.callout) });
}

/**
 * Narration subtitle: bottom-centre, at most three lines, never taller than the bottom fifth.
 * @param {import('playwright').Page} page
 * @param {string} text
 * @param {number} [ms=3000] Total time on screen, including fades.
 */
export async function showCaption(page, text, ms) {
  return callOverlay(page, 'caption', { text: tidy(text), ms: duration(ms, DEFAULT_MS.caption) });
}

/**
 * Full-bleed title card — top of an episode, and each segment heading.
 * @param {import('playwright').Page} page
 * @param {string} title
 * @param {string} [subtitle] Omit for a bare title; the rule under it hides with it.
 * @param {number} [ms=3500] Total time on screen, including fades.
 */
export async function showChapter(page, title, subtitle, ms) {
  return callOverlay(page, 'chapter', {
    title: tidy(title),
    subtitle: tidy(subtitle),
    ms: duration(ms, DEFAULT_MS.chapter),
  });
}

/**
 * Take down everything currently showing. Resolves once the frame is clean, so the caller can
 * screenshot or cut straight after awaiting it.
 * @param {import('playwright').Page} page
 */
export async function clearOverlay(page) {
  return callOverlay(page, 'clear', {});
}
