// Checks that the in-browser scorer (web/score.js) reproduces the pipeline ranking (pipeline/rank.py)
// for every window at the pre-registered weights. Run: node tests/score_parity.test.js
const fs = require('fs');
const path = require('path');
const S = require('../web/score.js');

const root = path.join(__dirname, '..');
const card = JSON.parse(fs.readFileSync(path.join(root, 'data/scorecard.json'), 'utf8'));
const cfg = JSON.parse(fs.readFileSync(path.join(root, 'pipeline/config.json'), 'utf8'));
let fail = 0, n = 0;

// defaults in the browser must equal the committed config
const map = { coherenceFloor: cfg.score.coherence_floor_for_visibility, opposingBonus: cfg.score.opposing_bonus, iceBonus: cfg.score.ice_bonus, wPop: cfg.score.w_pop, wSeason: cfg.score.w_season, nSensors: cfg.pass_condition.top_n };
for (const [k, v] of Object.entries(map)) {
  if (S.DEFAULTS[k] !== v) { console.error(`default ${k}=${S.DEFAULTS[k]} but config says ${v}`); fail++; }
}

for (const [win, w] of Object.entries(card.windows)) {
  const fc = JSON.parse(fs.readFileSync(path.join(root, `data/blobs_${win}.geojson`), 'utf8'));
  const r = S.rank(fc.features, S.DEFAULTS, cfg.truth.hit_radius_km);
  n++;
  if (r.srcRank !== w.source_rank || r.n !== w.n_ranked) {
    console.error(`${win}: browser rank ${r.srcRank}/${r.n} vs pipeline ${w.source_rank}/${w.n_ranked}`);
    fail++;
  }
  // every feature's score must match the pipeline's stored score
  for (const f of fc.features) {
    const s = S.score(f.properties, S.DEFAULTS);
    if (Math.abs(s - f.properties.score) > 1e-3 * Math.max(1, Math.abs(f.properties.score))) {
      console.error(`${win} ${f.properties.id}: browser score ${s} vs pipeline ${f.properties.score}`);
      fail++;
      break;
    }
  }
}
console.log(fail ? `FAIL (${fail} problems over ${n} windows)` : `ok: browser ranking matches pipeline for ${n} windows`);
process.exit(fail ? 1 : 0);
