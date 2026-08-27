/**
 * Workflow JSON on screen, for the episodes that read a real file (05, 09, 11).
 *
 * Two modes. With --repo-dir it renders the file straight off a local clone, which is
 * the mode that will actually get used: the operator already has the repo, and a
 * locally rendered page is more legible at 720p than GitLab's own blob view. Without
 * it, it drives gitlab.com — which needs a saved login, since the project is private.
 */

import { readFile, access } from 'node:fs/promises';
import { join } from 'node:path';
import { Target } from './base.mjs';
import { probe } from './probe.mjs';

const DEFAULT_BASE = 'https://gitlab.com';
const PROJECT = 'yurtsai/enablement-workflow-configs';
const PREFLIGHT_TIMEOUT_MS = 8000;

export default class GitlabTarget extends Target {
  static id = 'gitlab';

  constructor(opts = {}) {
    super(opts);
    this.baseUrl = (opts.baseUrl || DEFAULT_BASE).replace(/\/+$/, '');
    this.repoDir = opts.repoDir || null;
  }

  async preflight() {
    if (this.repoDir) {
      try {
        await access(this.repoDir);
      } catch {
        throw new Error(`--repo-dir ${this.repoDir} does not exist — point it at your enablement-workflow-configs clone`);
      }
      return;
    }

    const result = await probe(`${this.baseUrl}/${PROJECT}`, { timeoutMs: PREFLIGHT_TIMEOUT_MS });
    if (result.ok) return;

    throw new Error(
      `cannot read ${PROJECT} on ${this.baseUrl} (${result.reason}).\n\n` +
        'It is a private project, so one of these is needed:\n' +
        '  --repo-dir /path/to/enablement-workflow-configs   (recommended — renders from your clone)\n' +
        '  npm run auth -- --target gitlab                   (saves a browser session)\n',
    );
  }

  async open(page, episode) {
    const path = episode.workflowPath;
    if (!path) {
      await page.goto(`${this.baseUrl}/${PROJECT}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      return;
    }

    if (this.repoDir) {
      await this.#renderLocal(page, path);
      return;
    }
    await page.goto(`${this.baseUrl}/${PROJECT}/-/blob/main/${encodeURI(path)}`, {
      waitUntil: 'domcontentloaded',
      timeout: 30000,
    });
  }

  /**
   * Render the file into our own viewer rather than GitLab's. Same page the fixture
   * uses, so episodes look consistent whichever mode produced them.
   */
  async #renderLocal(page, path) {
    let source;
    try {
      source = await readFile(join(this.repoDir, path), 'utf8');
    } catch {
      throw new Error(`${path} not found under ${this.repoDir} — is the clone up to date?`);
    }
    await page.setContent(viewerHtml(path, source), { waitUntil: 'domcontentloaded' });
  }

  async action(page, beat) {
    const text = (beat.text || '').toLowerCase();
    const needle = /`([^`]+)`/.exec(beat.text || '')?.[1] || pickNeedle(text);
    if (needle) {
      const hit = await page
        .evaluate((n) => window.__viewer__?.highlight?.(n) ?? 0, needle)
        .catch(() => 0);
      if (hit) return true;
    }
    if (text.includes('scroll')) {
      // Pointer starts at (0,0), over the header — move it onto the code pane first.
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

function pickNeedle(text) {
  for (const key of ['depends_on', 'conditions', 'initial_state', 'persist_keys', 'native:chat', 'subagent', 'outputs']) {
    if (text.includes(key)) return key;
  }
  return null;
}

/** Standalone viewer: large type, line numbers, dark ground — legible in a 720p frame. */
function viewerHtml(path, source) {
  const escaped = source
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  const rows = escaped
    .split('\n')
    .map((line, i) => `<tr id="L${i + 1}"><td class="ln">${i + 1}</td><td class="src">${colour(line)}</td></tr>`)
    .join('');
  return `<!doctype html><html><head><meta charset="utf-8"><title>${path}</title><style>
    :root{--bg:#0f1319;--panel:#161b23;--line:#2a323f;--ink:#e6ebf2;--dim:#93a1b5;--faint:#64748b;
          --accent:#4f9cf9;--key:#79c0ff;--str:#a5d6ff;--num:#d2a8ff;--bool:#ff7b72;--punc:#7d8590;}
    *{box-sizing:border-box}html,body{height:100%;margin:0}
    body{background:var(--bg);color:var(--ink);font-family:ui-sans-serif,system-ui,sans-serif;display:flex;flex-direction:column}
    header{height:46px;flex:none;display:flex;align-items:center;gap:12px;padding:0 16px;background:var(--panel);border-bottom:1px solid var(--line)}
    .b{font-weight:650}.b span{color:var(--accent)}
    .p{font-family:ui-monospace,Menlo,monospace;font-size:13px;color:var(--dim)}
    .code{flex:1;overflow:auto;padding:16px 0 40px}
    table{border-collapse:collapse;width:100%;font-family:ui-monospace,Menlo,monospace;font-size:14px;line-height:1.62}
    td.ln{width:56px;text-align:right;padding:0 14px 0 0;color:var(--faint);user-select:none;vertical-align:top}
    td.src{padding:0 20px 0 0;white-space:pre;vertical-align:top}
    tr.hl{background:rgba(79,156,249,.13)}tr.hl td.ln{color:var(--accent);font-weight:600;box-shadow:inset 2px 0 0 var(--accent)}
    .k{color:var(--key)}.s{color:var(--str)}.n{color:var(--num)}.bo{color:var(--bool)}.pu{color:var(--punc)}
  </style></head><body>
    <header><div class="b">Legion <span>·</span> workflow-configs</div><div class="p">${path}</div></header>
    <div class="code"><table>${rows}</table></div>
    <script>
      window.__viewer__ = {
        highlight(needle) {
          const rows = [...document.querySelectorAll('tr')];
          rows.forEach(r => r.classList.remove('hl'));
          const hits = rows.filter(r => r.textContent.includes(needle));
          hits.forEach(r => r.classList.add('hl'));
          if (hits.length) hits[0].scrollIntoView({ block: 'center', behavior: 'smooth' });
          return hits.length;
        }
      };
    <\/script>
  </body></html>`;
}

/** Minimal JSON colouring — no dependency, and it only has to survive a video frame. */
function colour(line) {
  let out = '';
  let i = 0;
  while (i < line.length) {
    const rest = line.slice(i);
    let m;
    if ((m = /^"(?:[^"\\]|\\.)*"(\s*:)?/.exec(rest))) {
      const isKey = !!m[1];
      out += `<span class="${isKey ? 'k' : 's'}">${m[0].replace(/\s*:$/, '')}</span>${isKey ? '<span class="pu">:</span>' : ''}`;
      i += m[0].length;
      continue;
    }
    if ((m = /^-?\d+(?:\.\d+)?/.exec(rest))) { out += `<span class="n">${m[0]}</span>`; i += m[0].length; continue; }
    if ((m = /^(true|false|null)\b/.exec(rest))) { out += `<span class="bo">${m[0]}</span>`; i += m[0].length; continue; }
    if (/^[{}\[\],]/.test(rest)) { out += `<span class="pu">${rest[0]}</span>`; i += 1; continue; }
    out += rest[0];
    i += 1;
  }
  return out;
}
