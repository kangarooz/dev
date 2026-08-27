import { chromium } from 'playwright';
import { loadEpisodes } from './lib/parse.mjs';
import { planEpisode } from './lib/pace.mjs';
import FixtureTarget from './lib/targets/fixture.mjs';

const eps = await loadEpisodes('/home/user/dev/walkthroughs');
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', headless: true });
const snap = (page) => page.evaluate(() => {
  const scrolls = [...document.querySelectorAll('*')].map(e => e.scrollTop).join(',');
  return document.documentElement.outerHTML.length + '|' + document.documentElement.outerHTML.replace(/\s+/g,'').slice(0,200000).split('').reduce((h,c)=>((h<<5)-h+c.charCodeAt(0))|0,0) + '|' + scrolls + '|' + window.scrollY;
});
const tally = { acted: 0, actedWrong: 0, silentNoop: 0, dwell: 0 };
console.log('ep  #  verdict        handled  screen         beat text');
for (const ep of eps) {
  const t = new FixtureTarget({ log(){} });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await ctx.newPage();
  await t.open(page, ep);
  let n = 0;
  for (const b of planEpisode(ep, {}).beats) {
    if (b.kind !== 'action') continue;
    n++;
    const before = await snap(page);
    const handled = await t.action(page, b, { episode: ep, index: 0 });
    await page.waitForTimeout(250);
    const after = await snap(page);
    const changed = before !== after;
    const err = await page.evaluate(() => (document.querySelector('.msg.agent .bubble')?.textContent||'').includes('builder_agent_id not set'));
    let verdict;
    if (!handled) { verdict = 'DWELL(caption)'; tally.dwell++; }
    else if (!changed) { verdict = 'SILENT NO-OP'; tally.silentNoop++; }
    else if (err) { verdict = 'ACTED->ERROR'; tally.actedWrong++; }
    else { verdict = 'ACTED'; tally.acted++; }
    console.log(`${ep.id}  ${n}  ${verdict.padEnd(14)} ${String(handled).padEnd(7)}  ${String(t.current).slice(0,13).padEnd(13)}  ${JSON.stringify((b.text||'').slice(0,58))}${b.code?' +code':''}  [${b.durSec}s]`);
  }
  await ctx.close(); await t.close();
}
console.log('\nTOTALS', JSON.stringify(tally), 'sum=', Object.values(tally).reduce((a,b)=>a+b,0));
await browser.close();
