import { loadEpisodes } from './lib/parse.mjs';
import { recordEpisode } from './lib/record.mjs';
import FixtureTarget from './lib/targets/fixture.mjs';
const [ep] = await loadEpisodes('/home/user/dev/walkthroughs', ['12']);
const t = new FixtureTarget({ log(){} });
const t0 = Date.now();
const rows = [];
await recordEpisode(ep, { target: t, outDir: '/tmp/claude-0/-home-user-dev/dd4fc26d-e48b-59a0-ba33-93a85b95e273/scratchpad/off', fast: false,
  onBeat: (b) => rows.push({ i: b.index, kind: b.kind, seg: b.segmentHeading, plan: b.startSec, real: (Date.now()-t0)/1000 }) });
console.log(' beat  kind    VTT says   picture at   caption is EARLY by   segment');
for (const r of rows) console.log(`  ${String(r.i).padStart(3)}  ${r.kind.padEnd(7)} ${r.plan.toFixed(1).padStart(7)}s ${r.real.toFixed(1).padStart(11)}s ${(r.real-r.plan).toFixed(1).padStart(16)}s   ${r.seg}`);
