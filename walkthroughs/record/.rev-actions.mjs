import { chromium } from 'playwright';
import { loadEpisodes } from '/home/user/dev/walkthroughs/record/lib/parse.mjs';
import { planEpisode } from '/home/user/dev/walkthroughs/record/lib/pace.mjs';
import FixtureTarget from '/home/user/dev/walkthroughs/record/lib/targets/fixture.mjs';

const eps = await loadEpisodes('/home/user/dev/walkthroughs');
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', headless: true });

for (const ep of eps) {
  const target = new FixtureTarget({ log: () => {} });
  const ctxb = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await ctxb.newPage();
  await target.open(page, ep);
  const opened = page.url().replace(/^http:\/\/127\.0\.0\.1:\d+\//, '');
  console.log(`\n=== ${ep.id} ${ep.title}   OPENS ON: ${opened || '(about:blank)'}  wfPath=${ep.workflowPath}`);
  const plan = planEpisode(ep, { fast: false });
  for (const beat of plan.beats) {
    if (beat.kind !== 'action') continue;
    const before = page.url().replace(/^http:\/\/127\.0\.0\.1:\d+\//, '') || 'about:blank';
    const t0 = Date.now();
    let handled = false, err = null;
    try { handled = await target.action(page, beat, { episode: ep, index: 0 }); }
    catch (e) { err = e.message; }
    const took = ((Date.now() - t0) / 1000).toFixed(1);
    const after = page.url().replace(/^http:\/\/127\.0\.0\.1:\d+\//, '') || 'about:blank';
    // what is visible
    const state = await page.evaluate(() => {
      const o = {};
      const tr = document.getElementById('transcript');
      if (tr) { o.msgs = tr.querySelectorAll('.msg').length; o.lastRole = tr.querySelector('.msg:last-child .role')?.textContent; o.lastText = (tr.querySelector('.msg:last-child .bubble')?.textContent||'').slice(0,70); o.empty = !!document.getElementById('empty')?.parentNode; }
      const wf = document.getElementById('wfBody');
      if (wf) o.steps = wf.querySelectorAll('.step').length;
      const st = document.getElementById('statusText'); if (st) o.status = st.textContent;
      const hl = document.querySelectorAll('tr.hl'); if (document.getElementById('tbl')) o.hlRows = hl.length;
      const sc = document.getElementById('screen'); if (sc) o.termLines = sc.children.length;
      return o;
    }).catch(() => ({}));
    console.log(`  [ACTION dur=${beat.durSec}s wall=${took}s] handled=${handled}${err?' ERR='+err:''} ${before}${before!==after?' -> '+after:''}\n      text: ${JSON.stringify((beat.text||'').slice(0,95))}${beat.code?'\n      code: '+JSON.stringify(beat.code[0].slice(0,60)):''}\n      state: ${JSON.stringify(state)}`);
  }
  await ctxb.close();
  await target.close();
}
await browser.close();
