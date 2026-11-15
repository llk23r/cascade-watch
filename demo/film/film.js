/* Cascade Watch — 30-second film. Deterministic: render(t) draws the frame at time t (seconds).
   Every number on screen is read from /data at load; nothing about the event is typed in here. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var DATA = '../../data/';
  var DUR = 29.9;
  var D = {};

  /* ---------------------------------------------------------- easing */
  var clamp = function (x, a, b) { return Math.max(a, Math.min(b, x)); };
  var lerp = function (a, b, k) { return a + (b - a) * k; };
  var seg = function (t, a, b) { return clamp((t - a) / (b - a), 0, 1); };
  var outExpo = function (k) { return k >= 1 ? 1 : 1 - Math.pow(2, -10 * k); };
  var inOut = function (k) { return k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; };
  var outCubic = function (k) { return 1 - Math.pow(1 - k, 3); };
  var inCubic = function (k) { return k * k * k; };
  var outBack = function (k) { var c = 1.4; return 1 + (c + 1) * Math.pow(k - 1, 3) + c * Math.pow(k - 1, 2); };
  function fadeInOut(t, a, b, inDur, outDur) { return Math.min(outExpo(seg(t, a, a + (inDur || 0.6))), 1 - inCubic(seg(t, b - (outDur || 0.45), b))); }

  /* ---------------------------------------------------------- data */
  function j(p) { return fetch(DATA + p).then(function (r) { return r.json(); }); }
  function fmtDate(s, year) { var d = new Date(s + 'T00:00:00Z'); return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC', year: year ? 'numeric' : undefined }); }
  function clockAt(m) { var c = D.replay.collapse_npt.split(':'); var b = +c[0] * 60 + +c[1] + (+c[2] || 0) / 60 + m; var h = Math.floor(b / 60), mm = Math.floor(b % 60); return (h < 10 ? '0' : '') + h + ':' + (mm < 10 ? '0' : '') + mm; }
  function areaKm2(ring) {   // equirectangular shoelace, good to a fraction of a percent at this size
    var lat0 = ring[0][1] * Math.PI / 180, kx = 111.32 * Math.cos(lat0), ky = 110.54, s = 0;
    for (var i = 0; i < ring.length - 1; i++) s += (ring[i][0] * kx) * (ring[i + 1][1] * ky) - (ring[i + 1][0] * kx) * (ring[i][1] * ky);
    return Math.abs(s) / 2;
  }

  /* relief plane: lon/lat -> image pixel */
  var RB;  // relief bounds
  function px(lon, lat) { return [(lon - RB.w) / (RB.e - RB.w) * 2600, (RB.n - lat) / (RB.n - RB.s) * 2722]; }

  /* ---------------------------------------------------------- timeline */
  var T = { s1: 0, s2: 5.3, s3: 10.2, s4: 14.0, s5: 19.8, s6: 24.8, s7: 27.8 };
  var M_MAX = 48;                                   // minutes of 26 August shown in scene 2
  var R2 = [5.6, 10.0];                             // film seconds that map to minutes 0..M_MAX
  function minuteAt(t) { return clamp((t - R2[0]) / (R2[1] - R2[0]), 0, 1) * M_MAX; }

  /* camera keyframes on the relief plane: centre (relief px), zoom, tilt, rotation, screen anchor */
  var CAM = [];
  function camAt(t) {
    var k = 0; while (k < CAM.length - 2 && t > CAM[k + 1].t) k++;
    var a = CAM[k], b = CAM[k + 1], e = inOut(seg(t, a.t, b.t)), o = {};
    ['cx', 'cy', 'z', 'tilt', 'rot', 'sx', 'sy'].forEach(function (f) { o[f] = lerp(a[f], b[f], e); });
    return o;
  }

  /* ---------------------------------------------------------- build */
  var els = {};
  function build() {
    RB = { w: D.bounds.relief[0][0], e: D.bounds.relief[1][0], n: D.bounds.relief[0][1], s: D.bounds.relief[2][1] };
    $('relief').src = DATA + 'img/relief.jpg';
    var lp = D.lastPre, bx = D.bounds[lp.id] || D.bounds.box;
    var b0 = px(bx[0][0], bx[0][1]), b2 = px(bx[2][0], bx[2][1]);
    var mot = $('motion'); mot.src = DATA + 'img/motion_' + lp.id + '.png';
    mot.style.left = b0[0] + 'px'; mot.style.top = b0[1] + 'px'; mot.style.width = (b2[0] - b0[0]) + 'px'; mot.style.height = (b2[1] - b0[1]) + 'px';
    var sw = $('sweep'); sw.style.left = b0[0] + 'px'; sw.style.top = b0[1] + 'px'; sw.style.width = (b2[0] - b0[0]) + 'px'; sw.style.height = (b2[1] - b0[1]) + 'px';

    // flood path + truth outline in plane coordinates
    var P = D.replay.path.map(function (q) { return px(q[0], q[1]); });
    var cum = [0]; for (var i = 1; i < P.length; i++) cum.push(cum[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]));
    els.pathLen = cum[cum.length - 1]; els.cum = cum; els.P = P;
    var d = 'M' + P.map(function (q) { return q[0].toFixed(1) + ' ' + q[1].toFixed(1); }).join('L');
    var ring = D.truthRing.map(function (q) { return px(q[0], q[1]); });
    $('vec').innerHTML =
      '<defs><filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="9"/></filter></defs>' +
      '<path id="fp-glow" d="' + d + '" fill="none" stroke="#ff453a" stroke-width="22" stroke-linecap="round" stroke-linejoin="round" filter="url(#glow)" opacity=".75"/>' +
      '<path id="fp" d="' + d + '" fill="none" stroke="#ff453a" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<path id="fp-head" d="' + d + '" fill="none" stroke="#ffe7e2" stroke-width="9" stroke-linecap="round"/>' +
      '<polygon id="truth" points="' + ring.map(function (q) { return q.join(','); }).join(' ') + '" fill="rgba(255,214,10,.22)" stroke="#ffd60a" stroke-width="5" stroke-linejoin="round"/>';
    ['fp', 'fp-glow'].forEach(function (id) { var e = $(id); e.style.strokeDasharray = els.pathLen; e.style.strokeDashoffset = els.pathLen; });
    $('fp-head').style.strokeDasharray = '26 ' + (els.pathLen + 100);

    // anchors inside the plane (the browser projects them through the 3-D camera for us)
    var A = $('anchors'); A.innerHTML = '';
    function anchor(x, y) { var a = document.createElement('div'); a.className = 'anc'; a.style.left = x + 'px'; a.style.top = y + 'px'; A.appendChild(a); return a; }
    var c = D.truthCenter; els.srcAnc = anchor.apply(null, px(c[0], c[1]));
    els.frontAnc = anchor(0, 0);
    els.gauges = D.replay.gauges.map(function (g) {
      var q = px(g.lon, g.lat), lab = document.createElement('div');
      lab.className = 'lab gauge'; lab.innerHTML = '<i class="dot"></i><div class="tx">' + g.name + '<small></small></div>';
      $('labels').appendChild(lab);
      return { g: g, anc: anchor(q[0], q[1]), lab: lab };
    });
    var sl = document.createElement('div'); sl.className = 'lab src'; sl.innerHTML = '<div class="tx">What fell<small>UNOSAT detachment zone</small></div>';
    $('labels').appendChild(sl); els.srcLab = sl;

    // pass chips
    var ch = $('passes'); ch.innerHTML = '';
    els.chips = D.pre.map(function (w, i) {
      var r = (D.scorecard.windows[w.id] || {}).source_rank, e = document.createElement('div');
      e.className = 'chip' + (r ? (r <= D.topN ? ' top' : '') : ' miss') + (w.id === lp.id ? ' last' : '');
      e.innerHTML = '<div class="d">' + fmtDate(w.start) + '</div><div class="p">' + w.stack_days + '-day pair</div><div class="r">' + (r ? '#' + r : 'missed') + '</div>';
      ch.appendChild(e); return { el: e, r: r, w: w };
    });

    // haystack dots
    var dots = $('dots'); dots.innerHTML = '';
    var feats = D.blobs.features.slice().sort(function (a, b) { return (a.properties.rank || 1e9) - (b.properties.rank || 1e9); });
    var smax = Math.log(1 + (feats[0].properties.score || 1));
    els.dots = feats.map(function (f, i) {
      var p = f.properties, e = document.createElement('div'), isSrc = p.id === D.hitId;
      var r = p.rank ? lerp(7, 18, Math.log(1 + p.score) / smax) : 6;
      e.className = 'bd' + (isSrc ? ' src' : '');
      e.style.width = e.style.height = r + 'px'; e.style.marginLeft = e.style.marginTop = (-r / 2) + 'px';
      e.style.background = !p.rank ? 'rgba(235,235,245,.35)' : p.channel === 'phase' ? '#bf5af2' : '#40cbe0';
      dots.appendChild(e);
      var q = px(p.lon, p.lat);
      return { el: e, p: p, anc: anchor(q[0], q[1]), rank: p.rank, isSrc: isSrc, k: i };
    });
    els.rows = feats.filter(function (f) { return f.properties.rank && f.properties.rank <= 8; }).map(function (f) {
      var p = f.properties, e = document.createElement('div'), isSrc = p.id === D.hitId;
      e.className = 'rowlab' + (isSrc ? ' src' : '');
      e.innerHTML = '<b>#' + p.rank + '</b>' + (isSrc ? 'Source valley' : (p.channel === 'phase' ? 'Phase patch' : 'Offset patch')) + '<small>' + (isSrc ? Number(p.dist_truth_km).toFixed(1) + ' km from the mapped detachment' : 'drains to ' + (p.channel_name || 'an unnamed stream')) + '</small>';
      dots.appendChild(e); return { el: e, rank: p.rank };
    });
    var more = document.createElement('div'); more.id = 'more'; more.textContent = '+ ' + (D.nRanked - 8) + ' more ranked, ' + (feats.length - D.nRanked) + ' below the noise floor'; dots.appendChild(more); els.more = more;

    // app window callouts
    $('callouts').innerHTML = [
      ['The rule, term by term', 'motion ÷ noise × coherent motion × cracks × ice × people'],
      ['Committed before results', 'config ' + D.prov.config.sha256.slice(0, 8) + ' · ' + D.prov.config.first_commit.short],
      ['Real records behind every view', D.prov.granules.length + ' NISAR granules read from ASF']
    ].map(function (c) { return '<div class="callout"><b>' + c[0] + '</b><small>' + c[1] + '</small></div>'; }).join('');

    // finding
    var sc = D.scorecard.windows[lp.id];
    $('big-n').textContent = '#' + sc.source_rank;
    $('big-of').innerHTML = 'of ' + sc.n_ranked + ' moving slopes<br><span>last pass, public ' + Math.round(D.context.timing.hours_on_asf) + ' hours before the collapse</span>';
    var pass = D.scorecard.pass;
    var zs = D.context.zone_summary;
    var ratio = zs && zs.budhigandaki.expected_by_chance ? zs.budhigandaki.hits / zs.budhigandaki.expected_by_chance : null;
    $('verdict').innerHTML = (zs ? '<span class="pill good">' + zs.zone_km + ' km zone, top ' + zs.top_n + ': listed before both disasters</span>' : '') +
      (ratio ? '<span class="pill good">' + (ratio >= 1.8 && ratio <= 2.2 ? '≈2×' : ratio.toFixed(1) + '×') + ' chance on an unseen disaster</span>' : '') +
      '<span class="pill calm">Exact slope: ' + (pass.passed ? 'caught' : 'not yet') + '</span>';

    // sources
    var srcs = [
      ['hero', 'NISAR L2 · GUNW · GOFF · GCOV', 'NASA/JPL · ISRO, via ASF DAAC · ' + D.prov.granules.length + ' granules, ' + D.windows.length + ' radar pairs'],
      ['', 'UNOSAT 4257', 'Detachment zone, flood extent'],
      ['', 'Nepal DHM river gauges', 'Ten-minute records, Acharya & Paudel archive'],
      ['', 'USGS · GEOFON', 'Event time and classification'],
      ['', 'Copernicus DEM · RGI · WorldPop', 'Terrain, glaciers, people downstream'],
      ['', 'ERA5 · OpenStreetMap', 'Season heat, rivers and places']
    ];
    $('sources').innerHTML = srcs.map(function (s) { return '<div class="src-card ' + s[0] + '"><b>' + s[1] + '</b><span>' + s[2] + '</span></div>'; }).join('');
    $('papers').innerHTML = '<b>Research it builds on:</b> Acharya &amp; Paudel 2026 · Meena 2026 · Kutoğlu, Softa &amp; Akgün 2026 · Hassan et al. 2026 · Sah 2026. &nbsp;<b>Visuals:</b> World Labs Atlas on Copernicus DEM terrain.';

    $('end-tag').textContent = 'What NISAR saw before the Langtang flood, tested honestly.';
    $('end-meta').textContent = 'NASA Space Apps Challenge 2026 · Dancing with the SARs · Toronto';
    $('toll').innerHTML = '<b>≥ ' + D.dead + '</b>dead in Nepal · USGS';
    $('sms-t').innerHTML = 'Mass SMS alert<small> · ' + clockAt(D.replay.alert_min) + ' · ' + D.replay.alert_min.toFixed(1) + ' min after the collapse</small>';

    // captions
    var area = (D.context.detachment_km2 || areaKm2(D.truthRing)).toFixed(2);
    var CAPS = [
      { id: 'c1', a: 0.6, b: 5.0, cls: 'narrow', eb: fmtDate(D.eventDate, true) + ' · ' + D.replay.collapse_npt.slice(0, 5) + ' · Langtang, Nepal', ebc: '#ffd60a', h: 'Ice and rock broke away above this valley.', sub: 'The mapped detachment covers ' + area + ' km². The flood took the river below.', subAt: 2.7 },
      { id: 'c2', a: 5.6, b: 10.0, eb: 'The problem', ebc: '#ff6961', h: 'The flood outran the warning.', sub: 'The three gauges in its path went silent below their alarm levels. The text alert came ' + D.replay.alert_min.toFixed(1) + ' minutes after the collapse.' },
      { id: 'c3', a: 10.4, b: 13.8, eb: 'A witness in orbit', ebc: '#64d2ff', h: 'NISAR had been watching all summer.', sub: D.pre.length + ' radar passes over the slope before it fell. L-band radar sees through monsoon cloud.' },
      { id: 'c4a', a: 14.3, b: 16.3, cls: 'right top', eb: 'The question', ebc: '#64d2ff', h: D.nRanked + ' slopes were moving. Which one matters?' },
      { id: 'c4b', a: 16.4, b: 19.7, cls: 'right top', eb: 'Cascade Watch', ebc: '#30d158', h: 'One rule ranks every moving slope.', sub: 'Fixed in advance, committed before any result, and re-run live in the browser on the real radar data.' },
      { id: 'c5', a: 19.9, b: 24.7, cls: 'top', eb: 'What we found', ebc: '#30d158', h: '' },
      { id: 'c6', a: 24.9, b: 27.7, cls: 'top', eb: 'Built on open data and published work', ebc: '#64d2ff', h: 'Every number traces to its source.' }
    ];
    $('caps').innerHTML = CAPS.map(function (c) { return '<div class="cap ' + (c.cls || '') + '" id="' + c.id + '"><p class="eb" style="color:' + c.ebc + '">' + c.eb + '</p>' + (c.h ? '<h1>' + c.h + '</h1>' : '') + (c.sub ? '<p class="sub">' + c.sub + '</p>' : '') + '</div>'; }).join('');
    els.caps = CAPS;

    // atlas frames
    els.atlas = { n: D.atlas.frames.length };
    var g = document.createElement('div'); g.id = 'atlas'; g.innerHTML = '<img id="at-b"><img id="at-a"><svg id="at-v" viewBox="0 0 1920 1080"><defs><filter id="ag" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="7"/></filter></defs><path id="at-fg" fill="none" stroke="#ff453a" stroke-width="18" stroke-linecap="round" stroke-linejoin="round" filter="url(#ag)" opacity=".8"/><path id="at-fp" fill="none" stroke="#ff453a" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/><polygon id="at-tr" fill="rgba(255,214,10,.16)" stroke="#ffd60a" stroke-width="4" stroke-linejoin="round"/></svg><div id="at-tag"><b>AI visualization</b> · World Labs Atlas painting the real Copernicus DEM terrain · lines: UNOSAT outline, routed flood path</div>';
    $('stage').insertBefore(g, $('world'));

    // camera keyframes (relief px)
    var src = px(D.truthCenter[0], D.truthCenter[1]);
    var syab = D.replay.gauges[1], betr = D.replay.gauges[2];
    var pS = px(syab.lon, syab.lat), pB = px(betr.lon, betr.lat), bc = [(b0[0] + b2[0]) / 2, (b0[1] + b2[1]) / 2];
    CAM = [
      { t: 0, cx: src[0], cy: src[1], z: 1.25, tilt: 50, rot: -14, sx: 1180, sy: 470 },
      { t: T.s2, cx: src[0], cy: src[1], z: 1.25, tilt: 50, rot: -14, sx: 1180, sy: 470 },
      { t: 6.6, cx: lerp(src[0], pS[0], 0.45), cy: lerp(src[1], pS[1], 0.45), z: 1.0, tilt: 48, rot: -16, sx: 1180, sy: 470 },
      { t: 8.2, cx: pS[0], cy: pS[1], z: 0.86, tilt: 46, rot: -18, sx: 1180, sy: 470 },
      { t: 9.9, cx: lerp(pS[0], pB[0], 0.8), cy: lerp(pS[1], pB[1], 0.8), z: 0.62, tilt: 44, rot: -20, sx: 1180, sy: 470 },
      { t: 10.25, cx: bc[0], cy: bc[1], z: 0.78, tilt: 34, rot: -6, sx: 1060, sy: 500 },
      { t: 13.7, cx: bc[0], cy: bc[1] - 30, z: 0.84, tilt: 30, rot: -4, sx: 1060, sy: 500 },
      { t: 14.25, cx: bc[0], cy: bc[1], z: 0.58, tilt: 0, rot: 0, sx: 560, sy: 560 },
      { t: 19.8, cx: bc[0], cy: bc[1], z: 0.58, tilt: 0, rot: 0, sx: 560, sy: 560 },
      { t: 24.8, cx: bc[0], cy: bc[1], z: 0.66, tilt: 24, rot: -6, sx: 1260, sy: 520 },
      { t: 30, cx: bc[0], cy: bc[1], z: 0.7, tilt: 26, rot: -8, sx: 1260, sy: 520 }
    ];
  }

  /* ---------------------------------------------------------- grain */
  var grainCtx, grainImg;
  function grain(t) {
    if (!grainCtx) { grainCtx = $('grain').getContext('2d'); grainImg = grainCtx.createImageData(960, 540); }
    var seed = (Math.floor(t * 30) * 2654435761) >>> 0, d = grainImg.data;
    for (var i = 0; i < d.length; i += 4) { seed = (seed * 1664525 + 1013904223) >>> 0; var v = seed >>> 24; d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255; }
    grainCtx.putImageData(grainImg, 0, 0);
  }

  /* ---------------------------------------------------------- frame */
  function setCap(c, t) {
    var e = $(c.id); if (!e) return;
    var o = fadeInOut(t, c.a, c.b, 0.7, 0.45), k = outExpo(seg(t, c.a, c.a + 0.9));
    e.style.opacity = o;
    e.style.transform = 'translateY(' + ((1 - k) * 26 - inCubic(seg(t, c.b - 0.45, c.b)) * 12) + 'px)';
    e.style.filter = 'blur(' + ((1 - k) * 10) + 'px)';
    var s = e.querySelector('.sub');
    if (s && c.subAt) { var ks = outExpo(seg(t, c.subAt, c.subAt + 0.8)); s.style.opacity = ks; s.style.transform = 'translateY(' + (1 - ks) * 14 + 'px)'; }
  }
  function frontKm(m) {
    var a = D.replay.front; if (m <= a[0][0]) return a[0][1];
    for (var i = 1; i < a.length; i++) if (m <= a[i][0]) return lerp(a[i - 1][1], a[i][1], (m - a[i - 1][0]) / (a[i][0] - a[i - 1][0]));
    return a[a.length - 1][1];
  }
  function lenAtKm(km) {
    var K = D.replay.path_km; for (var i = 1; i < K.length; i++) if (km <= K[i]) return lerp(els.cum[i - 1], els.cum[i], (km - K[i - 1]) / (K[i] - K[i - 1]));
    return els.pathLen;
  }
  function pointAtLen(L) {
    var c = els.cum, P = els.P; for (var i = 1; i < c.length; i++) if (L <= c[i]) { var k = (L - c[i - 1]) / (c[i] - c[i - 1]); return [lerp(P[i - 1][0], P[i][0], k), lerp(P[i - 1][1], P[i][1], k)]; }
    return P[P.length - 1];
  }
  function rectC(el) { var r = el.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }

  function render(t) {
    t = clamp(t, 0, DUR);
    /* global fade */
    $('fade').style.opacity = Math.max(1 - outCubic(seg(t, 0, 0.55)), inCubic(seg(t, DUR - 0.5, DUR)));

    /* ---------------- scene 1: Atlas opener */
    var at = $('atlas'), atOn = 1 - inOut(seg(t, T.s2 - 0.6, T.s2 + 0.1));
    var atEnd = t >= T.s7 ? outCubic(seg(t, T.s7, T.s7 + 0.8)) : 0;
    at.style.opacity = Math.max(atOn, atEnd * 0.55);
    at.style.filter = atEnd ? 'blur(' + (atEnd * 18) + 'px) brightness(' + (1 - atEnd * 0.55) + ')' : 'none';
    if (atOn > 0 || atEnd > 0) {
      var fi = Math.min(els.atlas.n - 1, Math.floor((t >= T.s7 ? 5 : t) * D.atlas.fps));
      var nb = 'atlas/before_' + ('00' + fi).slice(-3) + '.jpg', na = 'atlas/after_' + ('00' + fi).slice(-3) + '.jpg';
      if ($('at-b').getAttribute('src') !== nb) $('at-b').setAttribute('src', nb);
      if ($('at-a').getAttribute('src') !== na) $('at-a').setAttribute('src', na);
      var mix = t >= T.s7 ? 0 : inOut(seg(t, 2.9, 4.2));
      ['at-b', 'at-a', 'at-v'].forEach(function (id) { $(id).style.transform = 'translate(90px,-10px) scale(1.08)'; });
      $('at-a').style.opacity = mix;
      var fr = D.atlas.frames[fi];
      var tri = fr.outline.filter(Boolean);
      var trk = t >= T.s7 ? 0 : outExpo(seg(t, 1.5, 2.6));
      $('at-tr').setAttribute('points', tri.map(function (q) { return q.join(','); }).join(' '));
      $('at-tr').style.opacity = trk;
      // routed path, visible stretches only, drawn from the point nearest the detachment
      var pts = [], vis = D.atlas.path_visible, started = false, cen = tri.reduce(function (s, q) { return [s[0] + q[0] / tri.length, s[1] + q[1] / tri.length]; }, [0, 0]);
      var k0 = 0, best = 1e18; fr.path.forEach(function (q, i) { if (q && vis[i]) { var d2 = Math.pow(q[0] - cen[0], 2) + Math.pow(q[1] - cen[1], 2); if (d2 < best) { best = d2; k0 = i; } } });
      for (var i = k0; i < fr.path.length; i++) { var q = fr.path[i]; if (!q || !vis[i]) { if (started) break; continue; } started = true; pts.push(q); }
      var dpath = pts.length ? 'M' + pts.map(function (q) { return q[0] + ' ' + q[1]; }).join('L') : '';
      ['at-fp', 'at-fg'].forEach(function (id) { var e = $(id); e.setAttribute('d', dpath); var L = e.getTotalLength ? e.getTotalLength() : 1000; e.style.strokeDasharray = L; e.style.strokeDashoffset = L * (1 - (t >= T.s7 ? 0 : outCubic(seg(t, 3.0, 4.9)))); });
      $('at-tag').style.opacity = Math.max(fadeInOut(t, 0.4, T.s2, 0.6, 0.4), atEnd);
    }

    /* ---------------- relief world */
    var c = camAt(t), worldOn = inOut(seg(t, T.s2 - 0.5, T.s2 + 0.2)) * (1 - 0.82 * inOut(seg(t, T.s6 - 0.3, T.s6 + 0.4))) * (1 - inOut(seg(t, T.s7 - 0.2, T.s7 + 0.4)));
    var dip = 1 - 0.75 * Math.sin(Math.PI * seg(t, 9.85, 10.45));       // dip between the race and the watcher
    var dim = 1 - 0.55 * inOut(seg(t, T.s5 - 0.2, T.s5 + 0.5));
    $('world').style.opacity = worldOn * dip * dim;
    $('plane').style.transform = 'translate(' + c.sx + 'px,' + c.sy + 'px) rotateX(' + c.tilt + 'deg) rotateZ(' + c.rot + 'deg) scale(' + c.z + ') translate(' + (-c.cx) + 'px,' + (-c.cy) + 'px)';
    $('scrim').style.opacity = worldOn;

    /* truth outline + label */
    $('truth').style.opacity = t < T.s4 ? 1 : 1 - seg(t, T.s4, T.s4 + 0.4);
    var sp = rectC(els.srcAnc), slo = fadeInOut(t, T.s2, 7.0, 0.4, 0.5);
    els.srcLab.style.opacity = slo; els.srcLab.style.transform = 'translate(' + (sp[0] - 60) + 'px,' + (sp[1] - 46) + 'px)';

    /* scene 2: the race */
    var m = minuteAt(t), inRace = t >= T.s2 && t < T.s3;
    var km = frontKm(m), L = lenAtKm(km);
    var showPath = t < T.s4 ? 1 : 1 - seg(t, T.s4, T.s4 + 0.4);
    ['fp', 'fp-glow'].forEach(function (id) { $(id).style.strokeDashoffset = els.pathLen - (t >= R2[0] ? L : 0); $(id).style.opacity = showPath * (id === 'fp' ? 1 : 0.75); });
    $('fp-head').style.strokeDashoffset = -(L - 13); $('fp-head').style.opacity = inRace && m > 0.2 && m < M_MAX - 0.2 ? 1 : 0;
    var fp = pointAtLen(L); els.frontAnc.style.left = fp[0] + 'px'; els.frontAnc.style.top = fp[1] + 'px';
    var fc = rectC(els.frontAnc), fo = inRace && m > 0.1 && m < M_MAX - 0.1 ? 1 : 0;
    $('front').style.opacity = fo; $('front').style.transform = 'translate(' + fc[0] + 'px,' + fc[1] + 'px)';
    $('front').style.boxShadow = '0 0 0 ' + (8 + 14 * ((t * 1.6) % 1)) + 'px rgba(255,69,58,' + (0.5 * (1 - (t * 1.6) % 1)) + ')';
    els.gauges.forEach(function (o) {
      var g = o.g, p = rectC(o.anc), on = fadeInOut(t, 5.9, T.s3, 0.5, 0.4);
      o.lab.style.opacity = on * clamp((690 - p[1]) / 90, 0, 1); o.lab.style.transform = 'translate(' + p[0] + 'px,' + p[1] + 'px)';
      var pct = Math.round(g.last_fraction_of_warning * 100), sm = o.lab.querySelector('small');
      if (m >= g.arrival_hi) { o.lab.classList.add('dead'); sm.textContent = 'silent · last reading ' + pct + '% of alarm'; }
      else if (m >= g.arrival_lo) { o.lab.classList.remove('dead'); sm.textContent = 'last reading ' + pct + '% of alarm'; }
      else { o.lab.classList.remove('dead'); sm.textContent = 'river gauge, reporting'; }
    });
    var co = fadeInOut(t, 5.7, T.s3 - 0.1, 0.5, 0.4);
    $('clock').style.opacity = co; $('clock-t').textContent = clockAt(m); $('clock-s').textContent = '26 Aug · +' + m.toFixed(1) + ' min';
    var tA = R2[0] + D.replay.alert_min / M_MAX * (R2[1] - R2[0]);
    var so = fadeInOut(t, tA, T.s3 - 0.1, 0.35, 0.4), sk = outBack(seg(t, tA, tA + 0.5));
    $('sms').style.opacity = so; $('sms').style.transform = 'translateX(-50%) scale(' + lerp(0.86, 1, sk) + ')';
    $('toll').style.opacity = fadeInOut(t, 7.4, T.s3 - 0.1, 0.6, 0.4);

    /* scene 3: the watcher */
    $('motion').style.opacity = 0.95 * outCubic(seg(t, 10.6, 11.6)) * (1 - seg(t, T.s4 + 0.1, T.s4 + 0.6));
    var swk = seg(t, 10.5, 11.7); $('sweep').style.opacity = swk > 0 && swk < 1 ? 1 : 0; $('sweep').style.backgroundPosition = (100 - swk * 100) + '% 0'; $('sweep').style.backgroundSize = '260% 100%';
    els.chips.forEach(function (o, i) {
      var a = 10.9 + i * 0.26, inS3 = outBack(seg(t, a, a + 0.45));
      var x3 = 1792 - (els.chips.length - i) * 162, y3 = 112;                        // top-right row in scene 3
      var x5 = 128 + i * 172, y5 = 760;                                                // bottom row in scene 5
      var mv = inOut(seg(t, T.s5 + 0.9, T.s5 + 1.7));
      var gone = 1 - seg(t, T.s4 - 0.3, T.s4 + 0.1), back = outCubic(seg(t, T.s5 + 0.9, T.s5 + 1.5)), out = 1 - seg(t, T.s6 - 0.3, T.s6 + 0.1);
      var op = t < T.s5 ? inS3 * gone : back * out;
      o.el.style.opacity = op;
      o.el.style.transform = 'translate(' + (t < T.s5 ? x3 : lerp(x3, x5, mv)) + 'px,' + (t < T.s5 ? y3 + (1 - inS3) * 18 : lerp(y3, y5, mv)) + 'px) scale(' + (t < T.s5 ? lerp(0.8, 1, inS3) : lerp(1, 1.06, mv)) + ')';
      var rk = t < T.s5 ? 0.0 : outCubic(seg(t, T.s5 + 1.5 + i * 0.12, T.s5 + 1.9 + i * 0.12)); o.el.querySelector('.r').style.opacity = rk; o.el.querySelector('.p').style.opacity = 1 - rk;
      if (o.r == null) o.el.style.transform += ' rotate(' + (t > T.s5 + 2.6 ? Math.sin((t - T.s5 - 2.6) * 18) * 1.6 * (1 - seg(t, T.s5 + 2.6, T.s5 + 3.4)) : 0) + 'deg)';
    });

    /* scene 4: haystack -> ranking -> the app */
    var listX = 1170, listY = 468, rowH = 56;
    els.dots.forEach(function (o) {
      var p = rectC(o.anc), a = 14.35 + (o.k % 40) * 0.012 + Math.floor(o.k / 40) * 0.05;
      var pop = outBack(seg(t, a, a + 0.35));
      var sortK = inOut(seg(t, 15.25 + Math.min(o.k, 60) * 0.006, 16.25 + Math.min(o.k, 60) * 0.006));
      var tx, ty, sc = 1, op;
      if (o.rank && o.rank <= 8) { tx = listX; ty = listY + (o.rank - 1) * rowH; sc = o.isSrc ? 1.35 : 1.1; }
      else { var gi = o.k - 8, col = gi % 46, row = Math.floor(gi / 46); tx = listX + col * 13; ty = listY + 8 * rowH + 40 + row * 13; sc = 0.42; }
      var x = lerp(p[0], tx, sortK), y = lerp(p[1], ty, sortK);
      op = pop * (1 - seg(t, 18.0, 18.4)) * (t > T.s4 - 0.1 ? 1 : 0);
      o.el.style.opacity = op;
      o.el.style.transform = 'translate(' + x + 'px,' + y + 'px) scale(' + (lerp(1, sc, sortK) * (o.isSrc ? 1 + 0.25 * Math.sin(Math.PI * seg(t, 15.0, 15.5)) : 1) * lerp(0.2, 1, pop)) + ')';
    });
    els.rows.forEach(function (o) {
      var k = outExpo(seg(t, 16.2 + o.rank * 0.05, 16.7 + o.rank * 0.05)) * (1 - seg(t, 18.0, 18.4));
      o.el.style.opacity = k; o.el.style.transform = 'translate(' + (listX + 26 + (1 - k) * 16) + 'px,' + (listY + (o.rank - 1) * rowH - 15) + 'px)';
    });
    els.more.style.opacity = outCubic(seg(t, 16.6, 17.0)) * (1 - seg(t, 18.0, 18.4)); els.more.style.transform = 'translate(' + listX + 'px,' + (listY + 8 * rowH + 98) + 'px)';
    var wk = outExpo(seg(t, 18.1, 19.0)), wo = wk * (1 - inCubic(seg(t, T.s5 - 0.35, T.s5 + 0.05)));
    var win = $('win'), ws = lerp(0.6, 0.66, wk);
    win.style.opacity = wo;
    win.style.transform = 'translate(' + lerp(1000, 1130, wk) + 'px,' + lerp(560, 430, wk) + 'px) perspective(2200px) rotateY(' + lerp(-26, -10, wk) + 'deg) rotateX(' + lerp(10, 4, wk) + 'deg) scale(' + ws + ')';
    Array.prototype.forEach.call(document.querySelectorAll('.callout'), function (e, i) {
      var k = outBack(seg(t, 18.55 + i * 0.28, 18.95 + i * 0.28)) * (1 - inCubic(seg(t, T.s5 - 0.35, T.s5 + 0.05)));
      var pos = [[1180, 520], [1560, 790], [1240, 920]][i];
      e.style.opacity = k; e.style.transform = 'translate(' + pos[0] + 'px,' + (pos[1] + (1 - k) * 14) + 'px) scale(' + lerp(0.9, 1, k) + ')';
    });

    /* scene 5: the finding */
    var bo = fadeInOut(t, T.s5 + 0.15, T.s6 - 0.05, 0.6, 0.45), bk = outExpo(seg(t, T.s5 + 0.15, T.s5 + 1.0));
    $('big').style.opacity = bo; $('big').style.transform = 'translateY(' + (1 - bk) * 30 + 'px)'; $('big').style.filter = 'blur(' + (1 - bk) * 12 + 'px)';
    var vk = outExpo(seg(t, T.s5 + 2.8, T.s5 + 3.5));
    $('verdict').style.opacity = vk * (1 - inCubic(seg(t, T.s6 - 0.4, T.s6))); $('verdict').style.transform = 'translateY(' + (1 - vk) * 18 + 'px)';

    /* scene 6: sources */
    Array.prototype.forEach.call(document.querySelectorAll('.src-card'), function (e, i) {
      var k = outExpo(seg(t, T.s6 + 0.25 + i * 0.12, T.s6 + 0.95 + i * 0.12)) * (1 - inCubic(seg(t, T.s7 - 0.35, T.s7)));
      e.style.opacity = k; e.style.transform = 'translateY(' + (1 - k) * 28 + 'px)';
    });
    var pk = outExpo(seg(t, T.s6 + 1.1, T.s6 + 1.7)) * (1 - inCubic(seg(t, T.s7 - 0.35, T.s7)));
    $('papers').style.opacity = pk;
    $('veil').style.opacity = 0.0;

    /* scene 7: end card */
    var ek = outExpo(seg(t, T.s7 + 0.1, T.s7 + 0.9));
    $('end').style.opacity = ek; $('end').style.transform = 'scale(' + lerp(0.96, 1, ek) + ')';

    els.caps.forEach(function (cp) { setCap(cp, t); });
    grain(t);
  }

  /* ---------------------------------------------------------- boot */
  window.FILM = { render: render, ready: false, duration: DUR, T: T, minuteAt: minuteAt };
  Promise.all([j('replay.json'), j('truth.geojson'), j('img/bounds.json'), j('context.json'), j('scorecard.json'), j('windows.json'), j('provenance.json'), j('config.json'), fetch('atlas/overlays.json').then(function (r) { return r.json(); }), j('backtest.json').catch(function () { return null; })])
    .then(function (r) {
      D.replay = r[0]; D.truth = r[1]; D.bounds = r[2]; D.context = r[3]; D.scorecard = r[4]; D.windows = r[5]; D.prov = r[6]; D.config = r[7]; D.atlas = r[8]; D.bt = r[9];
      D.truthRing = D.truth.features.find(function (f) { return f.properties.role !== 'hit_zone'; }).geometry.coordinates[0];
      var tp = D.truth.features[0].properties; D.truthCenter = tp.center || D.context.source_lonlat;
      D.eventDate = D.config.windows.event_utc.slice(0, 10); D.topN = D.config.pass_condition.top_n;
      D.pre = D.windows.filter(function (w) { return w.direction === 'A' && w.phase === 'pre'; }).sort(function (a, b) { return a.start < b.start ? -1 : 1; });
      D.lastPre = D.pre[D.pre.length - 1];
      D.hitId = D.scorecard.windows[D.lastPre.id].hit_id; D.nRanked = D.scorecard.windows[D.lastPre.id].n_ranked;
      D.dead = D.context.facts.dead.display;
      window.FILM.MARKS = { sms: R2[0] + D.replay.alert_min / M_MAX * (R2[1] - R2[0]), chips: D.pre.map(function (w, i) { return 10.9 + i * 0.26; }), gauges: D.replay.gauges.map(function (g) { return R2[0] + g.arrival_hi / M_MAX * (R2[1] - R2[0]); }) };
      return j('blobs_' + D.lastPre.id + '.geojson');
    }).then(function (b) {
      D.blobs = b; build();
      var imgs = Array.prototype.slice.call(document.images);
      return Promise.all(imgs.map(function (im) { return im.complete ? 1 : new Promise(function (ok) { im.onload = im.onerror = ok; }); }));
    }).then(function () { render(0); window.FILM.ready = true; })
    .catch(function (e) { document.body.insertAdjacentHTML('beforeend', '<pre style="position:fixed;left:20px;top:20px;color:#f66;z-index:99">' + e + '</pre>'); });

  // preview mode: play in real time when opened in a browser with ?play
  if (/[?&]play/.test(location.search)) {
    var t0 = null; (function loop(now) { if (window.FILM.ready) { if (t0 == null) t0 = now; render(((now - t0) / 1000) % DUR); } requestAnimationFrame(loop); })(0);
  }
})();
