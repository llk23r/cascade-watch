/* Cascade Watch — page logic. Everything drawn here is read from /data (pipeline output). */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var DATA = 'data/';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var CS = getComputedStyle(document.documentElement);
  var tok = function (n) { return CS.getPropertyValue(n).trim(); };
  var COL = { night: tok('--night'), ice: tok('--ice'), mist: tok('--mist'), dim: tok('--dim'), fa: tok('--fa'), fb: tok('--fb'), fc: tok('--fc'),
    alarm: tok('--alarm'), safe: tok('--safe'), mark: tok('--mark'), water: tok('--water'), rule: tok('--rule') };

  var D = {};            // loaded data
  var BLOBS = {};        // window id -> FeatureCollection
  var S = {              // UI state
    ch: 1, track: 'A', win: null, mode: 'window', t: 0, layer: 'motion', playing: false,
    w: Object.assign({}, CWScore.DEFAULTS), rules: { sigma: true, dir: true, opp: true, ice: true, people: true, season: true },
    d: { record: 0.5, recognise: 2, confirm: 1, broadcast: 1 }, radarMix: 1
  };
  var HIT_KM = 2;
  var map, markers = { villages: [], ranks: [], label: null, front: null };
  var cur = null; // current ranking result

  /* ---------------------------------------------------------------- utils */
  function fetchJSON(p, opt) {
    return fetch(DATA + p).then(function (r) { if (!r.ok) throw new Error(p + ' ' + r.status); return r.json(); })
      .catch(function (e) { if (opt) return null; throw e; });
  }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function fmtDate(s, long) { var d = new Date(s + 'T00:00:00Z'); return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC', year: long ? 'numeric' : undefined }); }
  function winLabel(w) { return fmtDate(w.start) + ' → ' + fmtDate(w.end); }
  function clockAt(t) { var base = 8 * 60 + 37 + 10 / 60; var m = base + t; var h = Math.floor(m / 60), mm = Math.floor(m % 60); return (h < 10 ? '0' : '') + h + ':' + (mm < 10 ? '0' : '') + mm; }
  function signed(v, d) { var r = d ? v.toFixed(d) : String(Math.round(v)); return (v < 0 ? '−' + r.replace('-', '') : '+' + r); }
  function cite(key) {
    var src = D.context && D.context.sources && D.context.sources[key];
    if (!src) return '';
    return ' <a class="cite" href="' + esc(src.url) + '" target="_blank" rel="noopener">' + esc(src.short) + '</a>';
  }
  function fact(key) { return D.context && D.context.facts && D.context.facts[key]; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function tween(from, to, ms, fn) {
    if (reduce || from === to) { fn(to); return; }
    var t0 = performance.now();
    (function step(now) { var k = Math.min(1, (now - t0) / ms); var e = 1 - Math.pow(1 - k, 3); fn(lerp(from, to, e)); if (k < 1) requestAnimationFrame(step); })(t0);
  }

  /* ---------------------------------------------------------------- windows */
  function trackWindows() {
    return D.windows.filter(function (w) { return w.direction === S.track && w.start < '2026-09-01' && w.end > '2026-05-20'; })
      .sort(function (a, b) { return a.start < b.start ? -1 : 1; });
  }
  function preWindows() { return trackWindows().filter(function (w) { return w.phase === 'pre'; }); }
  function coWindows() { return trackWindows().filter(function (w) { return w.phase === 'co'; }); }
  function lastPre() { var p = preWindows(); return p[p.length - 1]; }
  function winById(id) { return D.windows.find(function (w) { return w.id === id; }); }
  function activeWindow() { return S.mode === 'event' ? lastPre() : winById(S.win); }

  function loadBlobs(id) {
    if (BLOBS[id]) return Promise.resolve(BLOBS[id]);
    return fetchJSON('blobs_' + id + '.geojson').then(function (fc) { BLOBS[id] = fc; return fc; });
  }

  /* ---------------------------------------------------------------- scoring with rule toggles */
  function weights() {
    var w = Object.assign({}, S.w);
    if (!S.rules.opp) w.opposingBonus = 1;
    if (!S.rules.ice) w.iceBonus = 1;
    if (!S.rules.people) w.wPop = 0;
    if (!S.rules.season) w.wSeason = 0;
    return w;
  }
  function rankWindow(fc) {
    var w = weights();
    var feats = fc.features;
    if (!S.rules.sigma || !S.rules.dir) {
      feats = feats.map(function (f) {
        var p = Object.assign({}, f.properties);
        if (!S.rules.sigma) p.sigma_m = 1;             // speed only, not speed over its uncertainty
        if (!S.rules.dir) p.dir_coherence = 1;
        return { type: 'Feature', geometry: f.geometry, properties: p, _orig: f };
      });
    }
    return CWScore.rank(feats, w, HIT_KM);
  }

  /* ---------------------------------------------------------------- map */
  function initMap() {
    var b = D.bounds;
    var style = {
      version: 8,
      sources: {
        dem: { type: 'raster-dem', tiles: [location.href.replace(/[^/]*$/, '') + DATA + 'terrain/{z}/{x}/{y}.png'], encoding: 'terrarium', tileSize: 256, minzoom: 8, maxzoom: 13, bounds: [85.1, 27.85, 85.75, 28.45],
          attribution: 'Terrain: Copernicus DEM GLO-30 © ESA/DLR/Airbus' },
        relief: { type: 'image', url: DATA + 'img/relief.jpg', coordinates: b.relief },
        radarA: { type: 'image', url: DATA + 'img/' + (D.gcovPair[0] || 'relief') + '.jpg', coordinates: b.box },
        radarB: { type: 'image', url: DATA + 'img/' + (D.gcovPair[1] || 'relief') + '.jpg', coordinates: b.box },
        radarC: { type: 'image', url: DATA + 'img/' + (D.context.gcov_change || 'relief') + '.png', coordinates: b.box },
        win: { type: 'image', url: DATA + 'img/motion_' + lastPre().id + '.png', coordinates: b.box }
      },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': COL.night } },
        { id: 'relief', type: 'raster', source: 'relief', paint: { 'raster-fade-duration': 0 } },
        { id: 'radarA', type: 'raster', source: 'radarA', layout: { visibility: 'none' }, paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 } },
        { id: 'radarB', type: 'raster', source: 'radarB', layout: { visibility: 'none' }, paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 } },
        { id: 'radarC', type: 'raster', source: 'radarC', layout: { visibility: 'none' }, paint: { 'raster-opacity': 0.95, 'raster-fade-duration': 0 } },
        { id: 'win', type: 'raster', source: 'win', paint: { 'raster-opacity': 0.92, 'raster-fade-duration': 250 } }
      ],
      terrain: { source: 'dem', exaggeration: 1.35 }
    };
    map = new maplibregl.Map({ container: 'map', style: style, center: [85.42, 28.2], zoom: 9.6, pitch: 48, bearing: -12, maxPitch: 75,
      attributionControl: { compact: true }, minZoom: 8.5, maxZoom: 14.5, maxBounds: [[84.85, 27.7], [86.0, 28.6]] });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right');
    map.on('load', function () {
      map.addSource('box', { type: 'geojson', data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [b.box.concat([b.box[0]])] } } });
      map.addLayer({ id: 'box', type: 'line', source: 'box', paint: { 'line-color': COL.ice, 'line-opacity': 0.35, 'line-width': 1, 'line-dasharray': [3, 3] } });
      if (D.rivers) {
        map.addSource('rivers', { type: 'geojson', data: D.rivers });
        map.addLayer({ id: 'rivers', type: 'line', source: 'rivers', paint: { 'line-color': COL.water, 'line-opacity': ['case', ['==', ['get', 'waterway'], 'river'], 0.75, 0.35], 'line-width': ['case', ['==', ['get', 'waterway'], 'river'], 1.6, 0.7] } });
      }
      if (D.glaciers) {
        map.addSource('glaciers', { type: 'geojson', data: D.glaciers });
        map.addLayer({ id: 'glaciers', type: 'line', source: 'glaciers', paint: { 'line-color': '#cfe6ff', 'line-opacity': 0.28, 'line-width': 0.6 } });
      }
      if (D.observed) {
        map.addSource('observed', { type: 'geojson', data: D.observed });
        map.addLayer({ id: 'obs-flood', type: 'fill', source: 'observed', filter: ['in', ['get', 'kind'], ['literal', ['flood', 'lake']]], paint: { 'fill-color': ['match', ['get', 'kind'], 'lake', COL.water, COL.alarm], 'fill-opacity': 0.35 } });
        map.addLayer({ id: 'obs-road', type: 'line', source: 'observed', filter: ['==', ['get', 'kind'], 'road'], paint: { 'line-color': COL.mark, 'line-width': 2 } });
        map.addLayer({ id: 'obs-bld', type: 'circle', source: 'observed', filter: ['==', ['get', 'kind'], 'building'], paint: { 'circle-color': COL.alarm, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 1.2, 13, 3], 'circle-stroke-color': COL.night, 'circle-stroke-width': 0.5, 'circle-opacity': 0.9 } });
      }
      map.addSource('flood', { type: 'geojson', lineMetrics: true, data: { type: 'Feature', geometry: { type: 'LineString', coordinates: D.replay.path } } });
      map.addLayer({ id: 'flood-glow', type: 'line', source: 'flood', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-width': 16, 'line-blur': 9, 'line-opacity': 0, 'line-gradient': floodGradient(0, true) } });
      map.addLayer({ id: 'flood', type: 'line', source: 'flood', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 3.5, 13, 7], 'line-gradient': floodGradient(0) } });
      map.addSource('blobs', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'blobs-fill', type: 'fill', source: 'blobs', paint: {
        'fill-color': ['case', ['==', ['get', '_rank'], 0], 'rgba(163,179,200,0.10)', ['get', '_top'], ['match', ['get', 'channel'], 'phase', COL.fb, COL.fa], ['match', ['get', 'channel'], 'phase', 'rgba(199,107,242,0.55)', 'rgba(53,208,230,0.55)']],
        'fill-opacity': ['case', ['get', '_top'], 0.6, 0.35] } });
      map.addLayer({ id: 'blobs-line', type: 'line', source: 'blobs', filter: ['>', ['get', '_rank'], 0], paint: {
        'line-color': ['case', ['get', '_top'], COL.ice, 'rgba(233,240,247,0.45)'], 'line-width': ['case', ['get', '_top'], 1.8, 0.7] } });
      map.addLayer({ id: 'blobs-line-dead', type: 'line', source: 'blobs', filter: ['==', ['get', '_rank'], 0], paint: {
        'line-color': 'rgba(163,179,200,0.45)', 'line-width': 0.7, 'line-dasharray': [2, 2] } });
      map.addSource('truth', { type: 'geojson', data: D.truth });
      map.addSource('ring', { type: 'geojson', data: D.truthBuffer || ringAround(D.truthCenter, HIT_KM) });
      map.addLayer({ id: 'ring', type: 'line', source: 'ring', paint: { 'line-color': COL.mark, 'line-width': 1.4, 'line-dasharray': [2, 2.5], 'line-opacity': 0.9 } });
      map.addLayer({ id: 'truth-fill', type: 'fill', source: 'truth', paint: { 'fill-color': COL.mark, 'fill-opacity': 0.18 } });
      map.addLayer({ id: 'truth-line', type: 'line', source: 'truth', paint: { 'line-color': COL.mark, 'line-width': 2.2 } });
      addVillageMarkers();
      var lab = el('div', 'mk-label', 'What fell<small>' + esc(D.truthLabel) + '</small>');
      markers.label = new maplibregl.Marker({ element: lab, anchor: 'bottom', offset: [0, -14] }).setLngLat(D.truthCenter).addTo(map);
      markers.front = new maplibregl.Marker({ element: el('div', 'mk-front') }).setLngLat(D.replay.path[0]).addTo(map);
      markers.front.getElement().style.display = 'none';
      bindMapHover();
      ready.map = true; maybeStart();
    });
  }

  function ringAround(c, km) {
    var pts = [], R = 6371;
    for (var i = 0; i <= 96; i++) {
      var a = i / 96 * 2 * Math.PI;
      var dlat = (km / R) * Math.cos(a) * 180 / Math.PI;
      var dlon = (km / R) * Math.sin(a) * 180 / Math.PI / Math.cos(c[1] * Math.PI / 180);
      pts.push([c[0] + dlon, c[1] + dlat]);
    }
    return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [pts] } };
  }

  function floodGradient(p, glow) {
    var a = glow ? 'rgba(255,97,82,0.55)' : COL.alarm;
    if (p <= 0.0005) return ['interpolate', ['linear'], ['line-progress'], 0, 'rgba(255,97,82,0)', 1, 'rgba(255,97,82,0)'];
    if (p >= 0.999) return ['interpolate', ['linear'], ['line-progress'], 0, a, 1, a];
    var p0 = Math.max(0.0001, p - 0.03);
    return ['interpolate', ['linear'], ['line-progress'], 0, a, p0, a, p, glow ? 'rgba(255,233,226,0.8)' : '#ffe9e2', Math.min(0.9999, p + 0.0005), 'rgba(255,97,82,0)', 1, 'rgba(255,97,82,0)'];
  }

  function addVillageMarkers() {
    markers.villages.forEach(function (m) { m.marker.remove(); });
    markers.villages = (D.replay.villages || []).map(function (v) {
      var e = el('div', 'mk-village', '<i></i><span>' + esc(v.name) + '</span><em></em>');
      var m = new maplibregl.Marker({ element: e, anchor: 'left', offset: [-5, 0] }).setLngLat([v.lon, v.lat]).addTo(map);
      return { v: v, el: e, marker: m };
    });
  }

  function bindMapHover() {
    var tip = $('tip');
    map.on('mousemove', 'blobs-fill', function (e) {
      var f = e.features && e.features[0]; if (!f) return;
      var p = f.properties; map.getCanvas().style.cursor = 'pointer';
      var off = p.channel === 'offsets';
      var mot = off ? (Number(p.motion_m).toFixed(2) + ' m downhill') : (Math.round(p.motion_m * 1000) + ' mm line-of-sight');
      tip.innerHTML = '<b>' + (Number(p._rank) > 0 ? '#' + p._rank + ' · ' : 'Not ranked · ') + (off ? 'Offset blob' : 'Phase blob') + '</b>' +
        '<div class="v">' + mot + ' ± ' + (off ? Number(p.sigma_m).toFixed(2) + ' m' : Math.round(p.sigma_m * 1000) + ' mm') + '</div>' +
        '<div class="k">direction coherence ' + Number(p.dir_coherence).toFixed(2) + ' · signal quality ' + Number(p.coherence).toFixed(2) + '</div>' +
        '<div class="k">' + (p.ice_over_rock === true || p.ice_over_rock === 'true' ? 'hanging ice over rock · ' : '') + (p.opposing_sign === true || p.opposing_sign === 'true' ? 'opposing neighbour · ' : '') +
        Math.round(Number(p.people_below || 0)).toLocaleString() + ' people within 1 km of its first 30 km</div>' +
        '<div class="k">' + Math.round(p.elev_m) + ' m · ' + Number(p.area_km2).toFixed(2) + ' km² · drains to ' + esc(p.channel_name || 'unnamed channel') + '</div>' +
        '<div class="k" style="color:' + COL.mark + '">' + Number(p.dist_truth_km).toFixed(1) + ' km from what fell (used only to score)</div>';
      tip.hidden = false;
      var r = $('map').getBoundingClientRect();
      var x = e.point.x + 14, y = e.point.y + 14;
      if (x + 260 > r.width) x = e.point.x - 264;
      if (y + 150 > r.height) y = e.point.y - 150;
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    });
    map.on('mouseleave', 'blobs-fill', function () { tip.hidden = true; map.getCanvas().style.cursor = ''; });
  }

  function setWindowRaster() {
    if (!map || !map.getSource('win')) return;
    var w = activeWindow();
    var show = S.layer === 'motion' || S.layer === 'phase' || S.layer === 'coh';
    map.setLayoutProperty('win', 'visibility', show ? 'visible' : 'none');
    var radar = S.layer === 'radar';
    map.setLayoutProperty('radarA', 'visibility', radar ? 'visible' : 'none');
    map.setLayoutProperty('radarB', 'visibility', radar ? 'visible' : 'none');
    map.setLayoutProperty('radarC', 'visibility', radar && S.radarMix > 0.5 ? 'visible' : 'none');
    if (radar) map.setPaintProperty('radarC', 'raster-opacity', Math.max(0, (S.radarMix - 0.5) * 2) * 0.95);
    $('radar-compare').hidden = !radar;
    if (radar) map.setPaintProperty('radarB', 'raster-opacity', S.radarMix);
    if (show && w) {
      var name = S.layer + '_' + w.id;
      if (S.layer === 'motion' && !D.hasMotion[w.id]) name = 'phase_' + w.id;
      map.getSource('win').updateImage({ url: DATA + 'img/' + name + '.png', coordinates: D.bounds.box });
    }
    renderLegend();
  }

  function renderLegend() {
    var L = $('legend'), html = '';
    if (S.layer === 'motion') html = '<b>Downhill motion in this window</b> (NISAR GOFF offsets on the fall line)<div class="ramp" style="background:linear-gradient(90deg,#35d0e6,#c76bf2,#f3cf55,#fffae6)"></div><div class="ends"><span>0.15 m</span><span>1 m</span><span>2 m+</span></div>';
    else if (S.layer === 'phase') html = '<b>Relative line-of-sight motion</b> (NISAR GUNW phase, 3–5 km ring removed)<div class="ramp" style="background:linear-gradient(90deg,#4084ff,#ebf0f5,#ff6152)"></div><div class="ends"><span>−60 mm away</span><span>0</span><span>+60 mm towards</span></div>';
    else if (S.layer === 'coh') html = '<b>Interferometric coherence</b> (black = signal lost)<div class="ramp" style="background:linear-gradient(90deg,#000,#e5edf7)"></div><div class="ends"><span>0</span><span>0.5</span><span>1</span></div>';
    else if (S.layer === 'radar') html = '<b>NISAR L-band backscatter, 10 m</b> (GCOV γ⁰ HH). Slide to the after image: <span style="color:#ff7a5a">brighter</span> / <span style="color:#5aa0ff">darker</span> marks where the ground changed between the passes. Radar sees through monsoon cloud.';
    else if (S.mode === 'event' || S.ch === 1 || S.ch === 4) html = '<b>26 August</b> · the wave runs the D8 flow path on the Copernicus DEM through the DHM gauge brackets' +
      '<div class="row"><i style="background:' + COL.alarm + '"></i>Flood front and UNOSAT-mapped flood extent, affected buildings</div>' +
      '<div class="row"><i style="border:2px solid ' + COL.safe + ';border-radius:50%"></i>Warned before the wave <i style="background:' + COL.alarm + ';border-radius:50%;margin-left:8px"></i>Hit</div>';
    else html = '<b>Copernicus DEM</b> terrain, 1.35× vertical';
    if (S.mode === 'event' || S.ch === 1 || S.ch === 4) { L.innerHTML = html; return; }
    html += '<div class="row"><i style="border:2px solid ' + COL.mark + '"></i>What fell (' + esc(D.truthKind) + ') and the 2 km hit circle</div>';
    html += '<div class="row"><i style="background:' + COL.fa + '"></i>Offset blob <i style="background:' + COL.fb + ';margin-left:8px"></i>Phase blob <i style="border:1px dashed ' + COL.mist + ';margin-left:8px"></i>Not ranked</div>';
    L.innerHTML = html;
  }

  /* ---------------------------------------------------------------- blobs on map */
  function paintBlobs() {
    var w = activeWindow(); if (!w || !BLOBS[w.id] || !map || !map.getSource('blobs')) return;
    var fc = BLOBS[w.id];
    cur = rankWindow(fc);
    var N = S.w.nSensors;
    var rk = new Map();
    cur.rows.forEach(function (r) { rk.set((r.f._orig || r.f).properties.id, r.rank || 0); });
    var feats = fc.features.map(function (f) {
      var r = rk.get(f.properties.id) || 0;
      var p = Object.assign({}, f.properties, { _rank: r, _top: r > 0 && r <= N });
      return { type: 'Feature', geometry: f.geometry, properties: p };
    });
    map.getSource('blobs').setData({ type: 'FeatureCollection', features: feats });
    // rank badges for the top N
    var prev = {}; markers.ranks.forEach(function (m) { prev[m.id] = m.rank; m.marker.remove(); });
    markers.ranks = [];
    var showBadges = S.mode !== 'event' && S.ch !== 1;
    if (showBadges) {
      cur.ranked.slice(0, N).forEach(function (r) {
        var p = (r.f._orig || r.f).properties;
        var isSrc = cur.hit && cur.hit === r;
        var e = el('div', 'mk-rank' + (isSrc ? ' src' : '') + (prev[p.id] !== r.rank ? ' pop' : ''), String(r.rank));
        e.title = 'Rank ' + r.rank;
        e.addEventListener('click', function () { map.easeTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 12), duration: 800 }); });
        var m = new maplibregl.Marker({ element: e }).setLngLat([p.lon, p.lat]).addTo(map);
        markers.ranks.push({ id: p.id, rank: r.rank, marker: m });
      });
    }
  }

  /* ---------------------------------------------------------------- flood replay */
  function frontKm(t) {
    var a = D.replay.front; if (!a || !a.length) return 0;
    if (t <= a[0][0]) return a[0][1];
    for (var i = 1; i < a.length; i++) if (t <= a[i][0]) { var k = (t - a[i - 1][0]) / (a[i][0] - a[i - 1][0]); return lerp(a[i - 1][1], a[i][1], k); }
    return a[a.length - 1][1];
  }
  function pointAtKm(km) {
    var P = D.replay.path, K = D.replay.path_km;
    if (km <= 0) return P[0];
    for (var i = 1; i < K.length; i++) if (km <= K[i]) { var k = (km - K[i - 1]) / (K[i] - K[i - 1]); return [lerp(P[i - 1][0], P[i][0], k), lerp(P[i - 1][1], P[i][1], k)]; }
    return P[P.length - 1];
  }
  function tripT() { return S.d.record + S.d.recognise + S.d.confirm + S.d.broadcast; }
  function tripwireArmed() {
    var w = lastPre(); var fc = w && BLOBS[w.id]; if (!fc) return false;
    var r = rankWindow(fc); return !!(r.srcRank && r.srcRank <= S.w.nSensors);
  }
  function paintFlood() {
    if (!map || !map.getLayer('flood')) return;
    var total = D.replay.path_km[D.replay.path_km.length - 1];
    var inEvent = S.mode === 'event' || S.ch === 1 || S.ch === 4;
    var t = S.mode === 'event' ? S.t : (S.ch === 1 ? 999 : 0);
    var km = inEvent ? frontKm(t) : 0;
    var p = Math.min(1, km / total);
    var pp = S.mode === 'event' || S.ch === 1 ? p : 0;
    map.setPaintProperty('flood', 'line-gradient', floodGradient(pp));
    map.setPaintProperty('flood-glow', 'line-gradient', floodGradient(pp, true));
    map.setPaintProperty('flood-glow', 'line-opacity', pp > 0 ? 1 : 0);
    var hideBlobs = S.mode === 'event' || S.ch === 1;
    ['blobs-fill', 'blobs-line', 'blobs-line-dead'].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', hideBlobs ? 'none' : 'visible'); });
    var fe = markers.front.getElement();
    if (S.mode === 'event' && p > 0 && p < 1) { fe.style.display = ''; markers.front.setLngLat(pointAtKm(km)); } else fe.style.display = 'none';
    if (map.getLayer('obs-flood')) {
      var showObs = S.ch === 1 || (S.mode === 'event' && S.t >= TL.tmax - 0.01) || (S.mode === 'window' && (winById(S.win) || {}).phase === 'co');
      ['obs-flood', 'obs-road', 'obs-bld'].forEach(function (id) { map.setLayoutProperty(id, 'visibility', showObs ? 'visible' : 'none'); });
    }
    var A = D.replay.alert_min, T = tripT(), armed = tripwireArmed();
    markers.villages.forEach(function (m) {
      var v = m.v, arr = v.arrival_min;
      var hit = S.mode === 'event' && arr != null && S.t >= arr;
      var warned = S.mode === 'event' && !hit && ((S.t >= A) || (armed && S.t >= T));
      m.el.classList.toggle('hit', !!hit); m.el.classList.toggle('warned', !!warned);
      var em = m.el.querySelector('em');
      if (S.mode === 'event' && arr != null) em.textContent = hit ? ('hit +' + arr.toFixed(0) + ' min') : (warned ? 'warned' : ('+' + arr.toFixed(0) + ' min'));
      else em.textContent = '';
    });
  }

  /* ---------------------------------------------------------------- scorecard */
  function paintScorecard() {
    var w = activeWindow(); if (!w || !cur) return;
    $('sc-win').textContent = (S.track === 'A' ? 'Asc 98 · ' : 'Desc 48 · ') + winLabel(w);
    var n = $('sc-n'), old = n.textContent;
    var r = cur.srcRank, N = S.w.nSensors;
    n.textContent = r ? '#' + r : '—';
    n.className = 'sc-n ' + (r ? (r <= N ? 'in' : 'out') : 'none') + (old !== n.textContent ? ' bump' : '');
    $('sc-of').innerHTML = r ? ('of ' + cur.n + ' ranked slopes<br>is the slope that fell') : (cur.nearCount ? 'source blob present but scored zero' : 'no blob within 2 km of the source');
    var above = $('sc-above'); above.textContent = r ? String(r - 1) : '—';
    var vis = $('sc-vis'); var visible = cur.rows.some(function (x) { return x.p.dist_truth_km <= HIT_KM && x.p.same_channel && x.s > 0; });
    vis.textContent = visible ? 'yes' : (cur.nearCount ? 'below floor' : 'no blob'); vis.className = visible ? 'good' : 'bad';
    var sc = D.scorecard.windows[w.id] || {};
    var base = $('sc-base'); base.textContent = sc.baseline_rank ? ('#' + sc.baseline_rank + ' of ' + sc.n_offsets) : '—';
    var s1 = $('sc-s1'), ctl = D.control;
    if (ctl && ctl.status === 'done' && ctl.sentinel1_rank_per_window) { var v = ctl.sentinel1_rank_per_window[w.id]; s1.textContent = v == null ? 'not ranked' : '#' + v; s1.className = v == null ? 'bad' : ''; }
    else { s1.textContent = 'pending'; s1.className = 'muted'; s1.title = (ctl && ctl.note) || 'Sentinel-1 processing not available yet'; }
    var armed = tripwireArmed(), T = tripT(), A = D.replay.alert_min;
    var trip = $('sc-trip'); trip.textContent = armed ? ('+' + T.toFixed(1) + ' min') : 'not armed'; trip.className = armed ? 'good' : 'muted';
    trip.title = armed ? 'The source valley was in the top ' + N + ' of the last window before the collapse, so a ground sensor was in place.' : 'The source valley was not in the top ' + N + ' of the last window before the collapse, so no sensor was placed.';
    var gain = $('sc-gain');
    if (armed) {
      var vs = (D.replay.villages || []).filter(function (v) { return v.arrival_min != null; });
      var warnedTrip = vs.filter(function (v) { return v.arrival_min > T; }).length, warnedReal = vs.filter(function (v) { return v.arrival_min > A; }).length;
      gain.textContent = '+' + (A - T).toFixed(0) + ' min · ' + warnedTrip + '/' + vs.length; gain.className = 'good';
      gain.title = 'Alert ' + (A - T).toFixed(1) + ' min earlier than the real SMS. Villages warned before the wave: ' + warnedTrip + ' of ' + vs.length + ' (real alert: ' + warnedReal + ').';
    } else { gain.textContent = '0'; gain.className = 'muted'; }
  }

  /* ---------------------------------------------------------------- timeline */
  var TL = { a: 0.70, b: 0.88, tmax: 65 };
  function tlSpan() {
    var pre = preWindows(); var t0 = Date.parse(pre[0].start), t1 = Date.parse('2026-08-26');
    return { t0: t0, t1: t1 };
  }
  function buildTimeline() {
    var segs = $('segs'); segs.innerHTML = ''; segs.style.right = (100 - TL.a * 100) + '%';
    var sp = tlSpan(), ticks = $('ticks'); ticks.innerHTML = '';
    preWindows().forEach(function (w) {
      var x0 = (Date.parse(w.start) - sp.t0) / (sp.t1 - sp.t0), x1 = (Date.parse(w.end) - sp.t0) / (sp.t1 - sp.t0);
      var scw = D.scorecard.windows[w.id] || {};
      var s = el('div', 'seg-w' + (scw.source_rank ? '' : ' lost'));
      s.style.left = (x0 * 100) + '%'; s.style.width = 'calc(' + ((x1 - x0) * 100) + '% - 3px)';
      s.dataset.win = w.id; s.title = winLabel(w) + ' · ' + w.stack_days + ' days · source valley ' + (scw.source_rank ? 'ranked #' + scw.source_rank : 'not ranked (hatched)') + ' · ' + Math.round(w.ice_lost_share * 100) + '% of glacier area lost to low signal';
      segs.appendChild(s);
    });
    ['2026-06-01', '2026-07-01', '2026-08-01'].forEach(function (d) {
      var x = (Date.parse(d) - sp.t0) / (sp.t1 - sp.t0); if (x < 0) return;
      var t = el('span', '', fmtDate(d).replace(/^1 /, '')); t.style.left = (x * TL.a * 100) + '%'; ticks.appendChild(t);
    });
    var ev = $('evzone'); ev.style.left = (TL.a * 100) + '%'; ev.style.width = 'calc(' + ((TL.b - TL.a) * 100) + '% - 6px)'; ev.style.right = 'auto';
    if (!ev.querySelector('.prog')) ev.insertBefore(el('div', 'prog'), ev.firstChild);
    Array.prototype.forEach.call(ev.querySelectorAll('.evt'), function (x) { x.remove(); });
    var seenT = {};
    (D.replay.events || []).concat([{ t_min: D.replay.alert_min, label: 'SMS alert', alert: true }]).forEach(function (e) {
      if (e.t_min < 0 || e.t_min > TL.tmax || seenT[e.t_min.toFixed(1)]) return; seenT[e.t_min.toFixed(1)] = 1;
      var m = el('i', 'evt' + (e.alert ? ' alert' : '')); m.style.left = (e.t_min / TL.tmax * 100) + '%'; m.title = clockAt(e.t_min) + ' · ' + e.label; ev.appendChild(m);
    });
    [0, 30, 60].forEach(function (m) { var t = el('span', m ? 'mt' : '', clockAt(m)); t.style.left = ((TL.a + (TL.b - TL.a) * m / TL.tmax) * 100) + '%'; ticks.appendChild(t); });
    var co = coWindows()[0];
    if (co) {
      var s = el('div', 'seg-w co'); s.style.position = 'absolute'; s.style.top = '8px'; s.style.left = (TL.b * 100) + '%'; s.style.width = ((1 - TL.b) * 100) + '%';
      s.dataset.win = co.id; s.title = 'After: ' + winLabel(co) + ' spans the collapse'; s.id = 'coseg';
      var old = $('coseg'); if (old) old.remove();
      $('track').appendChild(s);
      var t = el('span', '', 'after'); t.style.left = ((TL.b + (1 - TL.b) / 2) * 100) + '%'; ticks.appendChild(t);
    }
  }
  function posOfState() {
    var sp = tlSpan();
    if (S.mode === 'event') return TL.a + (TL.b - TL.a) * Math.min(1, S.t / TL.tmax);
    var w = winById(S.win); if (!w) return 0;
    if (w.phase === 'co') return TL.b + (1 - TL.b) / 2;
    var mid = (Date.parse(w.start) + Date.parse(w.end)) / 2;
    return TL.a * Math.max(0, Math.min(1, (mid - sp.t0) / (sp.t1 - sp.t0)));
  }
  function stateFromPos(x) {
    if (x >= TL.a && x < TL.b) { S.mode = 'event'; S.t = Math.max(0, Math.min(TL.tmax, (x - TL.a) / (TL.b - TL.a) * TL.tmax)); return; }
    if (x >= TL.b) { var co = coWindows()[0]; if (co) { S.mode = 'window'; S.win = co.id; } return; }
    var sp = tlSpan(), t = sp.t0 + (x / TL.a) * (sp.t1 - sp.t0), pick = null;
    preWindows().forEach(function (w) { if (Date.parse(w.start) <= t) pick = w; });
    S.mode = 'window'; S.win = (pick || preWindows()[0]).id;
  }
  function paintTimeline() {
    var x = posOfState(), k = $('knob');
    k.style.left = (x * 100) + '%';
    $('knob-lab').textContent = S.mode === 'event' ? clockAt(S.t) + ' · +' + S.t.toFixed(1) + ' min' : winLabel(winById(S.win));
    Array.prototype.forEach.call(document.querySelectorAll('.seg-w'), function (s) { s.classList.toggle('on', S.mode === 'window' && s.dataset.win === S.win); });
    var prog = document.querySelector('#evzone .prog'); if (prog) prog.style.width = (S.mode === 'event' ? (S.t / TL.tmax * 100) : (posOfState() > TL.b ? 100 : 0)) + '%';
    $('track').setAttribute('aria-valuenow', String(Math.round(x * 1000)));
    $('track').setAttribute('aria-valuetext', $('knob-lab').textContent);
  }
  function bindTimeline() {
    var tr = $('track'), dragging = false;
    function at(ev) { var r = tr.getBoundingClientRect(); return Math.max(0, Math.min(0.9999, (ev.clientX - r.left) / r.width)); }
    tr.addEventListener('pointerdown', function (ev) { dragging = true; tr.setPointerCapture(ev.pointerId); $('knob').classList.add('drag'); stop(); move(ev); });
    tr.addEventListener('pointermove', function (ev) { if (dragging) move(ev); });
    tr.addEventListener('pointerup', function () { dragging = false; $('knob').classList.remove('drag'); });
    function move(ev) { var prevWin = S.win, prevMode = S.mode; stateFromPos(at(ev)); update(prevWin !== S.win || prevMode !== S.mode); }
    tr.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') { ev.preventDefault(); step(ev.key === 'ArrowRight' ? 1 : -1); }
    });
    $('play').addEventListener('click', function () { S.playing ? stop() : play(); });
  }
  function step(dir) {
    var list = preWindows().map(function (w) { return w.id; });
    if (S.mode === 'event') {
      S.t = Math.max(0, Math.min(TL.tmax, S.t + dir * 2));
      if (S.t === 0 && dir < 0) { S.mode = 'window'; S.win = list[list.length - 1]; }
      else if (S.t >= TL.tmax && dir > 0 && coWindows()[0]) { S.mode = 'window'; S.win = coWindows()[0].id; }
    } else {
      var w = winById(S.win);
      if (w && w.phase === 'co') { if (dir < 0) { S.mode = 'event'; S.t = TL.tmax; } }
      else {
        var i = list.indexOf(S.win) + dir;
        if (i >= list.length) { S.mode = 'event'; S.t = 0; } else if (i >= 0) S.win = list[i];
      }
    }
    update(true);
  }
  var playTimer = null, lastNow = 0;
  function play() {
    S.playing = true; $('play-icon').setAttribute('d', 'M7 5h4v14H7zM13 5h4v14h-4z'); $('play').setAttribute('aria-label', 'Pause');
    if (S.ch === 4 || S.ch === 1) { if (S.mode !== 'event' || S.t >= TL.tmax - 0.1) { S.mode = 'event'; S.t = 0; } }
    else if (S.mode !== 'window' || !winById(S.win) || winById(S.win).phase !== 'pre') { S.mode = 'window'; S.win = preWindows()[0].id; }
    else if (S.win === lastPre().id) { S.win = preWindows()[0].id; }
    update(true);
    lastNow = performance.now();
    var acc = 0;
    function tick(now) {
      if (!S.playing) return;
      var dt = Math.min(80, now - lastNow); lastNow = now;
      if (S.mode === 'event') {
        S.t += dt / 1000 * (TL.tmax / 22);
        if (S.t >= TL.tmax) { S.t = TL.tmax; update(false); stop(); return; }
        update(false);
      } else {
        acc += dt;
        if (acc > 1500) { acc = 0; var list = preWindows(), i = list.findIndex(function (w) { return w.id === S.win; });
          if (i < list.length - 1) { S.win = list[i + 1].id; update(true); } else { stop(); return; } }
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }
  function stop() { S.playing = false; $('play-icon').setAttribute('d', 'M8 5v14l11-7z'); $('play').setAttribute('aria-label', 'Play the replay'); }

  /* ---------------------------------------------------------------- update loop */
  var lastWinPainted = null;
  function update(windowChanged) {
    var w = activeWindow();
    if (!w) return;
    loadBlobs(w.id).then(function () {
      if (windowChanged && w.id !== lastWinPainted) {
        lastWinPainted = w.id; setWindowRaster();
        if (!reduce) { var sw = $('sweep'); sw.classList.remove('go'); void sw.offsetWidth; sw.classList.add('go'); }
      }
      paintBlobs(); paintFlood(); paintScorecard(); paintTimeline(); paintChapterLive(); renderLegend();
    });
  }

  /* ---------------------------------------------------------------- chapters */
  var NAMES = { 1: 'The morning', 2: 'The creep', 3: 'The haystack', 4: 'The race', 5: 'The answer' };
  var CAM = {
    1: { center: [85.36, 28.13], zoom: 9.75, pitch: 52, bearing: -18 },
    2: { center: [85.505, 28.292], zoom: 11.9, pitch: 62, bearing: 168 },
    3: { center: [85.52, 28.29], zoom: 10.55, pitch: 38, bearing: -6 },
    4: { center: [85.33, 28.14], zoom: 10.0, pitch: 56, bearing: -24 },
    5: { center: [85.48, 28.27], zoom: 10.7, pitch: 46, bearing: -10 }
  };
  function go(ch, fly) {
    S.ch = ch; stop();
    var navs = $('nav').children; for (var i = 0; i < navs.length; i++) navs[i].setAttribute('aria-current', String(+navs[i].dataset.ch === ch));
    $('prev').hidden = ch === 1;
    $('next').textContent = ch < 5 ? 'Next: ' + NAMES[ch + 1].toLowerCase() : 'Back to the start';
    if (ch === 1) { S.mode = 'event'; S.t = 0; }
    if (ch === 2 || ch === 3 || ch === 5) { S.mode = 'window'; S.win = lastPre().id; }
    if (ch === 4) { S.mode = 'event'; S.t = 0; }
    if (ch === 1 || ch === 4) setLayer('none'); else if (S.layer === 'none' || S.layer === 'radar') setLayer('motion');
    renderChapter();
    if (map && fly !== false) map.flyTo(Object.assign({ duration: reduce ? 0 : 2200, essential: true, curve: 1.3 }, CAM[ch]));
    update(true);
    $('chapbody').scrollTop = 0;
  }
  function setLayer(l) {
    S.layer = l;
    Array.prototype.forEach.call(document.querySelectorAll('.layerbar button'), function (b) { b.setAttribute('aria-pressed', String(b.dataset.layer === l)); });
    setWindowRaster();
  }

  function factRow(key, cls, unit) {
    var f = fact(key); if (!f) return '';
    return '<div class="' + (cls || '') + '"><dt>' + esc(f.display || f.value) + (unit || f.unit ? ' <small>' + esc(unit || f.unit) + '</small>' : '') + '</dt><dd>' + esc(f.label) + cite(f.source) + '</dd></div>';
  }

  function renderChapter() {
    var body = $('chapbody'), lp = lastPre(), sc = D.scorecard, html = '';
    var A = D.replay.alert_min;
    if (S.ch === 1) {
      html = '<div class="chap"><p class="eyebrow">26 August 2026 · Rasuwa, Nepal</p>' +
        '<h2>The alert came ' + A.toFixed(1) + ' minutes after the mountain fell</h2>' +
        '<p>' + esc(D.context.story.morning) + '</p>' +
        '<dl class="facts">' + (D.context.facts_order || []).map(function (k) { return factRow(k, (fact(k) || {}).tone); }).join('') + '</dl>' +
        '<p>' + esc(D.context.story.morning2) + '</p>' +
        '<p class="eyebrow">The question this replay answers</p>' +
        '<p class="question">With rules fixed in advance, would NISAR have put this valley on a watch list a week before it fell?</p>' +
        '<p class="note">Press <b>play</b> below to run the wave down the real flow path at the observed timings.</p></div>';
    } else if (S.ch === 2) {
      html = '<div class="chap"><p class="eyebrow">' + fmtDate(preWindows()[0].start) + ' to ' + fmtDate(lp.end) + ' · NISAR ' + (S.track === 'A' ? 'ascending track 98' : 'descending track 48') + '</p>' +
        '<h2>Radar watched the slope through the monsoon</h2>' +
        '<p>NISAR imaged the valley every 12 days. Each pair of passes gives two independent motion maps: <strong>pixel offsets</strong> (metre-scale motion of the moving mass) and <strong>phase</strong> (millimetre-scale motion at its edges).</p>' +
        '<div class="creep" id="creep"></div>' +
        '<p id="creep-note"></p>' +
        '<div class="row"><span class="eyebrow">Radar band</span><div class="seg" id="bands"><button type="button" data-band="L" aria-pressed="true">L-band · NISAR</button><button type="button" data-band="C" aria-pressed="false">C-band · Sentinel-1</button></div></div>' +
        '<p class="note" id="bandnote"></p>' +
        '<p class="note">NISAR GOFF offsets projected on the fall line, inside the UNOSAT detachment zone, per pass. Hatched: the source valley was not ranked that pass. Click a bar to load that pass on the map.</p></div>';
    } else if (S.ch === 3) {
      html = '<div class="chap"><p class="eyebrow">The ranking problem · ' + winLabel(activeWindow()) + '</p>' +
        '<h2>One moving slope among many</h2>' +
        '<p>Glaciers flow every day, so moving ground is normal here. The rules below were fixed in a config file and committed before the first real run. Switch them off and on to see what each one does.</p>' +
        '<div class="rankbox"><div class="rank" id="rank"><span class="n" id="rank-n"></span><span class="of" id="rank-of"></span></div>' +
        '<div class="strip"><div class="cut" id="cut"></div><div class="pin" id="pin"><span id="pinlab"></span></div></div><div class="ends"><span>#1 most suspicious</span><span id="strip-n"></span></div></div>' +
        '<div class="rules">' +
        rule('sigma', 'Trust clean signals', 'Divide motion by its measured uncertainty, so noisy patches drop.') +
        rule('dir', 'Moving one way', 'Reward offsets that point the same way across the patch.') +
        rule('opp', 'Neighbours pulling apart', 'One block moves towards the radar while the next moves away (Kutoğlu 2026).') +
        rule('ice', 'Hanging ice over rock', 'Glacier ice on slopes steeper than 30°, the terrain that fails as ice-rock avalanches.') +
        rule('people', 'People in the channel below', 'WorldPop residents within 1 km of the first 30 km downstream.') +
        rule('season', 'Hot season', 'ERA5 temperature anomaly since 1 June at the source (same for every slope in a window).') +
        '</div>' +
        '<label class="sl" for="nsens"><span>Slopes we can afford to instrument</span><input type="range" id="nsens" min="1" max="15" step="1"><output id="o-n"></output></label>' +
        '<div class="verdict" id="verdict"></div>' +
        '<div class="list" id="list"></div>' +
        '<details class="more"><summary>Tune the weights</summary><div style="display:grid;gap:10px">' +
        slider('coh', 'Coherence floor', 0, 0.8, 0.01, 'coherenceFloor') + slider('msig', 'Extra motion filter (×σ)', 0, 15, 0.5, 'motionSigma') +
        slider('ob', 'Opposing-neighbour bonus', 1, 3, 0.05, 'opposingBonus') + slider('ib', 'Ice-over-rock bonus', 1, 3, 0.05, 'iceBonus') +
        slider('wp', 'People weight', 0, 1, 0.01, 'wPop') + slider('ws', 'Season weight', 0, 2, 0.05, 'wSeason') +
        '<button class="btn ghost" type="button" id="reset">Reset to the pre-registered values</button></div></details>' +
        '<p class="note">Truth (' + esc(D.truthKind) + ') is used only to find where the real source landed in the ranking, never to rank.</p></div>';
    } else if (S.ch === 4) {
      html = '<div class="chap"><p class="eyebrow">26 August, 08:37 onward</p>' +
        '<h2>The wave against the warning</h2>' +
        '<p>Slow radar picks the valley; a fast ground sensor on that slope does the rest. The tripwire only exists if the valley made the watch list in the last pass before the collapse. Press play.</p>' +
        '<div class="verdict" id="tripnote"></div>' +
        '<div class="racechart" id="race"></div>' +
        '<div style="overflow-x:auto"><table id="vtable"></table></div>' +
        '<p class="eyebrow">Tripwire delays, in minutes</p>' +
        delay('record', 'Sensor records the fall', 0, 5, 0.5) + delay('recognise', 'Recognise a collapse, not a quake', 0, 20, 0.5) +
        delay('confirm', 'Confirm it can flood', 0, 15, 0.5) + delay('broadcast', 'Sound the sirens', 0, 15, 0.5) +
        '<p class="eyebrow">What happened, minute by minute</p><div class="log" id="log"></div>' +
        '<p class="note" id="race-note"></p></div>';
    } else if (S.ch === 5) {
      var pass = sc.pass || {};
      var ranks = (pass.ranks || []).map(function (r, i) { return (r ? '#' + r : 'not ranked') + ' (' + winLabel(winById(pass.windows[i]) || { start: '', end: '' }) + ')'; }).join(' and ');
      html = '<div class="chap"><p class="eyebrow">Read off the real data</p>' +
        '<h2>What the replay says</h2>' +
        '<p class="answer" id="answer"></p>' +
        '<div><span class="pf ' + (pass.passed ? 'pass' : 'fail') + '">Pre-registered test: ' + (pass.passed ? 'passed' : 'not passed') + '</span></div>' +
        '<p>' + esc(pass.rule || '') + '. Result: ' + esc(ranks) + '.</p>' +
        '<div class="kv" id="answer-kv"></div>' +
        '<div class="prov"><h3>The season was primed</h3><div class="creep" id="season"></div><p class="note" id="season-note"></p></div>' +
        '<div class="prov"><h3>Limits, said plainly</h3><ul id="limits"></ul></div>' +
        '<div class="prov"><h3>What is real here</h3><ul id="realist"></ul></div>' +
        '<p class="note">Config SHA-256 <code style="font-family:var(--mono)">' + esc((sc.config_sha256 || '').slice(0, 12)) + '</code> · commit history shows the rules were fixed before the first run.</p></div>';
    }
    body.innerHTML = html;
    bindChapter();
  }
  function rule(k, t, d) { return '<label class="rule" for="r-' + k + '"><span><b>' + t + '</b><small>' + d + '</small></span><input type="checkbox" class="sw" id="r-' + k + '"' + (S.rules[k] ? ' checked' : '') + '></label>'; }
  function slider(id, label, mn, mx, st, key) { return '<label class="sl" for="w-' + id + '"><span>' + label + '</span><input type="range" id="w-' + id + '" data-key="' + key + '" min="' + mn + '" max="' + mx + '" step="' + st + '" value="' + S.w[key] + '"><output></output></label>'; }
  function delay(k, label, mn, mx, st) { return '<label class="sl" for="d-' + k + '"><span>' + label + '</span><input type="range" id="d-' + k + '" data-key="' + k + '" min="' + mn + '" max="' + mx + '" step="' + st + '" value="' + S.d[k] + '"><output></output></label>'; }

  function bindChapter() {
    if (S.ch === 2) {
      Array.prototype.forEach.call(document.querySelectorAll('#bands button'), function (b) {
        b.addEventListener('click', function () { Array.prototype.forEach.call(document.querySelectorAll('#bands button'), function (x) { x.setAttribute('aria-pressed', String(x === b)); }); paintBandNote(b.dataset.band); });
      });
      paintBandNote('L');
    }
    if (S.ch === 3) {
      ['sigma', 'dir', 'opp', 'ice', 'people', 'season'].forEach(function (k) { $('r-' + k).addEventListener('change', function (e) { S.rules[k] = e.target.checked; update(false); }); });
      var ns = $('nsens'); ns.value = S.w.nSensors; ns.addEventListener('input', function () { S.w.nSensors = +ns.value; update(false); });
      Array.prototype.forEach.call(document.querySelectorAll('input[data-key]'), function (inp) {
        inp.nextElementSibling.textContent = (+inp.value).toFixed(2);
        inp.addEventListener('input', function () { S.w[inp.dataset.key] = +inp.value; inp.nextElementSibling.textContent = (+inp.value).toFixed(2); update(false); });
      });
      $('reset').addEventListener('click', function () {
        var n = S.w.nSensors; S.w = Object.assign({}, CWScore.DEFAULTS, { nSensors: n });
        Object.keys(S.rules).forEach(function (k) { S.rules[k] = true; }); renderChapter(); update(false);
      });
    }
    if (S.ch === 4) {
      Array.prototype.forEach.call(document.querySelectorAll('input[data-key]'), function (inp) {
        inp.nextElementSibling.textContent = (+inp.value).toFixed(1);
        inp.addEventListener('input', function () { S.d[inp.dataset.key] = +inp.value; inp.nextElementSibling.textContent = (+inp.value).toFixed(1); update(false); });
      });
      var log = $('log');
      (D.replay.events || []).forEach(function (e) { var d = el('div', '', '<i>' + esc(e.npt || clockAt(e.t_min)) + '</i><span>' + esc(e.label) + cite(e.source) + '</span>'); d.dataset.t = e.t_min; log.appendChild(d); });
      $('race-note').innerHTML = esc(D.replay.note || '');
    }
  }

  function paintBandNote(band) {
    var n = $('bandnote'); if (!n) return;
    var ctl = D.control;
    if (band === 'C') n.innerHTML = (ctl && ctl.status === 'done') ? esc(ctl.summary) : esc((ctl && ctl.note) || 'The Sentinel-1 control is pending.') + (D.context.sah ? ' ' + esc(D.context.sah.text) + cite(D.context.sah.source) : '');
    else n.innerHTML = 'L-band (24 cm wavelength) keeps its lock on ice and wet snow far better than C-band (5.6 cm), which is why NISAR can follow this slope at all.';
  }

  function paintChapterLive() {
    if (S.ch === 2) paintCreep();
    if (S.ch === 3) paintRanking();
    if (S.ch === 4) paintRace();
    if (S.ch === 5) paintAnswer();
  }

  function paintCreep() {
    var box = $('creep'); if (!box) return;
    var rows = (D.series || []).filter(function (r) { return r.direction === S.track && r.end <= '2026-08-26' && r.start >= '2026-05-01'; });
    var meena = {}; (D.context.meena || []).forEach(function (m) { meena[m.id] = m.disp_m; });
    var W = 380, H = 150, padB = 30, n = rows.length, bw = W / Math.max(n, 1);
    var vals = rows.map(function (r) { return Math.max(r.offsets_p90_m || 0, meena[r.id] || 0); });
    var vmax = Math.max(0.4, Math.max.apply(null, vals)) * 1.18;
    var Y = function (v) { return H - Math.max(0, v) / vmax * (H - 16); };
    var svg = '<svg viewBox="0 0 ' + W + ' ' + (H + padB) + '" role="img" aria-label="Downhill motion of the fastest tenth of the detachment zone per NISAR pass">';
    svg += '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="6" fill="rgba(163,179,200,.25)"/></pattern></defs>';
    rows.forEach(function (r, i) {
      var v = r.offsets_p90_m, med = r.offsets_median_m, x = i * bw + 4, w = bw - 8;
      var lost = !(D.scorecard.windows[r.id] || {}).source_rank;
      var on = S.mode === 'window' && S.win === r.id;
      var y = v == null ? H : Y(v);
      svg += '<g class="bar" data-win="' + r.id + '"><title>' + winLabel(r) + ': fastest tenth ' + (v == null ? '—' : v.toFixed(2) + ' m') + ', median ' + (med == null ? '—' : med.toFixed(2) + ' m') + (meena[r.id] != null ? ', Meena ' + meena[r.id].toFixed(2) + ' m' : '') + (lost ? ' · source valley not ranked this pass' : '') + '</title>';
      svg += '<rect x="' + x + '" y="0" width="' + w + '" height="' + H + '" fill="transparent"/>';
      svg += '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + (H - y) + '" rx="3" fill="' + (on ? COL.mark : COL.fa) + '" opacity="' + (lost ? 0.35 : 0.9) + '"/>';
      if (lost) svg += '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + (H - y) + '" rx="3" fill="url(#hatch)" stroke="' + COL.dim + '" stroke-dasharray="3 2"/>';
      if (med != null && med > 0) svg += '<line x1="' + (x + 3) + '" x2="' + (x + w - 3) + '" y1="' + Y(med) + '" y2="' + Y(med) + '" stroke="' + COL.night + '" stroke-width="2"/>';
      if (meena[r.id] != null) { var my = Y(meena[r.id]), cx = x + w / 2; svg += '<path d="M' + cx + ' ' + (my - 6) + 'L' + (cx + 6) + ' ' + my + 'L' + cx + ' ' + (my + 6) + 'L' + (cx - 6) + ' ' + my + 'Z" fill="' + COL.ice + '" stroke="' + COL.night + '" stroke-width="1.5"/>'; }
      svg += '<text class="val' + (on ? ' cur' : '') + '" x="' + (x + w / 2) + '" y="' + (y - 9) + '" text-anchor="middle">' + (v == null ? '—' : v.toFixed(2)) + '</text>';
      svg += '<text x="' + (x + w / 2) + '" y="' + (H + 14) + '" text-anchor="middle">' + fmtDate(r.start) + '</text><text x="' + (x + w / 2) + '" y="' + (H + 26) + '" text-anchor="middle">' + r.days + ' d</text></g>';
    });
    svg += '<line x1="0" x2="' + W + '" y1="' + H + '" y2="' + H + '" stroke="' + COL.rule + '"/></svg>';
    box.innerHTML = svg + '<div class="row note" style="gap:14px"><span><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + COL.fa + ';vertical-align:-1px"></i> fastest tenth of the UNOSAT zone, m downhill</span><span>— zone median</span><span>◆ Meena 2026</span></div>';
    Array.prototype.forEach.call(box.querySelectorAll('.bar'), function (g) { g.addEventListener('click', function () { S.mode = 'window'; S.win = g.dataset.win; update(true); }); });
    var note = $('creep-note');
    if (note) {
      if (S.track === 'A') note.innerHTML = esc(D.context.story.creep || '') + cite('meena');
      else {
        var lostN = rows.filter(function (r) { return !(D.scorecard.windows[r.id] || {}).source_rank; }).length;
        note.innerHTML = 'Descending track 48 sees the slope from the other side. ' + lostN + ' of its ' + rows.length + ' pre-collapse passes lost the source valley to low signal; in the last one (' + (rows.length ? winLabel(rows[rows.length - 1]) : '') + ') it ranked #' + ((D.scorecard.windows[(rows[rows.length - 1] || {}).id] || {}).source_rank || '—') + '.';
      }
    }
  }

  function paintRanking() {
    if (!$('rank-n') || !cur) return;
    var r = cur.srcRank, n = cur.n, N = S.w.nSensors;
    var rn = $('rank-n'); var from = parseInt((rn.textContent || '').replace('#', ''), 10);
    if (r && isFinite(from) && from !== r) tween(from, r, 450, function (v) { rn.textContent = '#' + Math.round(v); }); else rn.textContent = r ? '#' + r : '—';
    $('rank-of').textContent = r ? 'of ' + n + ' ranked slopes in ' + winLabel(activeWindow()) : (cur.nearCount ? 'The source blob scores zero under these settings' : 'No blob within 2 km of the source in this window');
    $('rank').className = 'rank ' + (r ? (r <= N ? 'in' : 'out') : 'none');
    $('strip-n').textContent = '#' + n;
    $('cut').style.width = Math.min(100, N / Math.max(1, n) * 100) + '%';
    var pin = $('pin'), pos = r ? (n > 1 ? (r - 1) / (n - 1) * 100 : 0) : 100;
    pin.style.left = pos + '%'; pin.style.opacity = r ? 1 : 0.25; pin.className = 'pin' + (pos < 14 ? ' l' : pos > 86 ? ' r' : '');
    $('pinlab').textContent = r ? 'the slope that fell, #' + r : 'not ranked';
    $('o-n').textContent = N;
    var v = $('verdict');
    if (!r) { v.className = 'verdict out'; v.textContent = 'With these settings the slope that fell is not on the list at all.'; }
    else if (r <= N) { v.className = 'verdict in'; v.textContent = 'The top ' + N + ' slopes get a ground sensor. The slope above the Lhende Khola is one of them' + (r > 1 ? ', with ' + (r - 1) + ' false alarm' + (r > 2 ? 's' : '') + ' ranked above it.' : ', ranked first.'); }
    else { v.className = 'verdict out'; v.textContent = 'The top ' + N + ' slopes get a ground sensor. The slope that fell is not one of them: ' + (r - 1) + ' slopes rank above it.'; }
    // list with FLIP
    var list = $('list'); var before = {};
    Array.prototype.forEach.call(list.children, function (c) { before[c.dataset.id] = c.getBoundingClientRect().top; });
    list.innerHTML = '';
    var top = cur.ranked.slice(0, 12), smax = top.length ? top[0].s : 1;
    var srcRow = cur.hit && cur.ranked.indexOf(cur.hit) >= 12 ? cur.hit : null;
    top.concat(srcRow ? [srcRow] : []).forEach(function (row) {
      var p = (row.f._orig || row.f).properties, isSrc = cur.hit === row;
      var d = el('div', 'li' + (isSrc ? ' src' : '') + (row.rank > N ? ' dead' : ''));
      d.dataset.id = p.id;
      var off = p.channel === 'offsets';
      d.innerHTML = '<span class="r">#' + row.rank + '</span><span>' + (isSrc ? 'The slope that fell' : (p.channel_name ? esc(p.channel_name) : 'Slope') + ', ' + Math.round(p.elev_m) + ' m') +
        '<span class="chip ' + (off ? 'off' : 'ph') + '">' + (off ? 'offsets' : 'phase') + '</span></span>' +
        '<span><span class="bar" style="display:block;width:' + Math.max(4, row.s / smax * 100) + '%"></span></span><span class="d">' + Number(p.dist_truth_km).toFixed(1) + ' km</span>';
      d.addEventListener('click', function () { map.easeTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 12.2), pitch: 55, duration: 900 }); });
      list.appendChild(d);
    });
    if (!reduce) Array.prototype.forEach.call(list.children, function (c) {
      var b = before[c.dataset.id]; if (b == null) return;
      var dy = b - c.getBoundingClientRect().top; if (!dy) return;
      c.style.transform = 'translateY(' + dy + 'px)'; c.style.transition = 'none';
      requestAnimationFrame(function () { c.style.transition = 'transform .45s cubic-bezier(.2,.8,.2,1)'; c.style.transform = ''; });
    });
  }

  function paintRace() {
    var tn = $('tripnote'); if (!tn) return;
    var A = D.replay.alert_min, T = tripT(), armed = tripwireArmed(), N = S.w.nSensors;
    var lw = lastPre(), r = rankWindow(BLOBS[lw.id]);
    if (armed) { tn.className = 'verdict in'; tn.innerHTML = 'In the last pass before the collapse the source valley ranked <strong>#' + r.srcRank + '</strong>, inside the top ' + N + '. A sensor is on that slope, and its siren sounds <strong>' + T.toFixed(1) + ' min</strong> after the fall.'; }
    else { tn.className = 'verdict out'; tn.innerHTML = 'In the last pass before the collapse the source valley ranked <strong>' + (r.srcRank ? '#' + r.srcRank : 'nowhere') + '</strong>, outside the top ' + N + '. No sensor, so no tripwire: only the real SMS at ' + A.toFixed(1) + ' min.'; }
    var vs = (D.replay.villages || []).filter(function (v) { return v.arrival_min != null; });
    var tb = '<thead><tr><th>Place</th><th>Wave</th><th>Real alert</th><th>Tripwire</th><th>Source</th></tr></thead><tbody>';
    vs.forEach(function (v) {
      var real = v.arrival_min - A, trip = armed ? v.arrival_min - T : null, hit = S.mode === 'event' && S.t >= v.arrival_min;
      tb += '<tr class="' + (hit ? 'hit' : '') + '"><td>' + esc(v.name) + '</td><td>+' + v.arrival_min.toFixed(1) + '</td>' +
        '<td class="' + (real < 0 ? 'late' : 'gain') + '">' + signed(real, 0) + ' min</td>' +
        '<td class="' + (trip == null ? 'none' : trip < 0 ? 'late' : 'gain') + '">' + (trip == null ? '—' : signed(trip, 0) + ' min') + '</td>' +
        '<td class="none">' + esc(v.arrival_kind || '') + '</td></tr>';
    });
    $('vtable').innerHTML = tb + '</tbody>';
    // lanes chart
    var W = 380, rowH = 22, H = vs.length * rowH + 34, x0 = 92, x1 = W - 8, tmax = TL.tmax;
    var X = function (t) { return x0 + Math.min(1, t / tmax) * (x1 - x0); };
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Wave arrival against alert times per place">';
    vs.forEach(function (v, i) {
      var y = 10 + i * rowH;
      svg += '<text x="0" y="' + (y + 11) + '" fill="' + COL.mist + '" style="font-family:var(--body);font-size:11px">' + esc(v.name) + '</text>';
      svg += '<line x1="' + x0 + '" x2="' + x1 + '" y1="' + (y + 7) + '" y2="' + (y + 7) + '" stroke="' + COL.rule + '"/>';
      var hit = S.mode === 'event' && S.t >= v.arrival_min;
      svg += '<circle cx="' + X(v.arrival_min) + '" cy="' + (y + 7) + '" r="5" fill="' + (hit ? COL.alarm : COL.night) + '" stroke="' + COL.alarm + '" stroke-width="2"/>';
    });
    var yb = 10 + vs.length * rowH;
    svg += '<line x1="' + X(A) + '" x2="' + X(A) + '" y1="4" y2="' + yb + '" stroke="' + COL.ice + '" stroke-width="1.5"/><text x="' + (X(A) + 3) + '" y="' + (yb + 12) + '" fill="' + COL.ice + '">SMS ' + A.toFixed(1) + '</text>';
    if (armed) svg += '<line x1="' + X(T) + '" x2="' + X(T) + '" y1="4" y2="' + yb + '" stroke="' + COL.safe + '" stroke-width="2"/><text x="' + (X(T) + 3) + '" y="' + (yb + 24) + '" fill="' + COL.safe + '">tripwire ' + T.toFixed(1) + '</text>';
    if (S.mode === 'event') svg += '<line x1="' + X(S.t) + '" x2="' + X(S.t) + '" y1="0" y2="' + yb + '" stroke="' + COL.alarm + '" stroke-opacity=".6" stroke-dasharray="2 3"/>';
    svg += '<text x="' + x0 + '" y="' + (H - 1) + '">0</text><text x="' + X(30) + '" y="' + (H - 1) + '" text-anchor="middle">30 min</text><text x="' + x1 + '" y="' + (H - 1) + '" text-anchor="end">' + tmax + '</text></svg>';
    $('race').innerHTML = svg;
    Array.prototype.forEach.call(document.querySelectorAll('#log div'), function (d) { d.className = (S.mode === 'event' && S.t >= +d.dataset.t) ? 'on' : ''; });
  }

  function paintAnswer() {
    var a = $('answer'); if (!a) return;
    var sc = D.scorecard, lp = lastPre(), w = sc.windows[lp.id] || {};
    var N = S.w.nSensors;
    var r = w.source_rank;
    a.innerHTML = r ? ('In the last NISAR pass before the collapse (' + winLabel(lp) + '), the slope that fell ranked <em>#' + r + ' of ' + w.n_ranked + '</em> moving slopes in the box, with ' + (r - 1) + ' ranked above it.')
      : ('In the last NISAR pass before the collapse (' + winLabel(lp) + '), the pre-registered screen did <em>not</em> put the slope that fell on the list.');
    var kv = [
      ['Source rank, last pass (rules on)', r ? '#' + r + ' / ' + w.n_ranked : '—'],
      ['Raw-offset baseline (Meena-style)', w.baseline_rank ? '#' + w.baseline_rank + ' / ' + w.n_offsets : '—'],
      ['Sentinel-1 C-band control', D.control && D.control.status === 'done' ? (D.control.sentinel1_rank_per_window[lp.id] == null ? 'not ranked' : '#' + D.control.sentinel1_rank_per_window[lp.id]) : 'pending'],
      ['Same, with troposphere screens removed', (function () { var x = D.context.sensitivity && D.context.sensitivity.windows[lp.id]; return x && x.tropo_rank ? '#' + x.tropo_rank + ' / ' + x.tropo_n : '—'; })()],
      ['Real alert after the collapse', D.replay.alert_min.toFixed(1) + ' min'],
      ['Data on ASF before the collapse', lp.asf_processed ? fmtDate(lp.asf_processed.slice(0, 10), true) : '—']
    ];
    $('answer-kv').innerHTML = kv.map(function (k) { return '<div><span>' + esc(k[0]) + '</span><b>' + esc(k[1]) + '</b></div>'; }).join('');
    var e5 = D.context.era5, sb = $('season');
    if (e5 && sb) {
      var ys = e5.years.filter(function (y) { return y[1] != null; }), W = 380, H = 70;
      var lo = Math.min.apply(null, ys.map(function (y) { return y[1]; })) - 0.3, hi = Math.max.apply(null, ys.map(function (y) { return y[1]; })) + 0.1, bw = W / ys.length;
      var svg = '<svg viewBox="0 0 ' + W + ' ' + (H + 14) + '" role="img" aria-label="Mean temperature 1 July to 25 August at the source, 1991 to 2026">';
      ys.forEach(function (y, i) {
        var h = (y[1] - lo) / (hi - lo) * H, is26 = y[0] === 2026;
        svg += '<rect x="' + (i * bw + 1) + '" y="' + (H - h) + '" width="' + (bw - 2) + '" height="' + h + '" rx="1.5" fill="' + (is26 ? COL.alarm : 'rgba(163,179,200,.35)') + '"><title>' + y[0] + ': ' + y[1].toFixed(2) + ' °C</title></rect>';
      });
      svg += '<text x="0" y="' + (H + 12) + '">1991</text><text x="' + W + '" y="' + (H + 12) + '" text-anchor="end" fill="' + COL.alarm + '">2026</text></svg>';
      sb.innerHTML = svg;
      $('season-note').innerHTML = esc(e5.span + ' at the source: ' + e5.t2026.toFixed(1) + ' °C, ' + signed(e5.anom, 1) + ' °C above 1991–2020 (z ' + signed(e5.z, 1) + '), the ' + (e5.rank === 2 ? '2nd' : e5.rank + 'th') + ' warmest of ' + e5.n + ' years, with ' + e5.snow_pct + '% of normal snowfall.') + cite('era5') + ' Same heat and snow drought as ' + cite('hassan').trim() + '.';
    }
    $('limits').innerHTML = (D.context.limits || []).map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('');
    $('realist').innerHTML = (D.context.real || []).map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('');
  }

  /* ---------------------------------------------------------------- sheets */
  function openSheet(which) {
    var dlg = $('sheet-' + which);
    var c = which === 'method' ? D.context.method_html : D.context.sources_html;
    dlg.innerHTML = '<div class="in"><button class="x" type="button" aria-label="Close">Close</button>' + c + '</div>';
    dlg.querySelector('.x').addEventListener('click', function () { dlg.close(); });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
    dlg.showModal();
  }

  /* ---------------------------------------------------------------- boot */
  var ready = { data: false, map: false };
  function maybeStart() {
    if (!ready.data || !ready.map) return;
    S.win = lastPre().id;
    buildTimeline();
    go(1, false);
  }
  function bindChrome() {
    $('nav').addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) go(+b.dataset.ch); });
    $('prev').addEventListener('click', function () { go(Math.max(1, S.ch - 1)); });
    $('next').addEventListener('click', function () { go(S.ch < 5 ? S.ch + 1 : 1); });
    Array.prototype.forEach.call(document.querySelectorAll('.layerbar button'), function (b) { b.addEventListener('click', function () { setLayer(b.dataset.layer); }); });
    Array.prototype.forEach.call(document.querySelectorAll('.trackpick button'), function (b) {
      b.addEventListener('click', function () {
        S.track = b.dataset.track;
        Array.prototype.forEach.call(document.querySelectorAll('.trackpick button'), function (x) { x.setAttribute('aria-pressed', String(x === b)); });
        if (S.mode === 'window') S.win = lastPre().id;
        buildTimeline(); renderChapter(); update(true);
      });
    });
    $('rc-slider').addEventListener('input', function (e) { S.radarMix = +e.target.value; setWindowRaster(); });
    Array.prototype.forEach.call(document.querySelectorAll('[data-open]'), function (b) { b.addEventListener('click', function () { openSheet(b.dataset.open); }); });
    document.addEventListener('keydown', function (e) {
      if (e.target.closest('input,textarea,select,dialog')) return;
      if (e.key >= '1' && e.key <= '5') go(+e.key);
      else if (e.key === ' ') { e.preventDefault(); S.playing ? stop() : play(); }
      else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { if (document.activeElement === $('track')) return; e.preventDefault(); step(e.key === 'ArrowRight' ? 1 : -1); }
    });
  }

  Promise.all([
    fetchJSON('windows.json'), fetchJSON('scorecard.json'), fetchJSON('truth.geojson'), fetchJSON('replay.json'),
    fetchJSON('control.json', true), fetchJSON('source_series.json', true), fetchJSON('context.json'), fetchJSON('img/bounds.json'),
    fetchJSON('rivers.geojson', true), fetchJSON('glaciers.geojson', true), fetchJSON('observed.geojson', true)
  ]).then(function (r) {
    D.observed = r[10];
    D.windows = r[0]; D.scorecard = r[1]; D.truth = r[2]; D.replay = r[3]; D.control = r[4]; D.series = r[5]; D.context = r[6]; D.bounds = r[7]; D.rivers = r[8]; D.glaciers = r[9];
    var tp = D.truth.features[0];
    D.truthKind = (tp.properties.kind || '').split(' (')[0];
    D.truthCenter = tp.properties.center || (tp.geometry.type === 'Point' ? tp.geometry.coordinates : D.context.source_lonlat);
    var buf = D.truth.features.find(function (f) { return f.properties.role === 'hit_zone'; });
    if (buf) { D.truthBuffer = buf; D.truth = { type: 'FeatureCollection', features: D.truth.features.filter(function (f) { return f.properties.role !== 'hit_zone'; }) }; }
    D.truthLabel = D.context.truth_label || '';
    D.hasMotion = {}; D.windows.forEach(function (w) { D.hasMotion[w.id] = !!w.offsets_layer; });
    D.gcovPair = D.context.gcov_pair || [];
    $('rc-a').textContent = D.context.gcov_labels ? D.context.gcov_labels[0] : 'before';
    $('rc-b').textContent = D.context.gcov_labels ? D.context.gcov_labels[1] : 'after';
    bindChrome(); bindTimeline();
    ready.data = true;
    initMap();
  }).catch(function (e) {
    $('chapbody').innerHTML = '<div class="chap"><h2>Data not found</h2><p>Run the pipeline first (see README), then serve this folder over HTTP. ' + esc(e.message) + '</p></div>';
  });
})();
