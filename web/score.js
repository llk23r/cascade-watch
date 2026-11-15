/* Cascade Watch scoring — the same formula as pipeline/rank.py, so sliders re-rank instantly.
   score = (motion/sigma) x dir_coherence x (opposing bonus) x (ice bonus)
           x (1 + w_pop x log10(1 + people_below)) x (1 + w_season x (season_anomaly - 1))
   Blobs under the coherence floor (or under the optional extra motion filter) score zero. */
(function (root) {
  'use strict';
  var DEFAULTS = { coherenceFloor: 0.30, motionSigma: 0, opposingBonus: 1.5, iceBonus: 1.3, wPop: 0.25, wSeason: 0.5, nSensors: 5 };

  function score(p, w) {
    if (p.coherence < w.coherenceFloor) return 0;
    var snr = p.motion_m / p.sigma_m;
    if (!(snr > 0) || snr < w.motionSigma) return 0;
    var s = snr * p.dir_coherence;
    if (p.opposing_sign) s *= w.opposingBonus;
    if (p.ice_over_rock) s *= w.iceBonus;
    s *= 1 + w.wPop * Math.log10(1 + (p.people_below || 0));
    s *= 1 + w.wSeason * (p.season_anomaly - 1);
    return s;
  }

  /* Rank one window's blobs. A "hit" is the best-ranked blob within hitKm of the true
     source that drains into the same channel. Truth is never used to rank. */
  function rank(features, w, hitKm) {
    var rows = features.map(function (f) { var p = f.properties; return { f: f, p: p, s: score(p, w), rank: null }; });
    var ranked = rows.filter(function (r) { return r.s > 0; }).sort(function (a, b) { return b.s - a.s; });
    ranked.forEach(function (r, i) { r.rank = i + 1; });
    var hit = null;
    for (var i = 0; i < ranked.length; i++) {
      var p = ranked[i].p;
      if (p.dist_truth_km <= hitKm && p.same_channel) { hit = ranked[i]; break; }
    }
    var near = rows.filter(function (r) { return r.p.dist_truth_km <= hitKm && r.p.same_channel; });
    return { rows: rows, ranked: ranked, hit: hit, srcRank: hit ? hit.rank : null, n: ranked.length, nearCount: near.length };
  }

  var api = { DEFAULTS: DEFAULTS, score: score, rank: rank };
  root.CWScore = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof self !== 'undefined' ? self : this);
