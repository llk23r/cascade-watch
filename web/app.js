/* Cascade Watch — page logic. Everything drawn here is read from /data (pipeline output). */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var all = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var DATA = 'data/';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var darkMQ = window.matchMedia('(prefers-color-scheme: dark)');
  var phoneMQ = window.matchMedia('(max-width: 860px)');
  var APP = $('app');

  /* theme colours for charts drawn on glass (they follow light / dark) */
  var COL = {};
  function readColors() {
    var cs = getComputedStyle(document.documentElement);
    ['accent', 'red', 'green', 'yellow', 'truth', 'teal', 'purple', 'label', 'label2', 'label3', 'label4', 'sep', 'fill2', 'fill3', 'ok', 'bad', 'card', 'map-bg']
      .forEach(function (k) { COL[k.replace('-', '')] = cs.getPropertyValue('--' + k).trim(); });
  }
  readColors();
  /* map overlay colours: always drawn over the same radar/terrain imagery, so they do not change with the theme */
  var MC = { water: '#7cc4ff', flood: '#ff453a', truth: '#ffd60a', off: '#40cbe0', ph: '#bf5af2', ice: '#d8ecff', box: '#ffffff', road: '#ffd60a' };

  var D = {};            // loaded data
  var BLOBS = {};        // window id -> FeatureCollection
  var S = {              // UI state
    ch: 1, track: 'A', win: null, mode: 'window', t: 0, layer: 'motion', playing: false, seen: { 1: true },
    w: Object.assign({}, CWScore.DEFAULTS), rules: { sigma: true, dir: true, opp: true, ice: true, people: true, season: true },
    d: { record: 0.5, recognise: 2, confirm: 1, broadcast: 1 }, radarMix: 1, pitched: true, layerByCh: {}, autoLayer: false
  };
  var singleKeys = true; try { singleKeys = localStorage.getItem('cw-keys') !== 'off'; } catch (e) { /* storage blocked */ }
  var lastAnnounce = '', annT = null;
  var HIT_KM = 2;
  var map, markers = { villages: [], ranks: [], label: null, front: null };
  var cur = null; // current ranking result

  /* ---------------------------------------------------------------- utils */
  var LOG = [];            // every file this page has read, shown in the Data sheet
  function countOf(j) { if (Array.isArray(j)) return j.length + ' rows'; if (j && j.features) return j.features.length + ' features'; if (j && typeof j === 'object') return Object.keys(j).length + ' fields'; return ''; }
  function fetchJSON(p, opt) {
    var t0 = performance.now();
    return fetch(DATA + p).then(function (r) { if (!r.ok) throw new Error(p + ' ' + r.status); return r.text(); })
      .then(function (txt) {
        var j = JSON.parse(txt);
        LOG.push({ path: DATA + p, bytes: new Blob([txt]).size, n: countOf(j), ms: Math.round(performance.now() - t0) });
        return j;
      })
      .catch(function (e) { if (opt) return null; throw e; });
  }
  function fmtBytes(b) { return b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : b > 1e3 ? Math.round(b / 1e3) + ' kB' : b + ' B'; }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function icon(name, cls) { return '<svg class="ic' + (cls ? ' ' + cls : '') + '" aria-hidden="true"><use href="#i-' + name + '"/></svg>'; }
  function fmtDate(s, long) { var d = new Date(s + 'T00:00:00Z'); return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC', year: long ? 'numeric' : undefined }); }
  function winLabel(w) { return fmtDate(w.start) + ' → ' + fmtDate(w.end); }
  function clockAt(t) { var c = (D.replay && D.replay.collapse_npt || '00:00:00').split(':'); var base = +c[0] * 60 + +c[1] + (+c[2] || 0) / 60; var m = base + t; var h = Math.floor(m / 60), mm = Math.floor(m % 60); return (h < 10 ? '0' : '') + h + ':' + (mm < 10 ? '0' : '') + mm; }
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
  function fillRange(inp) { var p = (inp.value - inp.min) / ((inp.max - inp.min) || 1) * 100; inp.style.setProperty('--p', p + '%'); }
  function trackName(long) { return S.track === 'A' ? (long ? 'Ascending track 98' : 'Ascending 98') : (long ? 'Descending track 48' : 'Descending 48'); }

  /* ---------------------------------------------------------------- windows */
  function trackWindows() {
    return D.windows.filter(function (w) { return w.direction === S.track && (w.phase === 'pre' || w.phase === 'co'); })
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

  /* ---------------------------------------------------------------- layout + map padding */
  function isPhone() { return phoneMQ.matches; }
  function pad() {
    var H = window.innerHeight;
    if (isPhone()) { var sh = APP.dataset.sheet === 'full' ? 0.78 : 0.5; return { top: 110, bottom: Math.round(H * sh) + 64, left: 16, right: 16 }; }
    var cs = getComputedStyle(APP), g = parseFloat(cs.getPropertyValue('--g')) || 10;
    var sb = parseFloat(cs.getPropertyValue('--sb-w')) || 268, ins = parseFloat(cs.getPropertyValue('--ins-w')) || 376;
    return { top: g * 2 + 44, bottom: g * 2 + 64, left: APP.dataset.sidebar === 'open' ? sb + g * 2 : 0, right: APP.dataset.inspector === 'open' ? ins + g * 2 : 0 };
  }
  function relayout(animate) { if (map) map.easeTo({ padding: pad(), duration: animate && !reduce ? 450 : 0 }); }
  function togglePane(which, force) {
    if (which === 'sheet') {
      var full = APP.dataset.sheet !== 'full'; APP.dataset.sheet = full ? 'full' : 'half';
      $('grabber').setAttribute('aria-label', full ? 'Collapse panel' : 'Expand panel'); relayout(true); return;
    }
    if (isPhone()) return;
    var open = force != null ? force : APP.dataset[which] !== 'open';
    APP.dataset[which] = open ? 'open' : 'closed';
    var pane = which === 'sidebar' ? $('sidebar') : $('panel');
    var hadFocus = pane.contains(document.activeElement);
    pane.inert = !open;                       // hidden panes leave the tab order
    if (which === 'sidebar') { if (hadFocus || document.activeElement === $('tg-sidebar')) (open ? $('tg-sidebar-hide') : $('tg-sidebar')).focus(); }
    else { var b = $('tg-inspector'); b.setAttribute('aria-expanded', String(open)); b.title = (open ? 'Hide' : 'Show') + ' inspector (I)'; b.setAttribute('aria-label', b.title); if (hadFocus) b.focus(); }
    setTimeout(function () { relayout(true); }, 10);
  }
  /* segmented controls behave as radio groups: one tab stop, arrow keys move the choice */
  function segSelect(group, btn) { all('[role=radio]', group).forEach(function (b) { var on = b === btn; b.setAttribute('aria-checked', String(on)); b.tabIndex = on ? 0 : -1; }); }
  function segKeys(group) {
    group.addEventListener('keydown', function (e) {
      var d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key]; if (!d) return;
      e.preventDefault(); e.stopPropagation();
      var bs = all('[role=radio]', group), i = bs.indexOf(document.activeElement), nb = bs[(i + d + bs.length) % bs.length];
      nb.focus(); nb.click();
    });
  }

  /* ---------------------------------------------------------------- map */
  function initMap() {
    var b = D.bounds;
    var style = {
      version: 8,
      sources: {
        dem: { type: 'raster-dem', tiles: [location.href.replace(/[?#].*$/, '').replace(/[^/]*$/, '') + DATA + 'terrain/{z}/{x}/{y}.png'], encoding: 'terrarium', tileSize: 256, minzoom: 8, maxzoom: 13, bounds: [85.1, 27.85, 85.75, 28.45],
          attribution: 'Terrain: Copernicus DEM GLO-30 © ESA/DLR/Airbus' },
        relief: { type: 'image', url: DATA + 'img/relief.jpg', coordinates: b.relief },
        radarA: { type: 'image', url: DATA + 'img/' + (D.gcovPair[0] || 'relief') + '.jpg', coordinates: b.box },
        radarB: { type: 'image', url: DATA + 'img/' + (D.gcovPair[1] || 'relief') + '.jpg', coordinates: b.box },
        radarC: { type: 'image', url: DATA + 'img/' + (D.context.gcov_change || 'relief') + '.png', coordinates: b.box },
        win: { type: 'image', url: DATA + 'img/motion_' + lastPre().id + '.png', coordinates: b.box }
      },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': COL.mapbg } },
        { id: 'relief', type: 'raster', source: 'relief', paint: { 'raster-fade-duration': 0 } },
        { id: 'radarA', type: 'raster', source: 'radarA', layout: { visibility: 'none' }, paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 } },
        { id: 'radarB', type: 'raster', source: 'radarB', layout: { visibility: 'none' }, paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 } },
        { id: 'radarC', type: 'raster', source: 'radarC', layout: { visibility: 'none' }, paint: { 'raster-opacity': 0.95, 'raster-fade-duration': 0 } },
        { id: 'win', type: 'raster', source: 'win', paint: { 'raster-opacity': 0.92, 'raster-fade-duration': 250 } }
      ],
      terrain: { source: 'dem', exaggeration: 1.35 }
    };
    map = new maplibregl.Map({ container: 'map', style: style, center: CAM[1].center, zoom: CAM[1].zoom, pitch: CAM[1].pitch, bearing: CAM[1].bearing, maxPitch: 75,
      attributionControl: { compact: true }, minZoom: 8.3, maxZoom: 14.5, maxBounds: [[84.7, 27.6], [86.15, 28.7]] });
    map.setPadding(pad());
    map.on('rotate', function () { $('north-ic').style.transform = 'rotate(' + (-map.getBearing()) + 'deg)'; });
    map.on('pitchend', function () { var p = map.getPitch() > 5; S.pitched = p; $('z-3d').setAttribute('aria-pressed', String(p)); });
    map.on('load', function () {
      map.addSource('box', { type: 'geojson', data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [b.box.concat([b.box[0]])] } } });
      map.addLayer({ id: 'box', type: 'line', source: 'box', paint: { 'line-color': MC.box, 'line-opacity': 0.4, 'line-width': 1, 'line-dasharray': [3, 3] } });
      if (D.rivers) {
        map.addSource('rivers', { type: 'geojson', data: D.rivers });
        map.addLayer({ id: 'rivers', type: 'line', source: 'rivers', paint: { 'line-color': MC.water, 'line-opacity': ['case', ['==', ['get', 'waterway'], 'river'], 0.8, 0.35], 'line-width': ['case', ['==', ['get', 'waterway'], 'river'], 1.7, 0.7] } });
      }
      if (D.glaciers) {
        map.addSource('glaciers', { type: 'geojson', data: D.glaciers });
        map.addLayer({ id: 'glaciers', type: 'line', source: 'glaciers', paint: { 'line-color': MC.ice, 'line-opacity': 0.3, 'line-width': 0.6 } });
      }
      if (D.observed) {
        map.addSource('observed', { type: 'geojson', data: D.observed });
        map.addLayer({ id: 'obs-flood', type: 'fill', source: 'observed', filter: ['in', ['get', 'kind'], ['literal', ['flood', 'lake']]], paint: { 'fill-color': ['match', ['get', 'kind'], 'lake', MC.water, MC.flood], 'fill-opacity': 0.38 } });
        map.addLayer({ id: 'obs-road', type: 'line', source: 'observed', filter: ['==', ['get', 'kind'], 'road'], paint: { 'line-color': MC.road, 'line-width': 2 } });
        map.addLayer({ id: 'obs-bld', type: 'circle', source: 'observed', filter: ['==', ['get', 'kind'], 'building'], paint: { 'circle-color': MC.flood, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 1.2, 13, 3], 'circle-stroke-color': '#fff', 'circle-stroke-width': 0.5, 'circle-opacity': 0.9 } });
      }
      map.addSource('flood', { type: 'geojson', lineMetrics: true, data: { type: 'Feature', geometry: { type: 'LineString', coordinates: D.replay.path } } });
      map.addLayer({ id: 'flood-glow', type: 'line', source: 'flood', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-width': 16, 'line-blur': 9, 'line-opacity': 0, 'line-gradient': floodGradient(0, true) } });
      map.addLayer({ id: 'flood', type: 'line', source: 'flood', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-width': ['interpolate', ['linear'], ['zoom'], 9, 3.5, 13, 7], 'line-gradient': floodGradient(0) } });
      map.addSource('blobs', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'blobs-fill', type: 'fill', source: 'blobs', paint: {
        'fill-color': ['case', ['==', ['get', '_rank'], 0], 'rgba(235,235,245,0.10)', ['match', ['get', 'channel'], 'phase', MC.ph, MC.off]],
        'fill-opacity': ['case', ['get', '_top'], 0.62, 0.32] } });
      map.addLayer({ id: 'blobs-line', type: 'line', source: 'blobs', filter: ['>', ['get', '_rank'], 0], paint: {
        'line-color': ['case', ['get', '_top'], '#ffffff', 'rgba(255,255,255,0.45)'], 'line-width': ['case', ['get', '_top'], 1.8, 0.7] } });
      map.addLayer({ id: 'blobs-line-dead', type: 'line', source: 'blobs', filter: ['==', ['get', '_rank'], 0], paint: {
        'line-color': 'rgba(235,235,245,0.45)', 'line-width': 0.7, 'line-dasharray': [2, 2] } });
      map.addSource('truth', { type: 'geojson', data: D.truth });
      map.addSource('ring', { type: 'geojson', data: D.truthBuffer || ringAround(D.truthCenter, HIT_KM) });
      map.addLayer({ id: 'ring', type: 'line', source: 'ring', paint: { 'line-color': MC.truth, 'line-width': 1.4, 'line-dasharray': [2, 2.5], 'line-opacity': 0.9 } });
      map.addLayer({ id: 'truth-fill', type: 'fill', source: 'truth', paint: { 'fill-color': MC.truth, 'fill-opacity': 0.2 } });
      map.addLayer({ id: 'truth-line', type: 'line', source: 'truth', paint: { 'line-color': MC.truth, 'line-width': 2.2 } });
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
    var a = glow ? 'rgba(255,69,58,0.55)' : MC.flood;
    if (p <= 0.0005) return ['interpolate', ['linear'], ['line-progress'], 0, 'rgba(255,69,58,0)', 1, 'rgba(255,69,58,0)'];
    if (p >= 0.999) return ['interpolate', ['linear'], ['line-progress'], 0, a, 1, a];
    var p0 = Math.max(0.0001, p - 0.03);
    return ['interpolate', ['linear'], ['line-progress'], 0, a, p0, a, p, glow ? 'rgba(255,233,226,0.8)' : '#ffe9e2', Math.min(0.9999, p + 0.0005), 'rgba(255,69,58,0)', 1, 'rgba(255,69,58,0)'];
  }

  function addVillageMarkers() {
    markers.villages.forEach(function (m) { m.marker.remove(); });
    var prev = null;
    markers.villages = (D.replay.villages || []).map(function (v) {
      var near = prev && Math.abs(v.km - prev.km) < 4;   // two places close together: put this label on the other side
      prev = v;
      var e = el('div', 'mk-village' + (near ? ' flip' : ''), '<i></i><span>' + esc(v.name) + '</span><em></em>');
      var m = new maplibregl.Marker({ element: e, anchor: near ? 'right' : 'left', offset: [near ? 5 : -5, 0] }).setLngLat([v.lon, v.lat]).addTo(map);
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
      tip.innerHTML = '<b>' + (Number(p._rank) > 0 ? 'Rank ' + p._rank + ' · ' : 'Not ranked · ') + (off ? 'Offset blob' : 'Phase blob') + '</b>' +
        '<div class="v">' + mot + ' ± ' + (off ? Number(p.sigma_m).toFixed(2) + ' m' : Math.round(p.sigma_m * 1000) + ' mm') + '</div>' +
        '<div class="k">Direction coherence ' + Number(p.dir_coherence).toFixed(2) + ' · signal quality ' + Number(p.coherence).toFixed(2) + '</div>' +
        '<div class="k">' + (p.ice_over_rock === true || p.ice_over_rock === 'true' ? 'Hanging ice over rock · ' : '') + (p.opposing_sign === true || p.opposing_sign === 'true' ? 'opposing neighbour · ' : '') +
        Math.round(Number(p.people_below || 0)).toLocaleString() + ' people within 1 km of its first 30 km</div>' +
        '<div class="k">' + Math.round(p.elev_m).toLocaleString() + ' m · ' + Number(p.area_km2).toFixed(2) + ' km² · drains to ' + esc(p.channel_name || 'an unnamed channel') + '</div>' +
        '<div class="t">' + Number(p.dist_truth_km).toFixed(1) + ' km from what fell (used only to score)</div>';
      tip.hidden = false;
      var W = window.innerWidth, H = window.innerHeight;
      var x = e.point.x + 16, y = e.point.y + 16;
      if (x + 270 > W) x = e.point.x - 276;
      if (y + 160 > H) y = e.point.y - 164;
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
    if (radar) map.setPaintProperty('radarB', 'raster-opacity', S.radarMix);
    if (show && w) {
      var name = S.layer + '_' + w.id;
      if (S.layer === 'motion' && !D.hasMotion[w.id]) name = 'phase_' + w.id;
      map.getSource('win').updateImage({ url: DATA + 'img/' + name + '.png', coordinates: D.bounds.box });
    }
    renderLegend();
  }

  var legendKey = '';
  function renderLegend() {
    var eventView = S.mode === 'event';
    var key = S.layer + '|' + eventView;
    if (key === legendKey) return;
    legendKey = key;
    var L = $('legend'), html = '';
    if (S.layer === 'motion') html = '<b>Downhill motion this pass</b><br>NISAR pixel offsets on the fall line<div class="ramp" style="background:linear-gradient(90deg,#35d0e6,#c76bf2,#f3cf55,#fffae6)"></div><div class="ends"><span>0.15 m</span><span>1 m</span><span>2 m+</span></div>';
    else if (S.layer === 'phase') html = '<b>Line-of-sight motion</b><br>NISAR phase, 3–5 km ring removed<div class="ramp" style="background:linear-gradient(90deg,#4084ff,#ebf0f5,#ff6152)"></div><div class="ends"><span>60 mm away</span><span>0</span><span>60 mm towards</span></div>';
    else if (S.layer === 'coh') html = '<b>Coherence</b> · where the radar kept its lock<div class="ramp" style="background:linear-gradient(90deg,#000,#e5edf7)"></div><div class="ends"><span>0 · lost</span><span>0.5</span><span>1</span></div>';
    else if (S.layer === 'radar') html = '<b>NISAR L-band radar backscatter (GCOV)</b><br>Drag towards the after image: <i style="background:#ff7a5a"></i><b>brighter</b> and <i style="background:#5aa0ff"></i><b>darker</b> mark ground that changed. Radar sees through monsoon cloud.' +
      '<div class="rc"><span id="rc-a">' + esc(D.context.gcov_labels ? D.context.gcov_labels[0] : 'before') + '</span><input type="range" id="rc-slider" min="0" max="1" step="0.01" value="' + S.radarMix + '" aria-label="Blend from before to after the collapse"><span id="rc-b">' + esc(D.context.gcov_labels ? D.context.gcov_labels[1] : 'after') + '</span></div>';
    else if (eventView) html = '<b>' + fmtDate(eventDate()) + '</b> · the front passes each gauge in the gap between its last normal reading and its silence; places between are interpolated' +
      '<div class="row"><i style="background:' + MC.flood + '"></i>Flood front, mapped flood extent, damaged buildings</div>' +
      '<div class="row"><i style="background:#30d158;border-radius:50%"></i><span>Warned in time</span><i style="background:' + MC.flood + ';border-radius:50%"></i><span>Hit first</span></div>';
    else html = '<b>Terrain</b> · Copernicus DEM, 1.35× vertical';
    if (!eventView || S.layer === 'radar') {
      html += '<div class="row"><i style="box-shadow:inset 0 0 0 2px ' + MC.truth + '"></i>What fell, and the 2 km hit zone</div>';
      if (S.layer !== 'radar') html += '<div class="row"><i style="background:' + MC.off + '"></i><span>Offset blob</span><i style="background:' + MC.ph + '"></i><span>Phase blob</span><i style="box-shadow:inset 0 0 0 1px rgba(128,128,128,.8)"></i><span>Not ranked</span></div>';
    }
    L.innerHTML = html;
    var rc = $('rc-slider');
    if (rc) { fillRange(rc); rc.addEventListener('input', function () { S.radarMix = +rc.value; fillRange(rc); setWindowRaster(); }); }
  }

  /* ---------------------------------------------------------------- blobs on map */
  function paintBlobs() {
    var w = activeWindow(); if (!w || !BLOBS[w.id] || !map || !map.getSource('blobs')) return;
    var fc = BLOBS[w.id];
    var N = S.w.nSensors;
    var rk = new Map();
    cur.rows.forEach(function (r) { rk.set((r.f._orig || r.f).properties.id, r.rank || 0); });
    var feats = fc.features.map(function (f) {
      var r = rk.get(f.properties.id) || 0;
      var p = Object.assign({}, f.properties, { _rank: r, _top: r > 0 && r <= N });
      return { type: 'Feature', geometry: f.geometry, properties: p };
    });
    map.getSource('blobs').setData({ type: 'FeatureCollection', features: feats });
    var prev = {}; markers.ranks.forEach(function (m) { prev[m.id] = m.rank; m.marker.remove(); });
    markers.ranks = [];
    var showBadges = S.mode !== 'event';
    if (showBadges) {
      cur.ranked.slice(0, N).forEach(function (r) {
        var p = (r.f._orig || r.f).properties;
        var isSrc = cur.hit && cur.hit === r;
        var e = el('div', 'mk-rank' + (isSrc ? ' src' : '') + (prev[p.id] !== r.rank ? ' pop' : ''), String(r.rank));
        e.title = 'Rank ' + r.rank + (isSrc ? ': source valley (counts as a hit)' : '') + '. Click to inspect its score.';
        e.addEventListener('click', function () { S.sel = p.id; map.easeTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 12), duration: 800 }); paintChapterLive(); });
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
    var t = S.mode === 'event' ? S.t : 0;
    var km = S.mode === 'event' ? frontKm(t) : 0;
    var p = Math.min(1, km / total);
    var pp = S.mode === 'event' ? p : 0;
    map.setPaintProperty('flood', 'line-gradient', floodGradient(pp));
    map.setPaintProperty('flood-glow', 'line-gradient', floodGradient(pp, true));
    map.setPaintProperty('flood-glow', 'line-opacity', pp > 0 ? 1 : 0);
    var hideBlobs = S.mode === 'event';
    ['blobs-fill', 'blobs-line', 'blobs-line-dead'].forEach(function (id) { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', hideBlobs ? 'none' : 'visible'); });
    var fe = markers.front.getElement();
    if (S.mode === 'event' && p > 0 && p < 1) { fe.style.display = ''; markers.front.setLngLat(pointAtKm(km)); } else fe.style.display = 'none';
    if (map.getLayer('obs-flood')) {
      var showObs = (S.mode === 'event' && S.t >= TL.tmax - 0.01) || (S.mode === 'window' && (winById(S.win) || {}).phase === 'co');
      ['obs-flood', 'obs-road', 'obs-bld'].forEach(function (id) { map.setLayoutProperty(id, 'visibility', showObs ? 'visible' : 'none'); });
    }
    var A = D.replay.alert_min, T = tripT(), armed = tripwireArmed();
    markers.villages.forEach(function (m) {
      var v = m.v, arr = v.arrival_min;
      var hit = S.mode === 'event' && arr != null && S.t >= arr;
      var warned = S.mode === 'event' && !hit && ((S.t >= A) || (armed && S.ch === 4 && S.t >= T));
      m.el.classList.toggle('hit', !!hit); m.el.classList.toggle('warned', !!warned);
      var em = m.el.querySelector('em');
      if (S.mode === 'event' && arr != null) em.textContent = hit ? ('hit at +' + arr.toFixed(0) + ' min') : (warned ? 'warned' : ('+' + arr.toFixed(0) + ' min'));
      else em.textContent = '';
    });
  }

  /* ---------------------------------------------------------------- scorecard */
  function paintScorecard() {
    var w = activeWindow(); if (!w || !cur) return;
    $('sc-win').textContent = (S.track === 'A' ? 'Asc · ' : 'Desc · ') + winLabel(w);
    var n = $('sc-n'), old = n.textContent;
    var r = cur.srcRank, N = S.w.nSensors, state = r ? (r <= N ? 'in' : 'out') : 'none';
    n.textContent = r ? '#' + r : '—';
    n.className = 'w-n ' + state + (old !== n.textContent && !reduce ? ' bump' : '');
    $('sc-of').innerHTML = r ? ('of ' + cur.n + ' moving slopes<br>' + (r <= N ? 'on the watch list' : 'off the watch list')) : (cur.nearCount ? 'present, but scored zero<br>under these rules' : 'no moving slope<br>within 2 km');
    $('sc-cut').style.width = Math.min(100, N / Math.max(1, cur.n) * 100) + '%';
    var dot = $('sc-dot'); dot.style.left = (r ? (cur.n > 1 ? (r - 1) / (cur.n - 1) * 100 : 0) : 100) + '%'; dot.style.opacity = r ? 1 : 0;
    $('sc-above').textContent = r ? String(r - 1) : '—';
    var pass = D.scorecard.pass || {}, tst = $('sc-test');
    tst.textContent = pass.passed ? 'Passed' : 'Not passed'; tst.className = pass.passed ? 'good' : 'bad';
    $('sc-test-row').title = 'Fixed before the first run: ' + (pass.rule || '') + '. It judges the last two passes together, not this pass alone.';
    var sc = D.scorecard.windows[w.id] || {};
    $('sc-base').textContent = sc.baseline_rank ? ('#' + sc.baseline_rank + ' of ' + sc.n_offsets) : '—';
    var zr = (D.v2 && D.v2.zone && D.v2.zone.radius_km) || 5, zn = (D.v2 && D.v2.watch_list && D.v2.watch_list.top_n) || 10;
    var zhit = cur.ranked.slice(0, zn).filter(function (x) { return x.p.same_channel && x.p.dist_truth_km <= zr; })[0];
    var zd = $('sc-zone'); zd.textContent = zhit ? 'On the list (#' + zhit.rank + ')' : 'Not listed'; zd.className = zhit ? 'good' : 'muted';
    var armed = tripwireArmed(), T = tripT(), A = D.replay.alert_min;
    var trip = $('sc-trip'); trip.textContent = armed ? ('+' + T.toFixed(1) + ' min') : 'Not armed'; trip.className = armed ? 'good' : 'muted';
    trip.parentNode.title = armed ? 'The source valley was in the top ' + N + ' of the last pass before the collapse, so a ground sensor was in place.' : 'The source valley was not in the top ' + N + ' of the last pass before the collapse, so no sensor was placed.';
    var gain = $('sc-gain');
    if (armed) {
      var vs = (D.replay.villages || []).filter(function (v) { return v.arrival_min != null; });
      var warnedTrip = vs.filter(function (v) { return v.arrival_min > T; }).length, warnedReal = vs.filter(function (v) { return v.arrival_min > A; }).length;
      gain.textContent = '+' + (A - T).toFixed(0) + ' min'; gain.className = 'good';
      gain.parentNode.title = 'Alert ' + (A - T).toFixed(1) + ' min earlier than the real SMS. Places warned before the wave: ' + warnedTrip + ' of ' + vs.length + ' (real alert: ' + warnedReal + ').';
    } else { gain.textContent = '0'; gain.className = 'muted'; }
    var mn = $('scm-n'); mn.textContent = n.textContent; mn.className = state;
    var msg = winLabel(w) + ': the source valley ' + (r ? 'ranked ' + r + ' of ' + cur.n : 'is not ranked') + '.';
    if (!S.playing && msg !== lastAnnounce) { lastAnnounce = msg; clearTimeout(annT); annT = setTimeout(function () { $('announce').textContent = msg; }, 700); }
    $('scm-of').textContent = r ? 'of ' + cur.n + ' · ' + winLabel(w) : winLabel(w);
  }

  /* ---------------------------------------------------------------- timeline */
  var TL = { a: 0.70, b: 0.88, tmax: 65 };   // tmax is reset from the replay's last front time at load
  function eventDate() { return D.config.windows.event_utc.slice(0, 10); }
  function tlSpan() {
    var pre = preWindows(); var t0 = Date.parse(pre[0].start), t1 = Date.parse(eventDate());
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
    var ev = $('evzone'); ev.style.left = (TL.a * 100) + '%'; ev.style.width = 'calc(' + ((TL.b - TL.a) * 100) + '% - 4px)'; ev.style.right = 'auto';
    if (!ev.querySelector('.prog')) ev.insertBefore(el('div', 'prog'), ev.firstChild);
    all('.evt', ev).forEach(function (x) { x.remove(); });
    var seenT = {};
    (D.replay.events || []).concat([{ t_min: D.replay.alert_min, label: 'SMS alert', alert: true }]).forEach(function (e) {
      if (e.t_min < 0 || e.t_min > TL.tmax || seenT[e.t_min.toFixed(1)]) return; seenT[e.t_min.toFixed(1)] = 1;
      var m = el('i', 'evt' + (e.alert ? ' alert' : '')); m.style.left = (e.t_min / TL.tmax * 100) + '%'; m.title = clockAt(e.t_min) + ' · ' + e.label; ev.appendChild(m);
    });
    [0, 30].forEach(function (m) { var t = el('span', 'ev' + (m ? ' mt' : ''), clockAt(m)); t.style.left = ((TL.a + (TL.b - TL.a) * m / TL.tmax) * 100) + '%'; ticks.appendChild(t); });
    var co = coWindows()[0];
    var old = $('coseg'); if (old) old.remove();
    if (co) {
      var s = el('div', 'seg-w co'); s.style.top = '8px'; s.style.left = (TL.b * 100) + '%'; s.style.width = ((1 - TL.b) * 100) + '%';
      s.dataset.win = co.id; s.title = 'Radar pass across the collapse (' + winLabel(co) + '). Pick it to see the avalanche path in the before/after radar image.'; s.id = 'coseg';
      $('track').appendChild(s);
      var t = el('span', 'aft', fmtDate(co.start).split(' ')[0] + '–' + fmtDate(co.end)); t.style.left = ((TL.b + (1 - TL.b) / 2) * 100) + '%'; t.title = 'The radar pass that spans the collapse'; ticks.appendChild(t);
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
  function stateLabel() { return S.mode === 'event' ? clockAt(S.t) + ' · +' + S.t.toFixed(1) + ' min' : winLabel(winById(S.win)); }
  function paintTimeline() {
    var x = posOfState(), k = $('knob');
    k.style.left = (x * 100) + '%';
    var lab = stateLabel();
    $('knob-lab').textContent = lab;
    all('.seg-w').forEach(function (s) { s.classList.toggle('on', S.mode === 'window' && s.dataset.win === S.win); });
    var prog = document.querySelector('#evzone .prog'); if (prog) prog.style.width = (S.mode === 'event' ? (S.t / TL.tmax * 100) : (posOfState() > TL.b ? 100 : 0)) + '%';
    $('track').setAttribute('aria-valuenow', String(Math.round(x * 1000)));
    $('track').setAttribute('aria-valuetext', lab);
    $('tb-sub').textContent = S.mode === 'event' ? '26 Aug 2026 · ' + clockAt(S.t) + ' NPT · ' + (S.t < 0.05 ? 'the collapse' : '+' + S.t.toFixed(1) + ' min') : trackName() + ' · ' + winLabel(winById(S.win)) + ' 2026';
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
      else if (ev.key === 'Home') { ev.preventDefault(); stop(); S.mode = 'window'; S.win = preWindows()[0].id; update(true); }
      else if (ev.key === 'End') { ev.preventDefault(); stop(); var co = coWindows()[0]; if (co) { S.mode = 'window'; S.win = co.id; } else { S.mode = 'event'; S.t = TL.tmax; } update(true); }
      else if (ev.key === 'PageUp' || ev.key === 'PageDown') { ev.preventDefault(); stop(); for (var k = 0; k < 5; k++) step(ev.key === 'PageDown' ? 1 : -1); }
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
  var lastNow = 0;
  function setPlayUI(on) {
    $('play-icon').setAttribute('href', on ? '#i-pause' : '#i-play');
    var lab = on ? 'Pause (Space)' : 'Play (Space)'; $('play').setAttribute('aria-label', lab); $('play').title = lab;
    var c = $('cta-play');
    if (c) c.innerHTML = icon(on ? 'pause' : 'play') + (on ? 'Pause' : (S.mode === 'event' && S.t >= TL.tmax - 0.1 ? 'Play it again' : c.dataset.label));
  }
  function play() {
    S.playing = true;
    if (S.ch === 4 || S.ch === 1) { if (S.mode !== 'event' || S.t >= TL.tmax - 0.1) { S.mode = 'event'; S.t = 0; } }
    else if (S.mode !== 'window' || !winById(S.win) || winById(S.win).phase !== 'pre') { S.mode = 'window'; S.win = preWindows()[0].id; }
    else if (S.win === lastPre().id) { S.win = preWindows()[0].id; }
    setPlayUI(true);
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
  function stop() { S.playing = false; setPlayUI(false); }

  /* ---------------------------------------------------------------- update loop */
  var lastWinPainted = null;
  function update(windowChanged) {
    var w = activeWindow();
    if (!w) return;
    loadBlobs(w.id).then(function () {
      if (w !== activeWindow()) return;
      cur = rankWindow(BLOBS[w.id]);
      var isCo = S.mode === 'window' && (winById(S.win) || {}).phase === 'co';
      if (isCo && !S.layerByCh[S.ch] && S.layer !== 'radar' && S.track === 'A') { S.autoLayer = true; setLayer('radar'); }
      else if (S.mode === 'window' && !isCo && S.autoLayer && S.layer === 'radar') { setLayer('motion'); }
      else if (S.mode === 'window' && S.layer === 'none' && !S.layerByCh[S.ch]) { S.autoLayer = true; setLayer('motion'); }
      else if (S.mode === 'event' && S.autoLayer) { S.autoLayer = false; setLayer('none'); }
      if (windowChanged && w.id !== lastWinPainted) S.sel = null;
      if (windowChanged && w.id !== lastWinPainted && map && map.getSource('win')) {
        lastWinPainted = w.id; setWindowRaster();
        if (!reduce) { var sw = $('sweep'); sw.classList.remove('go'); void sw.offsetWidth; sw.classList.add('go'); }
      }
      paintBlobs(); paintFlood(); paintScorecard(); paintTimeline(); paintChapterLive(); renderLegend(); paintPasses();
    });
  }

  /* ---------------------------------------------------------------- passes (sidebar source list) */
  var passKey = '', lastPassShown = null;
  function passList() { return preWindows().concat(coWindows()); }
  function paintPasses() {
    var ul = $('passes'), list = passList();
    var key = S.track + JSON.stringify(S.w) + JSON.stringify(S.rules) + list.filter(function (w) { return BLOBS[w.id]; }).length;
    if (key !== passKey) {
      passKey = key;
      $('passes-h').textContent = 'Passes · ' + trackName();
      lastPassShown = null;
      ul.innerHTML = list.map(function (w) {
        var r = BLOBS[w.id] ? rankWindow(BLOBS[w.id]).srcRank : (D.scorecard.windows[w.id] || {}).source_rank;
        var cls = r ? (r <= S.w.nSensors ? 'in' : '') : 'none';
        var co = w.phase === 'co';
        return '<li><button type="button" data-win="' + w.id + '" title="' + winLabel(w) + (co ? ', spans the collapse' : '') + ': source valley ' + (r ? 'ranked #' + r : 'not ranked') + '">' +
          '<span class="pd">' + winLabel(w) + (co ? '<small>after</small>' : '') + '</span><span class="pb ' + cls + '">' + (r ? '#' + r : '—') + '</span></button></li>';
      }).join('');
    }
    var curB = null;
    all('button', ul).forEach(function (b) { var on = (S.mode === 'window' ? S.win : (lastPre() || {}).id) === b.dataset.win; b.setAttribute('aria-current', String(S.mode === 'window' && on)); if (on) curB = b; });
    if (curB && curB !== lastPassShown) { lastPassShown = curB; var sc = curB.closest('.sb-scroll'), br = curB.getBoundingClientRect(), sr = sc.getBoundingClientRect(); if (br.bottom > sr.bottom - 16 || br.top < sr.top) sc.scrollTo({ top: sc.scrollTop + br.top - sr.top - sr.height / 2 + br.height / 2, behavior: reduce ? 'auto' : 'smooth' }); }
  }
  function preloadPasses() { passList().forEach(function (w) { loadBlobs(w.id).then(paintPasses); }); }

  /* ---------------------------------------------------------------- chapters */
  var NAMES = { 1: 'The Morning', 2: 'The Creep', 3: 'The Haystack', 4: 'The Race', 5: 'The Answer' };
  var CAM = {
    1: { center: [85.36, 28.13], zoom: 9.55, pitch: 52, bearing: -18 },
    2: { center: [85.505, 28.292], zoom: 11.7, pitch: 62, bearing: 168 },
    3: { center: [85.5, 28.28], zoom: 10.35, pitch: 40, bearing: -6 },
    4: { center: [85.33, 28.14], zoom: 9.8, pitch: 56, bearing: -24 },
    5: { center: [85.48, 28.27], zoom: 10.5, pitch: 46, bearing: -10 }
  };
  function go(ch, fly) {
    var back = ch < S.ch;
    S.ch = ch; S.seen[ch] = true; stop();
    all('#nav button').forEach(function (b) { var c = +b.dataset.ch; b.setAttribute('aria-current', String(c === ch)); b.classList.toggle('seen', !!S.seen[c]); });
    all('#tabbar button').forEach(function (b) { b.setAttribute('aria-current', String(+b.dataset.ch === ch)); });
    all('#dots i').forEach(function (d, i) { d.classList.toggle('on', i + 1 === ch); });
    $('prev').style.visibility = ch === 1 ? 'hidden' : '';
    $('next').innerHTML = ch < 5 ? NAMES[ch + 1] + icon('chev-r') : 'Start over' + icon('chev-r');
    $('next').title = ch < 5 ? 'Next chapter (→ or ' + (ch + 1) + ')' : 'Back to the beginning';
    $('tb-title').textContent = NAMES[ch];
    document.title = NAMES[ch] + ' · Cascade Watch';
    if (ch === 1) { S.mode = 'event'; S.t = 0; }
    if (ch === 2 || ch === 3 || ch === 5) { S.mode = 'window'; S.win = lastPre().id; }
    if (ch === 4) { S.mode = 'event'; S.t = 0; }
    S.autoLayer = false; setLayer(S.layerByCh[ch] || (ch === 1 || ch === 4 ? 'none' : 'motion'));
    $('panel').setAttribute('aria-label', 'Chapter ' + ch + ' of 5: ' + NAMES[ch]);
    renderChapter(back);
    if (map && fly !== false) map.flyTo(Object.assign({ duration: reduce ? 0 : 2200, essential: true, curve: 1.3, padding: pad() }, CAM[ch]));
    update(true);
    $('chapbody').scrollTop = 0;
  }
  function setLayer(l, byUser) {
    S.layer = l;
    if (byUser) { S.layerByCh[S.ch] = l; S.autoLayer = false; }
    segSelect($('layerbar'), document.querySelector('.layerbar [data-layer="' + l + '"]'));
    setWindowRaster();
  }

  function factRow(key) {
    var f = fact(key); if (!f) return '';
    return '<div class="r ' + (key === 'dead' ? '' : (f.tone || '')) + '"><span class="fv">' + esc(f.display || f.value) + (f.unit ? '<small>' + esc(f.unit) + '</small>' : '') + '</span><span class="fl">' + esc(f.label) + cite(f.source) + '</span></div>';
  }
  function rule(k, t, d) { return '<label class="r" for="r-' + k + '"><span class="l">' + t + '<small>' + d + '</small></span><input type="checkbox" class="sw" id="r-' + k + '"' + (S.rules[k] ? ' checked' : '') + '></label>'; }
  function slider(id, label, mn, mx, st, key) { return '<label class="r slr" for="w-' + id + '"><span class="l">' + label + '</span><input type="range" id="w-' + id + '" data-key="' + key + '" min="' + mn + '" max="' + mx + '" step="' + st + '" value="' + S.w[key] + '"><output></output></label>'; }
  function delay(k, label, mn, mx, st) { return '<label class="r slr" for="d-' + k + '"><span class="l">' + label + '</span><input type="range" id="d-' + k + '" data-key="' + k + '" min="' + mn + '" max="' + mx + '" step="' + st + '" value="' + S.d[k] + '"><output></output></label>'; }

  function renderChapter(back) {
    var body = $('chapbody'), lp = lastPre(), sc = D.scorecard, html = '';
    var A = D.replay.alert_min, cls = 'chap' + (back ? ' back' : '');
    if (S.ch === 1) {
      html = '<div class="' + cls + '"><p class="cap">' + icon('pin') + '26 August 2026 · Rasuwa, Nepal</p>' +
        '<h2 class="title">The alert came ' + A.toFixed(1) + ' minutes after the mountain fell</h2>' +
        '<p>' + esc(D.context.story.morning) + '</p>' +
        '<div class="cta"><button type="button" class="btn primary large" id="cta-play" data-label="Play the wave">' + icon('play') + 'Play the wave</button><span class="hint">or press <kbd>Space</kbd></span></div>' +
        atlasCard() +
        '<div class="group facts">' + (D.context.facts_order || []).map(factRow).join('') + '</div>' +
        '<p class="sub">' + esc(D.context.story.morning2) + '</p>' +
        '<div class="callout q">' + icon('target') + '<div><b>The question this replay answers</b><span>With rules fixed in advance, would NISAR have put this valley on a watch list before it fell?</span></div></div>' + dataSlot() + '</div>';
    } else if (S.ch === 2) {
      html = '<div class="' + cls + '"><p class="cap">' + icon('chart') + fmtDate(preWindows()[0].start) + ' – ' + fmtDate(lp.end) + ' · NISAR ' + trackName(true).toLowerCase() + '</p>' +
        '<h2 class="title">Radar watched the slope through the monsoon</h2>' +
        '<p>NISAR’s orbit repeats every 12 days; on this track ' + preWindows().length + ' pairs cover the source before the collapse' + (preWindows().some(function (w) { return w.stack_days > 12; }) ? ', some spanning ' + Math.max.apply(null, preWindows().map(function (w) { return w.stack_days; })) + ' days where an acquisition is missing' : '') + '. Each pair of passes gives two independent motion maps: <strong>pixel offsets</strong> for metre-scale motion of the moving mass, and <strong>phase</strong> for millimetre-scale motion at its edges.</p>' +
        '<div class="sec-h">Motion inside the mapped detachment zone <small>metres downhill, per pass</small></div>' +
        '<div class="card chart" id="creep"></div>' +
        '<p class="sub" id="creep-note"></p>' +
        '<div class="sec-h">Why L-band? <small>what each band can see</small></div>' +
        '<div class="seg inline" id="bands" role="radiogroup" aria-label="Explain a radar band" style="width:100%"><button type="button" data-band="L" role="radio" aria-checked="true" tabindex="0" style="flex:1;justify-content:center">L-band · NISAR</button><button type="button" data-band="C" role="radio" aria-checked="false" tabindex="-1" style="flex:1;justify-content:center">C-band · Sentinel-1</button></div>' +
        '<div class="callout" id="bandnote"></div>' +
        '<p class="note">NISAR GOFF offsets projected on the fall line, inside the UNOSAT detachment zone. Hatched bars: the source valley was not ranked that pass. Tap a bar to load that pass on the map.</p>' + dataSlot() + '</div>';
    } else if (S.ch === 3) {
      html = '<div class="' + cls + '"><p class="cap">' + icon('search') + 'The ranking problem</p>' +
        '<h2 class="title">One moving slope among many</h2>' +
        '<p>Glaciers flow every day, so moving ground is normal here. The screen has to pick a handful of places worth a closer look out of hundreds of moving patches. This is the rule it uses, and what it gives for one patch.</p>' +
        ruleCard() +
        '<div class="hero"><div class="rank" id="rank"><span class="n" id="rank-n"></span><span class="of" id="rank-of"></span></div>' +
        '<div class="strip"><div class="cut" id="cut"></div><div class="pin" id="pin"><span id="pinlab"></span></div></div><div class="ends"><span>#1 most suspicious</span><span id="strip-n"></span></div></div>' +
        '<div class="callout" id="verdict"></div>' +
        '<div class="sec-h">Pre-registered rules <small>on = as committed</small></div>' +
        '<div class="group">' +
        rule('sigma', 'Trust clean signals', 'Divide motion by its measured uncertainty, so noisy patches drop.') +
        rule('dir', 'Moving one way', 'Reward offsets that point the same way across the patch.') +
        rule('opp', 'Neighbours pulling apart', 'One block moves towards the radar while the next moves away (Kutoğlu 2026).') +
        rule('ice', 'Hanging ice over rock', 'Glacier ice on slopes steeper than 30°, the terrain that fails as ice-rock avalanches.') +
        rule('people', 'People in the channel below', 'WorldPop residents within 1 km of the first 30 km downstream.') +
        rule('season', 'Hot season', 'ERA5 temperature anomaly from 1 May to the end of the pass. The same for every patch in a pass, so it never changes their order.') +
        '</div>' +
        '<div class="group"><label class="r slr" for="nsens"><span class="l">Slopes we can instrument</span><input type="range" id="nsens" min="1" max="15" step="1"><output id="o-n"></output></label></div>' +
        '<div class="sec-h">Ranked slopes <small>tap to fly there</small></div>' +
        '<div class="group rlist" id="list"></div>' +
        '<details class="disc"><summary>' + icon('chev-r') + 'Tune the weights</summary><div class="in">' +
        '<div class="group">' + slider('coh', 'Coherence floor', 0, 0.8, 0.01, 'coherenceFloor') + slider('msig', 'Extra motion filter (×σ)', 0, 15, 0.5, 'motionSigma') +
        slider('ob', 'Opposing-neighbour bonus', 1, 3, 0.05, 'opposingBonus') + slider('ib', 'Ice-over-rock bonus', 1, 3, 0.05, 'iceBonus') +
        slider('wp', 'People weight', 0, 1, 0.01, 'wPop') + slider('ws', 'Season weight', 0, 2, 0.05, 'wSeason') + '</div>' +
        '<button class="btn" type="button" id="reset">Reset to the pre-registered values</button></div></details>' +
        '<p class="note">Truth (' + esc(D.truthKind) + ') is used only to find where the real source landed in the ranking, never to rank.</p>' + dataSlot() + '</div>';
    } else if (S.ch === 4) {
      html = '<div class="' + cls + '"><p class="cap">' + icon('clock') + '26 August, 08:37 onward</p>' +
        '<h2 class="title">The wave against the warning</h2>' +
        '<p>Slow radar picks the valley; a fast ground sensor on that slope does the rest. The tripwire only exists if the valley made the watch list in the last pass before the collapse.</p>' +
        '<div class="cta"><button type="button" class="btn primary large" id="cta-play" data-label="Run the race">' + icon('play') + 'Run the race</button><span class="hint">or press <kbd>Space</kbd></span></div>' +
        '<div class="callout" id="tripnote"></div>' +
        '<div class="card chart" id="race"></div>' +
        '<div class="tbl"><table id="vtable"></table></div>' +
        '<div class="sec-h">Tripwire delays <small>minutes</small></div>' +
        '<div class="group">' + delay('record', 'Sensor records the fall', 0, 5, 0.5) + delay('recognise', 'Recognise a collapse, not a quake', 0, 20, 0.5) +
        delay('confirm', 'Confirm it can flood', 0, 15, 0.5) + delay('broadcast', 'Sound the sirens', 0, 15, 0.5) + '</div>' +
        '<div class="sec-h">Minute by minute</div><div class="group log" id="log"></div>' +
        '<p class="note" id="race-note"></p>' + dataSlot() + '</div>';
    } else if (S.ch === 5) {
      var pass = sc.pass || {};
      var ranks = (pass.ranks || []).map(function (r, i) { return (r ? '#' + r : 'not ranked') + ' (' + winLabel(winById(pass.windows[i]) || { start: '', end: '' }) + ')'; }).join(' and ');
      html = '<div class="' + cls + '"><p class="cap">' + icon('seal') + 'Read off the real data</p>' +
        '<h2 class="title">What the replay says</h2>' +
        '<p class="answer" id="answer"></p>' +
        '<div><span class="badge ' + (pass.passed ? 'pass' : 'fail') + '">' + icon(pass.passed ? 'check' : 'warn') + 'Exact-slope test ' + (pass.passed ? 'passed' : 'not passed') + '</span>' + (function () { var z = D.sz && D.sz.sites.langtang && D.sz.sites.langtang.rows.find(function (x) { return x.zone_km === 5 && x.top_n === 10; }); if (!z || !z.last_two_covered) return ''; var lr = z.last_two_best_rank[z.last_two_best_rank.length - 1]; return ' <span class="badge pass">' + icon('check') + (lr && lr <= z.top_n ? 'Danger zone on the list in the last pass (#' + lr + ')' : 'Danger zone on the list in one of the last two passes') + '</span>'; })() + '</div>' +
        '<p class="sub">The test: ' + esc(pass.rule || '') + '. Result: ' + esc(ranks) + '.</p>' +
        '<div class="group" id="answer-kv"></div>' +
        '<div class="sec-h">The season was primed</div>' +
        '<div class="card chart"><div id="season"></div><p class="note" id="season-note" style="margin-top:8px"></p></div>' +
        '<div class="sec-h">Backtest on other events <small>the real test</small></div><div class="card" id="backtest"></div>' +
        '<div class="sec-h">Limits, said plainly</div><div class="group"><ul class="plist" id="limits"></ul></div>' +
        '<div class="sec-h">What is real here</div><div class="group"><ul class="plist ok" id="realist"></ul></div>' +
        dataSlot() + '</div>';
    }
    body.innerHTML = html;
    bindChapter();
  }

  function bindRanges(store, digits) {
    all('#chapbody input[type=range][data-key]').forEach(function (inp) {
      var out = inp.nextElementSibling;
      fillRange(inp); out.textContent = (+inp.value).toFixed(digits);
      inp.addEventListener('input', function () { store[inp.dataset.key] = +inp.value; out.textContent = (+inp.value).toFixed(digits); fillRange(inp); update(false); });
    });
  }
  function bindChapter() {
    bindAtlas();
    var dc = $('datacard'); if (dc) dc.addEventListener('toggle', function () { S.dataOpen = dc.open; if (dc.open) { dataKey = ''; paintData(); } });
    var cta = $('cta-play'); if (cta) cta.addEventListener('click', function () { S.playing ? stop() : play(); });
    if (S.ch === 2) {
      all('#bands button').forEach(function (b) {
        b.addEventListener('click', function () { segSelect($('bands'), b); paintBandNote(b.dataset.band); });
      });
      segKeys($('bands'));
      paintBandNote('L');
    }
    if (S.ch === 3) {
      ['sigma', 'dir', 'opp', 'ice', 'people', 'season'].forEach(function (k) { $('r-' + k).addEventListener('change', function (e) { S.rules[k] = e.target.checked; update(false); }); });
      var ns = $('nsens'); ns.value = S.w.nSensors; fillRange(ns);
      ns.addEventListener('input', function () { S.w.nSensors = +ns.value; fillRange(ns); update(false); });
      bindRanges(S.w, 2);
      $('reset').addEventListener('click', function () {
        var n = S.w.nSensors; S.w = Object.assign({}, S.w0, { nSensors: n });
        Object.keys(S.rules).forEach(function (k) { S.rules[k] = true; });
        var open = document.querySelector('details.disc').open; renderChapter(); document.querySelector('details.disc').open = open; update(false);
      });
    }
    if (S.ch === 4) {
      bindRanges(S.d, 1);
      var log = $('log');
      (D.replay.events || []).forEach(function (e) { var d = el('div', 'r', '<i>' + esc(e.npt || clockAt(e.t_min)) + '</i><span>' + esc(e.label) + (e.url ? ' <a class="cite" href="' + esc(e.url) + '" target="_blank" rel="noopener" title="' + esc(e.quote || '') + '">' + esc(e.src || 'source') + '</a>' : cite(e.source)) + '</span>'); d.dataset.t = e.t_min; log.appendChild(d); });
      $('race-note').innerHTML = esc(D.replay.note || '') + ((D.replay.villages || []).some(function (v) { return v.arrival_kind !== 'gauge' && v.reported; }) ? ' * Another time is reported for this place (hover the row); the two disagree.' : '');
    }
  }

  function paintBandNote(band) {
    var n = $('bandnote'); if (!n) return;
    var ctl = D.control;
    if (band === 'C') { n.className = 'callout'; n.innerHTML = icon('info') + '<div>' + ((ctl && ctl.status === 'done') ? esc(ctl.summary) : esc((ctl && ctl.note) || 'The Sentinel-1 control is pending.') + (D.context.sah ? ' ' + esc(D.context.sah.text) + cite(D.context.sah.source) : '')) + '</div>'; }
    else { n.className = 'callout'; n.innerHTML = icon('radar') + '<div>L-band (24 cm wavelength) keeps its lock on ice and wet snow far better than C-band (5.6 cm). That is why NISAR can follow this slope at all.</div>'; }
  }

  function paintChapterLive() {
    if (S.ch === 2) paintCreep();
    if (S.ch === 3) { paintRanking(); paintRule(); }
    if (S.ch === 4) paintRace();
    if (S.ch === 5) paintAnswer();
    paintData();
  }

  function paintCreep() {
    var box = $('creep'); if (!box) return;
    var preIds = preWindows().map(function (w) { return w.id; }); var rows = (D.series || []).filter(function (r) { return preIds.indexOf(r.id) >= 0; });
    var meena = {}; (D.context.meena || []).forEach(function (m) { meena[m.id] = m.disp_m; });
    var W = 312, G = 26, H = 140, padB = 30, n = rows.length, bw = (W - G) / Math.max(n, 1);
    var vals = rows.map(function (r) { return Math.max(r.offsets_p90_m || 0, meena[r.id] || 0); });
    var vmax = Math.max(0.4, Math.max.apply(null, vals)) * 1.2;
    var Y = function (v) { return H - Math.max(0, v) / vmax * (H - 16); };
    var svg = '<svg viewBox="0 0 ' + W + ' ' + (H + padB) + '" role="img" aria-label="Downhill motion of the fastest tenth of the detachment zone per NISAR pass">';
    svg += '<defs><pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="2" height="5" fill="' + COL.label3 + '"/></pattern></defs>';
    [0.5, 1].forEach(function (g) { if (g < vmax) svg += '<line x1="' + G + '" x2="' + W + '" y1="' + Y(g) + '" y2="' + Y(g) + '" stroke="' + COL.sep + '"/><text x="0" y="' + (Y(g) + 3) + '">' + g + ' m</text>'; });
    rows.forEach(function (r, i) {
      var v = r.offsets_p90_m, med = r.offsets_median_m, x = G + i * bw + 5, w = bw - 10;
      var lost = !(D.scorecard.windows[r.id] || {}).source_rank;
      var on = S.mode === 'window' && S.win === r.id;
      var y = v == null ? H : Y(v);
      svg += '<g class="bar" data-win="' + r.id + '" tabindex="0" role="button" aria-label="Load pass ' + winLabel(r) + '"><title>' + winLabel(r) + ': fastest tenth ' + (v == null ? '—' : v.toFixed(2) + ' m') + ', median ' + (med == null ? '—' : med.toFixed(2) + ' m') + (meena[r.id] != null ? ', Meena ' + meena[r.id].toFixed(2) + ' m' : '') + (lost ? ' · slope not ranked this pass' : '') + '</title>';
      svg += '<rect x="' + (x - 4) + '" y="0" width="' + (w + 8) + '" height="' + (H + padB) + '" fill="transparent"/>';
      svg += '<rect class="b" x="' + x + '" y="' + y + '" width="' + w + '" height="' + (H - y) + '" rx="4" fill="' + (on ? COL.accent : COL.teal) + '" opacity="' + (lost ? 0.4 : 1) + '"/>';
      if (lost) svg += '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + (H - y) + '" rx="4" fill="url(#hatch)"/>';
      if (med != null && med > 0) svg += '<line x1="' + (x + 4) + '" x2="' + (x + w - 4) + '" y1="' + Y(med) + '" y2="' + Y(med) + '" stroke="#fff" stroke-opacity=".9" stroke-width="2" stroke-linecap="round"/>';
      if (meena[r.id] != null) { var my = Y(meena[r.id]), cx = x + w / 2; svg += '<path d="M' + cx + ' ' + (my - 5.5) + 'L' + (cx + 5.5) + ' ' + my + 'L' + cx + ' ' + (my + 5.5) + 'L' + (cx - 5.5) + ' ' + my + 'Z" fill="#fff" stroke="rgba(0,0,0,.6)" stroke-width="1.3"/>'; }
      svg += '<text class="val" x="' + (x + w / 2) + '" y="' + (Math.min(y, meena[r.id] != null ? Y(meena[r.id]) - 4 : y) - 6) + '" text-anchor="middle"' + (on ? ' style="fill:' + COL.accent + '"' : '') + '>' + (v == null ? '—' : v.toFixed(2)) + '</text>';
      svg += '<text x="' + (x + w / 2) + '" y="' + (H + 14) + '" text-anchor="middle"' + (on ? ' style="fill:' + COL.label + ';font-weight:600"' : '') + '>' + fmtDate(r.start) + '</text><text x="' + (x + w / 2) + '" y="' + (H + 26) + '" text-anchor="middle">' + r.days + ' d</text></g>';
    });
    svg += '<line x1="' + G + '" x2="' + W + '" y1="' + H + '" y2="' + H + '" stroke="' + COL.label4 + '"/></svg>';
    box.innerHTML = svg + '<div class="legend-row"><span><i style="background:' + COL.teal + '"></i>Fastest tenth</span><span><i style="background:' + COL.teal + ';box-shadow:inset 0 4px 0 -2px #fff"></i>Median</span><span><svg width="11" height="11" viewBox="-6 -6 12 12" style="vertical-align:-1px;margin-right:5px"><path d="M0 -5L5 0L0 5L-5 0Z" fill="#fff" stroke="rgba(0,0,0,.6)" stroke-width="1.2"/></svg>Meena 2026, crown median</span><span><i style="background:' + COL.accent + '"></i>On the map</span></div>';
    all('.bar', box).forEach(function (g) {
      var pick = function () { S.mode = 'window'; S.win = g.dataset.win; update(true); };
      g.addEventListener('click', pick);
      g.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); pick(); } });
    });
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
    if (S.sel && !cur.rows.some(function (x) { return (x.f._orig || x.f).properties.id === S.sel; })) S.sel = null;
    var r = cur.srcRank, n = cur.n, N = S.w.nSensors;
    var rn = $('rank-n'); var from = parseInt((rn.textContent || '').replace('#', ''), 10);
    if (r && isFinite(from) && from !== r) tween(from, r, 450, function (v) { rn.textContent = '#' + Math.round(v); }); else rn.textContent = r ? '#' + r : '—';
    $('rank-of').textContent = r ? 'of ' + n + ' ranked slopes in ' + winLabel(activeWindow()) + ' is the source valley' : (cur.nearCount ? 'The source valley scores zero under these settings' : 'No moving patch within ' + HIT_KM + ' km of the source in this pass');
    $('rank').className = 'rank ' + (r ? (r <= N ? 'in' : 'out') : 'none');
    $('strip-n').textContent = '#' + n;
    $('cut').style.width = Math.min(100, N / Math.max(1, n) * 100) + '%';
    var pin = $('pin'), pos = r ? (n > 1 ? (r - 1) / (n - 1) * 100 : 0) : 100;
    pin.style.left = pos + '%'; pin.style.opacity = r ? 1 : 0.25; pin.className = 'pin' + (pos < 14 ? ' l' : pos > 86 ? ' r' : '');
    $('pinlab').textContent = r ? 'Source valley' : 'Not ranked';
    $('o-n').textContent = N;
    var v = $('verdict');
    if (!r) { v.className = 'callout bad'; v.innerHTML = icon('warn') + '<div>With these settings the source valley is not on the list at all.</div>'; }
    else if (r <= N) { v.className = 'callout ok'; v.innerHTML = icon('check') + '<div>The top ' + N + ' slopes get a ground sensor. The source valley is one of them' + (r > 1 ? ', with ' + (r - 1) + ' false alarm' + (r > 2 ? 's' : '') + ' ranked above it.' : ', ranked first.') + '</div>'; }
    else { v.className = 'callout bad'; v.innerHTML = icon('warn') + '<div>The top ' + N + ' slopes get a ground sensor. The source valley is not one of them: ' + (r - 1) + ' slopes rank above it.</div>'; }
    var list = $('list'); var before = {};
    all('.li', list).forEach(function (c) { before[c.dataset.id] = c.getBoundingClientRect().top; });
    list.innerHTML = '';
    var top = cur.ranked.slice(0, 12), smax = top.length ? top[0].s : 1;
    var srcRow = cur.hit && cur.ranked.indexOf(cur.hit) >= 12 ? cur.hit : null;
    top.concat(srcRow ? [srcRow] : []).forEach(function (row) {
      var p = (row.f._orig || row.f).properties, isSrc = cur.hit === row;
      var d = el('div', 'li' + (isSrc ? ' src' : '') + (row.rank > N ? ' dead' : ''));
      d.dataset.id = p.id; d.tabIndex = 0; d.setAttribute('role', 'button');
      var off = p.channel === 'offsets';
      d.innerHTML = '<span class="rk">' + row.rank + '</span><span class="nm">' + (isSrc ? 'Source valley' : (p.channel_name ? esc(p.channel_name) : 'Slope') + ', ' + Math.round(p.elev_m).toLocaleString() + ' m') +
        '<span class="chip ' + (off ? 'off' : 'ph') + '">' + (off ? 'Offsets' : 'Phase') + '</span></span>' +
        '<span><span class="bar" style="width:' + Math.max(4, row.s / smax * 100) + '%"></span></span><span class="d">' + Number(p.dist_truth_km).toFixed(1) + ' km</span>';
      if (S.sel === p.id) d.classList.add('sel');
      var fly = function () { S.sel = p.id; map.easeTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 12.2), pitch: 55, duration: 900, padding: pad() }); paintChapterLive(); };
      d.addEventListener('click', fly);
      d.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); fly(); } });
      list.appendChild(d);
    });
    if (!reduce) all('.li', list).forEach(function (c) {
      var b = before[c.dataset.id]; if (b == null) return;
      var dy = b - c.getBoundingClientRect().top; if (!dy) return;
      c.style.transform = 'translateY(' + dy + 'px)'; c.style.transition = 'none';
      requestAnimationFrame(function () { c.style.transition = 'transform .5s cubic-bezier(.2,.9,.1,1)'; c.style.transform = ''; });
    });
  }

  function paintRace() {
    var tn = $('tripnote'); if (!tn) return;
    var A = D.replay.alert_min, T = tripT(), armed = tripwireArmed(), N = S.w.nSensors;
    var lw = lastPre(), r = rankWindow(BLOBS[lw.id]);
    if (armed) { tn.className = 'callout ok'; tn.innerHTML = icon('bell') + '<div>In the last pass before the collapse the slope ranked <strong>#' + r.srcRank + '</strong>, inside the top ' + N + '. A sensor is on that slope, and its siren sounds <strong>' + T.toFixed(1) + ' min</strong> after the fall.</div>'; }
    else { tn.className = 'callout bad'; tn.innerHTML = icon('warn') + '<div>In the last pass before the collapse the slope ranked <strong>' + (r.srcRank ? '#' + r.srcRank : 'nowhere') + '</strong>, outside the top ' + N + '. No sensor, so no tripwire: only the real SMS at ' + A.toFixed(1) + ' min.</div>'; }
    var vs = (D.replay.villages || []).filter(function (v) { return v.arrival_min != null; });
    var tb = '<thead><tr><th>Place</th><th>Wave</th><th>Real alert</th><th>Tripwire</th></tr></thead><tbody>';
    vs.forEach(function (v) {
      var real = v.arrival_min - A, trip = armed ? v.arrival_min - T : null, hit = S.mode === 'event' && S.t >= v.arrival_min;
      var gauged = v.arrival_kind === 'gauge';
      var rep = (v.reported || []).map(function (x) { return x.npt + ' reported (' + (x.source_url.indexOf('11167819') >= 0 ? 'Hassan 2026' : 'source') + ')'; }).join('; ');
      tb += '<tr class="' + (hit ? 'hit' : '') + '" title="' + esc(v.name) + ': arrival ' + esc(v.arrival_kind || '') + (rep ? '. Also ' + esc(rep) : '') + '"><td>' + esc(v.name) + (rep && !gauged ? ' <small>*</small>' : '') + '</td><td>' + (gauged ? '' : '≈') + '+' + v.arrival_min.toFixed(1) + (gauged ? ' <small>gauge</small>' : '') + '</td>' +
        '<td class="' + (real < 0 ? 'late' : 'gain') + '">' + signed(real, 0) + ' min</td>' +
        '<td class="' + (trip == null ? 'none' : trip < 0 ? 'late' : 'gain') + '">' + (trip == null ? '—' : signed(trip, 0) + ' min') + '</td>' +
        '</tr>';
    });
    $('vtable').innerHTML = tb + '</tbody>';
    var W = 312, rowH = 24, H = vs.length * rowH + 37, x0 = 86, x1 = W - 6, tmax = TL.tmax;
    var X = function (t) { return x0 + Math.min(1, t / tmax) * (x1 - x0); };
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Wave arrival against alert times per place">';
    var yb = 8 + vs.length * rowH;
    svg += '<rect x="' + x0 + '" y="2" width="' + (X(A) - x0) + '" height="' + (yb - 2) + '" rx="6" fill="' + COL.red + '" fill-opacity=".07"/>';
    vs.forEach(function (v, i) {
      var y = 8 + i * rowH;
      svg += '<text x="0" y="' + (y + 11) + '" style="font-size:11px;fill:' + COL.label + '">' + esc(v.name) + '</text>';
      svg += '<line x1="' + x0 + '" x2="' + x1 + '" y1="' + (y + 7) + '" y2="' + (y + 7) + '" stroke="' + COL.sep + '"/>';
      var hit = S.mode === 'event' && S.t >= v.arrival_min;
      svg += '<circle cx="' + X(v.arrival_min) + '" cy="' + (y + 7) + '" r="' + (hit ? 6 : 5) + '" fill="' + (hit ? COL.red : COL.card) + '" stroke="' + COL.red + '" stroke-width="2"/>';
    });
    svg += '<line x1="' + X(A) + '" x2="' + X(A) + '" y1="2" y2="' + yb + '" stroke="' + COL.label + '" stroke-width="1.5"/><text x="' + (X(A) + 4) + '" y="' + (yb + 13) + '" style="fill:' + COL.label + ';font-weight:600">SMS ' + A.toFixed(1) + '</text>';
    if (armed) svg += '<line x1="' + X(T) + '" x2="' + X(T) + '" y1="2" y2="' + yb + '" stroke="' + COL.green + '" stroke-width="2"/><text x="' + (X(T) + 4) + '" y="' + (yb + 13) + '" style="fill:' + COL.ok + ';font-weight:600">Tripwire ' + T.toFixed(1) + '</text>';
    if (S.mode === 'event') svg += '<line x1="' + X(S.t) + '" x2="' + X(S.t) + '" y1="0" y2="' + yb + '" stroke="' + COL.red + '" stroke-opacity=".7" stroke-dasharray="2 3"/>';
    svg += '<text x="' + x0 + '" y="' + (H - 1) + '">0</text><text x="' + X(30) + '" y="' + (H - 1) + '" text-anchor="middle">30 min</text><text x="' + x1 + '" y="' + (H - 1) + '" text-anchor="end">' + tmax + '</text></svg>';
    $('race').innerHTML = svg;
    all('#log .r').forEach(function (d) { d.classList.toggle('on', S.mode === 'event' && S.t >= +d.dataset.t); });
  }

  function paintAnswer() {
    var a = $('answer'); if (!a) return;
    var sc = D.scorecard, lp = lastPre(), w = sc.windows[lp.id] || {};
    var r = w.source_rank;
    var pass = sc.pass || {}, pw = (pass.windows || []).map(winById);
    var detail = (pass.ranks || []).map(function (x, i) { return (x ? 'ranked <em>#' + x + '</em>' : '<em>was not ranked</em>') + ' in ' + (pw[i] ? winLabel(pw[i]) : 'a pass'); }).join(' and ');
    a.innerHTML = (r && r <= (D.v2 && D.v2.watch_list ? D.v2.watch_list.top_n : 10) ? 'Yes, for the valley. In the last pass before the collapse the source was <em>#' + r + '</em> of ' + w.n_ranked + ' moving slopes. ' : '') +
      (pass.passed ? 'The strict exact-slope test passed too: the source ' : 'The strict exact-slope test did not pass: the source ') + detail + '.';
    var kv = [
      ['Source rank, last pass (rules on)', r ? '#' + r + ' of ' + w.n_ranked : '—'],
      ['Raw-offset baseline (Meena-style)', w.baseline_rank ? '#' + w.baseline_rank + ' of ' + w.n_offsets : '—'],
      ['Sentinel-1 C-band control', D.control && D.control.status === 'done' ? (D.control.sentinel1_rank_per_window[lp.id] == null ? 'Not ranked' : '#' + D.control.sentinel1_rank_per_window[lp.id]) : 'Pending'],
      ['With troposphere screens removed', (function () { var x = D.context.sensitivity && D.context.sensitivity.windows[lp.id]; return x && x.tropo_rank ? '#' + x.tropo_rank + ' of ' + x.tropo_n : '—'; })()],
      ['Real alert after the collapse', D.replay.alert_min.toFixed(1) + ' min'],
      ['Data on ASF before the collapse', lp.asf_processed ? fmtDate(lp.asf_processed.slice(0, 10), true) : '—']
    ];
    $('answer-kv').innerHTML = kv.map(function (k) { return '<div class="r"><span class="l">' + esc(k[0]) + '</span><span class="v">' + esc(k[1]) + '</span></div>'; }).join('');
    var e5 = D.context.era5, sb = $('season');
    if (e5 && sb) {
      var ys = e5.years.filter(function (y) { return y[1] != null; }), W = 312, H = 70;
      var lo = Math.min.apply(null, ys.map(function (y) { return y[1]; })) - 0.3, hi = Math.max.apply(null, ys.map(function (y) { return y[1]; })) + 0.1, bw = W / ys.length;
      var svg = '<svg viewBox="0 0 ' + W + ' ' + (H + 14) + '" role="img" aria-label="Mean temperature 1 July to 25 August at the source, 1991 to 2026">';
      ys.forEach(function (y, i) {
        var h = (y[1] - lo) / (hi - lo) * H, is26 = y[0] === 2026;
        svg += '<rect x="' + (i * bw + 1) + '" y="' + (H - h) + '" width="' + (bw - 2) + '" height="' + h + '" rx="2" fill="' + (is26 ? COL.red : COL.fill3) + '"><title>' + y[0] + ': ' + y[1].toFixed(2) + ' °C</title></rect>';
      });
      svg += '<text x="0" y="' + (H + 12) + '">' + ys[0][0] + '</text><text x="' + W + '" y="' + (H + 12) + '" text-anchor="end" style="fill:' + COL.bad + ';font-weight:600">2026</text></svg>';
      sb.innerHTML = svg;
      $('season-note').innerHTML = esc(e5.span + ' at the source: ' + e5.t2026.toFixed(1) + ' °C, ' + signed(e5.anom, 1) + ' °C above the 1991–2020 mean (z ' + signed(e5.z, 1) + '), the ' + e5.ordinal + ' warmest of ' + e5.n + ' years, at ' + Math.round(e5.elev_m).toLocaleString() + ' m. Snowfall was ' + Math.round(e5.snow_pct) + '% of normal, a figure that depends on the elevation used.') + cite('era5') + ' The same heat and snow drought as ' + cite('hassan').trim() + '.';
    }
    paintBacktest();
    $('limits').innerHTML = (D.context.limits || []).map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('');
    $('realist').innerHTML = (D.context.real || []).map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('');
  }

  /* ---------------------------------------------------------------- the rule, term by term */
  var WHY = {
    snr: 'Counts only motion the radar measured clearly. Dividing by the patch’s own noise puts a clean 5 cm above a noisy 50 cm.',
    dir: 'A failing slope moves as one block; noise points every way. 1 means every pixel in the patch moves the same way.',
    opp: 'Two neighbouring blocks moving opposite ways mark a crack opening between them.',
    ice: 'Glacier ice resting on steep rock is where ice–rock avalanches start.',
    people: 'Risk is hazard times exposure. Logarithmic, so a big town does not drown out the motion signal.',
    season: 'A warm, low-snow summer weakens ice. It is the same number for every patch in a pass, so it never changes the order within a pass.'
  };
  function fmtTime(t) { return new Date(t).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC'; }
  function ruleCard() {
    var C = D.config, g = C.grid, kmx = (g.x_edges[1] - g.x_edges[0]) / 1000, kmy = (g.y_edges[1] - g.y_edges[0]) / 1000;
    var pr = D.prov && D.prov.config;
    return '<div class="card rulecard">' +
      '<div class="rc-h">' + icon('sliders') + '<b>The rule</b></div>' +
      '<ol class="rc-steps">' +
      '<li><b>Find moving patches.</b> In each NISAR pass, join neighbouring ' + g.cell_m + ' m cells that move: pixel offsets above ' + C.offsets.motion_threshold_sigma + '× the scene noise, or phase motion beyond ±' + C.phase.blob_threshold_mm + ' mm, at least ' + C.blobs.min_cells + ' cells. The box is ' + kmx.toFixed(0) + ' × ' + kmy.toFixed(0) + ' km.</li>' +
      '<li><b>Score each patch</b> by multiplying the six factors below.</li>' +
      '<li><b>Watch the top ' + C.pass_condition.top_n + '.</b> Those valleys get a closer look and, in the what-if, a ground sensor.</li>' +
      '<li><b>Count a hit</b> when a patch within ' + C.truth.hit_radius_km + ' km of the mapped detachment, draining to the same channel, is in the top ' + C.pass_condition.top_n + '. The map outline is used only for this check.</li>' +
      '</ol>' +
      '<div class="fx" id="fx"></div>' +
      '<button type="button" class="btn rule-more" data-open="rule">' + icon('info') + 'Where the rule comes from, what it would have done, and whether it held</button>' +
      '<p class="note">Fixed before any result was computed, then run unchanged on a second disaster: it kept the 5 km zone around that source on its top-10 list.</p>' +
      '</div>';
  }
  function paintRule() {
    var box = $('fx'); if (!box || !cur) return;
    var row = null;
    if (S.sel) row = cur.rows.find(function (x) { return (x.f._orig || x.f).properties.id === S.sel; });
    if (!row) row = cur.hit || cur.ranked[0];
    if (!row) { box.innerHTML = '<p class="note">No moving patches in this pass.</p>'; return; }
    var p = row.p, w = weights(), C = D.config, off = p.channel === 'offsets';
    var sig = S.rules.sigma ? p.sigma_m : 1, dc = S.rules.dir ? p.dir_coherence : 1;
    var mm = function (v) { var x = v * 1000; return off ? Number(v).toFixed(2) + ' m' : (x < 10 ? x.toFixed(2) : Math.round(x)) + ' mm'; };
    var f = [
      ['snr', 'Motion ÷ its noise', (off ? 'downhill ' : 'along the radar line of sight ') + mm(p.motion_m) + (S.rules.sigma ? ' ÷ ' + mm(p.sigma_m) : ' (noise rule off)'), p.motion_m / sig],
      ['dir', '× Moves one way', S.rules.dir ? 'direction coherence of the offsets' : 'rule off', dc],
      ['opp', '× Neighbours pull apart', (p.opposing_sign ? 'yes' : 'no') + ', checked within ' + C.blobs.opposing_sign_radius_km + ' km' + (S.rules.opp ? '' : ' (rule off)'), p.opposing_sign ? w.opposingBonus : 1],
      ['ice', '× Hanging ice over rock', (p.ice_over_rock ? 'yes' : 'no') + ': ' + Math.round(p.glacier_frac * 100) + '% glacier, ' + Math.round(p.slope_deg) + '° slope (needs ≥ ' + C.blobs.ice_over_rock_min_slope_deg + '°)' + (S.rules.ice ? '' : ' (rule off)'), p.ice_over_rock ? w.iceBonus : 1],
      ['people', '× People below', Math.round(p.people_below || 0).toLocaleString() + ' people within ' + C.blobs.people_buffer_km + ' km of the first ' + C.blobs.people_path_km + ' km downstream', 1 + w.wPop * Math.log10(1 + (p.people_below || 0))],
      ['season', '× Hot season', 'ERA5 heat anomaly ' + Number(p.season_anomaly).toFixed(2), 1 + w.wSeason * (p.season_anomaly - 1)]
    ];
    var gated = p.coherence < w.coherenceFloor ? 'Signal quality ' + Number(p.coherence).toFixed(2) + ' is under the floor of ' + w.coherenceFloor.toFixed(2) + ', so the patch scores 0.' : '';
    var isHit = cur.hit === row;
    box.innerHTML = '<div class="fx-who">' + (isHit ? '<i class="sw-truth"></i><b>Source valley</b>' : '<b>Patch ' + esc(p.id) + '</b>') + ' · ' + (off ? 'pixel offsets' : 'phase') + ' · ' + Number(p.dist_truth_km).toFixed(1) + ' km from the mapped source' +
      (isHit ? '' : ' <button type="button" class="btn plain sm" id="fx-hit">Show the source valley</button>') + '</div>' +
      f.map(function (x) { return '<div class="fx-r"><span class="fx-t">' + x[1] + '<small>' + esc(x[2]) + '</small><em>' + WHY[x[0]] + '</em></span><span class="fx-v">' + x[3].toFixed(2) + '</span></div>'; }).join('') +
      '<div class="fx-r tot"><span class="fx-t">Score' + (gated ? '<small>' + gated + '</small>' : '') + '</span><span class="fx-v">' + (row.s || 0).toFixed(1) + '</span></div>' +
      '<div class="fx-rank">' + (row.rank ? 'Rank <b>#' + row.rank + '</b> of ' + cur.n + ' in ' + winLabel(activeWindow()) + (row.rank <= S.w.nSensors ? ' · on the watch list' : ' · not on the watch list') : 'Not ranked in ' + winLabel(activeWindow())) + '</div>';
    var b = $('fx-hit'); if (b) b.addEventListener('click', function () { S.sel = null; paintChapterLive(); });
  }

  /* ---------------------------------------------------------------- data behind each view */
  function dataSlot() { return '<details class="disc data" id="datacard"' + (S.dataOpen ? ' open' : '') + '><summary>' + icon('chev-r') + 'Data behind this view<small id="dc-n"></small></summary><div class="in" id="dc-body"></div></details>'; }
  function fileLinks(list) { return '<div class="files">' + list.map(function (f) { return '<a class="file" href="' + DATA + f + '" target="_blank" rel="noopener">' + icon('list') + esc(f) + '</a>'; }).join('') + '</div>'; }
  function addMin(hhmm, m) { var t = +hhmm.slice(0, 2) * 60 + +hhmm.slice(3) + m; return ('0' + Math.floor(t / 60)).slice(-2) + ':' + ('0' + t % 60).slice(-2); }
  function tbl(head, rows, cls) { return '<div class="dwrap"><table class="dtbl' + (cls ? ' ' + cls : '') + '"><thead><tr>' + head.map(function (h) { return '<th>' + h + '</th>'; }).join('') + '</tr></thead><tbody>' + rows.join('') + '</tbody></table></div>'; }
  function gaugeTables() {
    return ((D.prov && D.prov.gauges) || []).map(function (g) {
      var rows = g.rows_0730_1000.slice(-4), last = g.last_row.npt, gone = [addMin(last, 10), addMin(last, 20)];
      return '<div class="gauge"><div class="g-h"><b>' + esc(g.name) + '</b> · DHM station ' + g.station_id + ' · warning level ' + g.warning_m + ' m</div>' +
        tbl(['Time (NPT)', 'River stage', 'Of warning'], rows.map(function (r) { return '<tr' + (r.npt === last ? ' class="last"' : '') + '><td>' + r.npt + '</td><td>' + r.stage_m.toFixed(2) + ' m</td><td>' + Math.round(r.stage_m / g.warning_m * 100) + '%</td></tr>'; })
          .concat(gone.map(function (t) { return '<tr class="gone"><td>' + t + '</td><td colspan="2">no report</td></tr>'; }))) +
        '<p class="src">' + esc(g.file) + ' · ' + g.n_rows_day + ' readings that day</p></div>';
    }).join('');
  }
  var dataKey = '';
  function paintData() {
    var body = $('dc-body'); if (!body) return;
    var w = activeWindow(), key = S.ch + '|' + (w && w.id) + '|' + S.sel + '|' + JSON.stringify(S.w) + JSON.stringify(S.rules) + (cur ? cur.n : '');
    if (key === dataKey && body.innerHTML) return;
    dataKey = key;
    var html = '', files = [];
    if (S.ch === 1 || S.ch === 4) {
      html += '<p class="note">Ten-minute river-stage readings from Nepal’s DHM river-watch service, as archived in Zenodo 22679444. Every gauge on the path went silent when the wave reached it, and each one’s last reading was below its warning level.</p>' + gaugeTables();
      if (S.ch === 4) {
        var G = D.replay.gauges || [];
        html += '<div class="g-h"><b>How arrival times are set</b></div>' + tbl(['Gauge', 'Path km', 'Last report', 'Silent by', 'Used'], G.map(function (g) {
          return '<tr><td>' + esc(g.name) + '</td><td>' + g.km.toFixed(1) + '</td><td>+' + g.arrival_lo.toFixed(1) + '</td><td>+' + g.arrival_hi.toFixed(1) + '</td><td>+' + g.arrival_min.toFixed(1) + '</td></tr>'; })) +
          '<p class="src">Minutes after the collapse at ' + esc(D.replay.collapse_npt || '') + ' NPT. ' + esc(D.replay.note || '') + '</p>';
      }
      files = ['replay.json', 'provenance.json'];
    } else if (S.ch === 2) {
      var gr = ((D.prov && D.prov.granules) || []).filter(function (g) { return g.key === 'GUNW_' + w.id || g.key === 'GOFF_' + w.id; });
      var ss = (D.series || []).find(function (x) { return x.id === w.id; }) || {};
      html += '<p class="note">The two NISAR L2 products behind the pass on the map (' + winLabel(w) + '), read from ASF. Only the study box was read from each file.</p>' +
        tbl(['Product', 'Scene', 'Release', 'On ASF'], gr.map(function (g) {
          return '<tr><td>' + esc(g.level) + '</td><td class="scene" title="' + esc(g.scene) + '"><a href="' + esc(g.url) + '" target="_blank" rel="noopener">' + esc(g.scene.replace(/^NISAR_L2_PR_/, '').slice(0, 34)) + '…</a></td><td>' + esc(g.crid) + '</td><td>' + esc((g.asf_processed || '').slice(0, 16).replace('T', ' ')) + '</td></tr>'; })) +
        '<div class="g-h"><b>Inside the mapped detachment zone, this pass</b></div>' +
        tbl(['Measure', 'Value'], [
          ['Grid cells in the zone', ss.cells], ['Cells with usable offsets', ss.offsets_valid_share != null ? Math.round(ss.offsets_valid_share * 100) + '%' : '—'],
          ['Median downhill motion', ss.offsets_median_m != null ? ss.offsets_median_m.toFixed(3) + ' m' : '—'], ['Fastest tenth (90th percentile)', ss.offsets_p90_m != null ? ss.offsets_p90_m.toFixed(3) + ' m' : '—'],
          ['Standard error of the median', ss.offsets_sem_m != null ? ss.offsets_sem_m.toFixed(3) + ' m' : '—'], ['Offset noise on stable ground', w.offsets_noise_m != null ? w.offsets_noise_m.toFixed(3) + ' m (' + esc(w.offsets_layer || '') + ')' : '—'],
          ['Phase noise on stable ground', w.phase_noise_mm != null ? w.phase_noise_mm.toFixed(1) + ' mm' : '—'], ['Glacier area lost to low signal', w.ice_lost_share != null ? Math.round(w.ice_lost_share * 100) + '%' : '—']
        ].map(function (r) { return '<tr><td>' + r[0] + '</td><td>' + (r[1] == null ? '—' : r[1]) + '</td></tr>'; }));
      files = ['windows.json', 'source_series.json', 'provenance.json'];
    } else if (S.ch === 3 && cur) {
      var row = (S.sel && cur.rows.find(function (x) { return (x.f._orig || x.f).properties.id === S.sel; })) || cur.hit || cur.ranked[0];
      var p = row ? (row.f._orig || row.f).properties : null;
      html += '<p class="note">The ranking is computed in your browser from this file, with the same formula as the pipeline (checked by a test on all ' + D.windows.length + ' passes). It holds ' + (BLOBS[w.id] ? BLOBS[w.id].features.length : '?') + ' patches for ' + winLabel(w) + '.</p>';
      if (p) html += '<div class="g-h"><b>Record for ' + (row === cur.hit ? 'the source valley' : 'patch ' + esc(p.id)) + '</b></div>' +
        tbl(['Field', 'Value'], Object.keys(p).map(function (k) { var v = p[k]; return '<tr><td><code>' + esc(k) + '</code></td><td>' + esc(typeof v === 'number' ? +v.toPrecision(6) : String(v)) + '</td></tr>'; }), 'kv');
      html += '<div class="g-h"><b>Top ' + S.w.nSensors + ' this pass</b></div>' + tbl(['#', 'Patch', 'Kind', 'Motion ÷ noise', 'Score', 'From source'], cur.ranked.slice(0, S.w.nSensors).map(function (r) {
        var q = (r.f._orig || r.f).properties; return '<tr' + (r === cur.hit ? ' class="last"' : '') + '><td>' + r.rank + '</td><td>' + esc(q.id) + '</td><td>' + (q.channel === 'offsets' ? 'offsets' : 'phase') + '</td><td>' + (r.p.motion_m / r.p.sigma_m).toFixed(1) + '</td><td>' + r.s.toFixed(1) + '</td><td>' + Number(q.dist_truth_km).toFixed(1) + ' km</td></tr>'; }));
      files = ['blobs_' + w.id + '.geojson', 'config.json', 'scorecard.json'];
    } else if (S.ch === 5) {
      var cm = (D.prov && D.prov.commits) || [];
      html += '<p class="note">The git history of this project. The rules file was committed before any results were. Commit times are recorded by git on the author’s machine.</p>' +
        tbl(['Commit', 'Time', 'What', 'Rules', 'Results'], cm.map(function (c) { return '<tr><td><code>' + c.short + '</code></td><td>' + esc(c.time.slice(5, 16).replace('T', ' ')) + '</td><td class="wrap">' + esc(c.subject) + '</td><td>' + (c.touches_config ? '✓' : '') + '</td><td>' + (c.adds_results ? '✓' : '') + '</td></tr>'; })) +
        '<div class="g-h"><b>The test, as scored</b></div>' + tbl(['Field', 'Value'], [['Rule', D.scorecard.pass.rule], ['Passes', (D.scorecard.pass.windows || []).join(', ')], ['Ranks', (D.scorecard.pass.ranks || []).map(function (x) { return x == null ? 'not ranked' : '#' + x; }).join(', ')], ['Passed', String(D.scorecard.pass.passed)]]
          .map(function (r) { return '<tr><td>' + r[0] + '</td><td class="wrap">' + esc(r[1]) + '</td></tr>'; }), 'kv');
      files = ['scorecard.json', 'sensitivity.json', 'config.json', 'provenance.json'];
    }
    body.innerHTML = html + fileLinks(files);
    var n = $('dc-n'); if (n) n.textContent = files.length + ' files';
  }
  function dataSheet() {
    var G = (D.prov && D.prov.granules) || [];
    return '<h2 id="d-title">Data</h2><p>Every number on this page is read from the files below at load time. Nothing is typed into the page. Links open the files themselves.</p>' +
      '<h3>Files this page has read (' + LOG.length + ')</h3>' + tbl(['File', 'Size', 'Contents', 'Load'], LOG.map(function (l) { return '<tr><td><a href="' + esc(l.path) + '" target="_blank" rel="noopener">' + esc(l.path.replace(DATA, '')) + '</a></td><td>' + fmtBytes(l.bytes) + '</td><td>' + esc(l.n) + '</td><td>' + l.ms + ' ms</td></tr>'; })) +
      '<h3>NISAR granules read from ASF (' + G.length + ')</h3>' + tbl(['Product', 'Track', 'Dates', 'Release', 'On ASF'], G.map(function (g) {
        return '<tr><td><a href="' + esc(g.url) + '" target="_blank" rel="noopener" title="' + esc(g.scene) + '">' + esc(g.level) + '</a></td><td>' + esc((g.direction || '') + ' ' + (g.track || '')) + '</td><td>' + esc((g.dates || []).join(' → ')) + '</td><td>' + esc(g.crid || '') + '</td><td>' + esc((g.asf_processed || '').slice(0, 10)) + '</td></tr>'; })) +
      '<p class="note">Granule links need a free NASA Earthdata login. The pipeline reads only the study box out of each file (HTTP range requests); see the README to rebuild everything from scratch.</p>';
  }

  /* ---------------------------------------------------------------- see the place (World Labs Atlas) */
  function atlasCard() {
    var V = D.atlas; if (!V) return '';
    var poly = V.outline.map(function (q) { return q.join(','); }).join(' ');
    var path = V.path.length ? 'M' + V.path.map(function (q) { return q.join(' '); }).join('L') : '';
    return '<div class="card atlas-card"><div class="sec-h" style="margin:0 0 8px">See the place <small>before and after</small></div>' +
      '<div class="ba" id="ba" style="--x:50%"><img src="' + DATA + 'atlas/before.jpg" alt="AI visualization of the Langtang source slope before the collapse"><img class="after" src="' + DATA + 'atlas/after.jpg" alt="AI visualization of the same slope after the collapse">' +
      '<svg viewBox="0 0 ' + V.width + ' ' + V.height + '" class="ba-ov" id="ba-ov"><polygon points="' + poly + '" fill="rgba(255,214,10,.16)" stroke="#ffd60a" stroke-width="5"/><path d="' + path + '" fill="none" stroke="#ff453a" stroke-width="7" stroke-linecap="round"/></svg>' +
      '<span class="ba-tag l">Before · summer</span><span class="ba-tag r">After · visualized</span><i class="ba-bar"></i>' +
      '<input type="range" min="0" max="100" value="50" id="ba-r" aria-label="Slide between the view before and after the collapse"></div>' +
      '<label class="ba-opt"><input type="checkbox" class="sw" id="ba-data" checked> Show the data on top <small>UNOSAT outline · routed flood path</small></label>' +
      '<p class="note"><b>' + esc(V.label) + '.</b> ' + esc(V.how) + ' <a href="' + DATA + 'atlas/view.json" target="_blank" rel="noopener">Prompts, seed and camera</a></p></div>';
  }
  function bindAtlas() {
    var r = $('ba-r'); if (!r) return;
    r.addEventListener('input', function () { $('ba').style.setProperty('--x', r.value + '%'); });
    $('ba-data').addEventListener('change', function (e) { $('ba-ov').style.opacity = e.target.checked ? 1 : 0; });
  }

  /* ---------------------------------------------------------------- backtest */
  function btRow(k, x) {
    var pp = x.positive_pass, fa = x.false_alarm || {};
    var test = pp ? (pp.result === 'pass' ? 'Passed' : 'Not passed') + ' (' + pp.ranks.map(function (r, i) { return r ? '#' + r + ' of ' + pp.n_ranked[i] : 'not ranked'; }).join(', then ') + ')' : 'No failure to find';
    var names = (fa.false_alarms || []).map(function (f) { return f.channel_name || 'unnamed'; });
    return '<tr><td class="wrap"><b>' + esc({ budhigandaki: 'Budhi Gandaki', manang: 'Manang', langtang: 'Langtang' }[k] || k) + '</b><br>' + esc((x.role || '').split(' (')[0]) + '</td><td class="wrap">' + esc(test) + '</td><td class="wrap">' + (fa.n_false_alarms != null ? fa.n_false_alarms + ' in ' + fa.n_passes + ' passes' + (names.length ? '<br><small>' + esc(names.join(', ')) + '</small>' : '') : '—') + '</td></tr>';
  }
  function paintBacktest() {
    var box = $('backtest'); if (!box) return;
    var plan = D.btPlan, res = D.bt;
    var head = '<p class="note">The test areas, the truth and the passes were fixed before any radar data from them was read, and the rules ran unchanged. The ' + (D.context.zone_summary ? D.context.zone_summary.zone_km + ' km' : '') + ' scale was set from these two events; every new event tests it again.</p>';
    if (!res || !res.sites) { box.innerHTML = head + '<p class="note">Backtest results not found.</p>'; return; }
    var order = ['budhigandaki', 'manang', 'langtang'].filter(function (k) { return res.sites[k]; });
    var b = res.sites.budhigandaki, zs = D.context.zone_summary;
    box.innerHTML = zoneCallout() +
      '<div class="g-h"><b>The strict test: the exact slope</b> (within ' + (D.btPlan && D.btPlan.sites.budhigandaki ? D.btPlan.sites.budhigandaki.truth.hit_radius_km : 2) + ' km, top ' + res.top_n + ', both of the last two passes)</div>' +
      tbl(['Site', 'Result', 'False alarms'], order.map(function (k) { return btRow(k, res.sites[k]); })) +
      zoneTable() + head +
      '<p class="note">False alarm (exact-slope test): another valley in the top ' + res.top_n + ' in two passes in a row. A list entry never sounds a siren by itself: a siren needs a detected collapse, so a longer list costs sensors, not evacuations. Budhi Gandaki has no mapped outline, so its truth is an approximate scar point.</p>' +
      fileLinks(['backtest.json', 'sensitivity_zone.json']);
  }

  function zoneCallout() {
    var zs = D.context.zone_summary, res = D.bt; if (!zs || !res) return '';
    var b = res.sites.budhigandaki, zb = zs.budhigandaki;
    var meena = (D.context.comparisons || [])[0];
    return '<div class="callout ok">' + icon('check') + '<div><b>On a disaster it had never seen, it found the danger zone.</b> Run unchanged on the Budhi Gandaki rock avalanche of October 2026, Cascade Watch put the ' + zs.zone_km + ' km zone around the source in its top ' + zs.top_n + ' in ' + (zb.last_two_covered === 2 ? 'both' : zb.last_two_covered) + ' of the last two passes before the collapse, and in ' + zb.hits + ' of ' + zb.passes + ' summer passes: about ' + (function (x) { return x >= 1.8 && x <= 2.2 ? 'twice' : x.toFixed(1) + ' times'; })(zb.hits / zb.expected_by_chance) + ' what a random list would catch. ' +
      'It cannot yet pick the exact slope (' + b.positive_pass.ranks.map(function (r) { return r ? '#' + r : 'unranked'; }).join(', then ') + '), which is why it watches valleys, not slopes.</div></div>' +
      (meena ? '<p class="note"><b>For comparison:</b> ' + esc(meena.text) + cite(meena.source) + '</p>' : '');
  }
  function zoneTable() {
    var sz = D.sz; if (!sz) return '';
    var zones = [2, 3, 5, 8, 'tributary'], N = 10;
    var cell = function (site, z) { var r = sz.sites[site].rows.find(function (x) { return x.zone_km === z && x.top_n === N; }); return r ? r.hits + '/' + r.passes + ' <small>(' + r.expected_by_chance.toFixed(1) + ')</small>' : '—'; };
    var vl = function (site) { var v = sz.sites[site].valleys_on_list_per_pass; return v ? v[String(N)] : '—'; };
    return '<div class="g-h"><b>The zone a warning needs</b> (top ' + N + ', same drainage; passes on the list, with what a random list would give)</div>' +
      tbl(['Zone', 'Langtang', 'Budhi Gandaki'], zones.map(function (z) { return '<tr' + (z === 5 ? ' class="last"' : '') + '><td>' + (z === 'tributary' ? 'Whole valley' : z + ' km') + '</td><td>' + cell('langtang', z) + '</td><td>' + cell('budhigandaki', z) + '</td></tr>'; })
        .concat(['<tr><td>Valleys on list</td><td>' + vl('langtang') + '</td><td>' + vl('budhigandaki') + '</td></tr>'])) +
      '<p class="note">Too small a zone is a coin-toss on an approximate point; the whole valley is caught by any list, because half the moving ground is there. The ranking adds value in between.</p>';
  }

  function compareTable() {
    var cs = D.context.comparisons || []; if (!cs.length) return '';
    return '<h3>How it compares</h3>' + tbl(['Study', 'What it found'], cs.map(function (c) { return '<tr><td class="wrap"><b>' + esc(c.who) + '</b></td><td class="wrap">' + esc(c.text) + cite(c.source) + '</td></tr>'; })) +
      '<p class="note">Most published radar precursors were found in hindsight at slopes already known. Cascade Watch ranks every moving slope in a region blind, scores itself against chance, and was tested on a disaster it had not seen.</p>';
  }

  /* ---------------------------------------------------------------- the rule, explained */
  function ruleSheet() {
    var C = D.config, lp = lastPre(), sc = D.scorecard.windows[lp.id] || {}, A = D.replay.alert_min, T = tripT();
    var ev = (D.context.rule_evidence || []).map(function (e) { return '<tr><td class="wrap"><b>' + esc(e.factor) + '</b></td><td class="wrap">' + esc(e.text) + cite(e.source) + '</td></tr>'; });
    var vs = (D.replay.villages || []).filter(function (v) { return v.arrival_min != null; });
    var lead = vs.map(function (v) { var real = v.arrival_min - A, trip = v.arrival_min - T;
      return '<tr><td>' + esc(v.name) + '</td><td>' + (v.arrival_kind === 'gauge' ? '' : '≈') + '+' + v.arrival_min.toFixed(0) + ' min</td><td>' + signed(real, 0) + ' min</td><td>' + signed(trip, 0) + ' min</td></tr>'; });
    var pass = D.scorecard.pass, res = D.bt;
    return '<h2 id="r-title">How the rule works</h2>' +
      '<h3>The rule, in one line</h3><p>In every NISAR pass, find every patch of ground that moves, score each one, and put the ' + C.pass_condition.top_n + ' highest on a watch list. A watched valley gets a ground sensor that sounds sirens if the slope fails.</p>' +
      '<p>Score = motion ÷ its noise × how consistently it moves × ' + C.score.opposing_bonus + ' if neighbours pull apart × ' + C.score.ice_bonus + ' if glacier ice sits on rock steeper than ' + C.blobs.ice_over_rock_min_slope_deg + '° × a small boost for people downstream × a small boost for a hot season.</p>' +
      '<h3>Where each part comes from</h3>' + tbl(['Factor', 'Evidence'], ev) +
      '<p class="note">We fixed the rule before computing any result. We had read these studies of Langtang, so the real proof is how it does on an event it never saw, below.</p>' +
      '<h3>What it would have done at Langtang</h3>' +
      '<p>The last pass before the collapse (' + winLabel(lp) + ') was public on ASF ' + Math.round(D.context.timing.hours_on_asf) + ' hours before it. In that pass the source valley ranked <b>#' + sc.source_rank + ' of ' + sc.n_ranked + '</b>, so it would have been on the watch list. A sensor there, with the default delays (record, recognise, confirm, broadcast), sounds sirens ' + T.toFixed(1) + ' minutes after the fall:</p>' +
      tbl(['Place', 'Wave arrives', 'Real alert', 'With the tripwire'], lead) +
      '<p class="note">The tripwire delays are our assumptions, in line with Acharya &amp; Paudel’s own estimate that source-side detection within 5 minutes would have warned Rasuwagadhi −2 to +8 minutes ahead. ≈ marks interpolated or extrapolated arrivals; Timure’s reported arrival is 08:50–09:00, later than the routed estimate.</p>' +
      '<h3>Did it hold up on a new disaster?</h3>' +
      '<p>At Langtang the strict test asked for the exact slope in both of the last two passes: it ' + (pass.ranks || []).map(function (r) { return r ? 'ranked #' + r : 'was not ranked'; }).join(', then ') + ' in the last two passes. Run unchanged on other places:</p>' +
      (res && res.sites ? tbl(['Site', 'Exact-slope test', 'False alarms'], ['budhigandaki', 'manang', 'langtang'].filter(function (k) { return res.sites[k]; }).map(function (k) { return btRow(k, res.sites[k]); })) : '<p>Backtest not available.</p>') +
      zoneCallout() + zoneTable() +
      compareTable() +
      '<p><button type="button" class="btn" data-open="eng">' + icon('code') + 'For engineers: the score on real numbers, the data trail, 37 questions</button></p>' +
      '<h3>What we conclude</h3><p>NISAR’s L-band radar sees slopes move through the monsoon, where Sentinel-1 did not. Cascade Watch turns that into a short list of valleys to watch, and it had the zone that failed on that list before both disasters, about twice as often as chance on the one it had never seen. It does not yet name the exact slope, so the list places sensors; sirens sound only on a detected collapse. Two events are a strong start; every new NISAR event is another test.</p>';
  }


  /* ---------------------------------------------------------------- for engineers */
  var TERM_HELP = {
    snr: 'How many times bigger the motion is than the noise in this pass. Dividing cancels the units, so metres (offsets) and millimetres (phase) compete fairly, and a noisy pass counts for less.',
    dir: 'Near 1 when every pixel moves the same way, near 0 when they point everywhere. Multiplying lets chaotic motion drag the whole score down.',
    opp: 'Two neighbouring blocks moving opposite ways along the radar line can mean a crack opening between them.',
    ice: 'Glacier ice resting on steep rock can break away, as it did at Langtang.',
    pop: 'A gentle boost: every tenfold increase in people adds the same step (log10), so a town cannot outweigh real motion.',
    season: 'The same number for every patch in a pass, so it scales scores but never changes the order.'
  };
  var ENG_S = { ex: 0, pt: 1, tag: 'All' };
  function engMachine() {
    var E = D.eng, ex = E.examples[ENG_S.ex], p = ex.patches[Math.min(ENG_S.pt, ex.patches.length - 1)];
    var mx = 0; ex.patches.forEach(function (q) { q.terms.forEach(function (t) { mx = Math.max(mx, t.running); }); });
    var prev = 0;
    var rows = p.terms.map(function (t) {
      var a = prev / mx * 100, b = t.running / mx * 100, fx = t.factor == null ? '= ' + t.value.toFixed(1) : '×' + t.factor.toFixed(t.factor === 1 ? 0 : 2);
      var flat = t.factor === 1;
      var h = '<div class="e-row' + (flat ? ' flat' : '') + '"><span class="e-op" aria-hidden="true">' + esc(t.op) + '</span>' +
        '<div class="e-what"><b>' + esc(t.label) + '</b><span>' + esc(TERM_HELP[t.key] || '') + '</span><small>' + esc(t.input) + '</small></div>' +
        '<div class="e-fx">' + fx + '</div>' +
        '<div class="e-bar" aria-hidden="true"><i class="pv" style="width:' + a.toFixed(1) + '%"></i><i class="nw" style="width:' + b.toFixed(1) + '%"></i></div>' +
        '<div class="e-run">' + t.running.toFixed(1) + '</div></div>';
      prev = t.running; return h;
    }).join('');
    var near = p.dist_km <= (D.config.truth.hit_radius_km) && p.same_channel;
    return '<div class="e-machine" aria-live="polite">' + rows +
      '<div class="e-total"><span>Score <b>' + p.score.toFixed(1) + '</b></span><span class="e-rank">#' + p.rank + ' <small>of ' + ex.n_ranked + '</small></span>' +
      '<span class="e-where">' + (p.channel === 'offsets' ? 'Offset patch' : 'Phase patch') + ' · ' + p.cells + ' cells (' + p.area_km2.toFixed(2) + ' km²) at ' + p.elev_m.toLocaleString() + ' m · ' + p.dist_km.toFixed(1) + ' km from what fell' + (p.same_channel ? ', same stream (' + esc(p.stream || '—') + ')' : ', another stream') + (near ? ' · <b class="good">counts as a hit</b>' : '') + '</span></div></div>';
  }
  function engPatchPick() {
    var ex = D.eng.examples[ENG_S.ex];
    return '<div class="seg inline e-pts" role="radiogroup" aria-label="Patch">' + ex.patches.map(function (p, i) {
      var on = i === Math.min(ENG_S.pt, ex.patches.length - 1);
      return '<button type="button" role="radio" data-pt="' + i + '" aria-checked="' + on + '" tabindex="' + (on ? 0 : -1) + '">#' + p.rank + ' · ' + esc(p.why) + '</button>'; }).join('') + '</div>';
  }
  function engQA() {
    var Q = D.eng.qa, tags = ['All'].concat(Q.map(function (x) { return x.tag; }).filter(function (t, i, a) { return a.indexOf(t) === i; }));
    var list = Q.map(function (x, i) { return { x: x, i: i }; }).filter(function (o) { return ENG_S.tag === 'All' || o.x.tag === ENG_S.tag; });
    return '<div class="e-qa-bar"><div class="seg inline" role="radiogroup" aria-label="Topic">' + tags.map(function (t) {
      var on = t === ENG_S.tag; return '<button type="button" role="radio" data-tag="' + esc(t) + '" aria-checked="' + on + '" tabindex="' + (on ? 0 : -1) + '">' + esc(t) + '</button>'; }).join('') + '</div>' +
      '<span class="note">' + list.length + ' of ' + Q.length + '</span></div>' +
      '<div class="e-qa">' + list.map(function (o) { return '<details><summary><span class="e-qn">' + (o.i + 1) + '</span>' + esc(o.x.q) + '</summary><p>' + esc(o.x.a) + '</p></details>'; }).join('') + '</div>';
  }
  function engSheet() {
    var E = D.eng;
    if (!E) return '<h2 id="e-title">For engineers</h2><p>data/engineers.json is missing: run pipeline/build_engineers.py.</p>';
    var W = E.weights, ex = E.examples;
    var flow = E.stages.map(function (st) { return '<li><span class="e-n">' + st.n + '</span><b>' + esc(st.name) + '</b><code>' + esc(st.script) + '</code><span>' + esc(st.does) + '</span><small>→ ' + esc(st.writes) + '</small></li>'; }).join('');
    var prods = E.products.map(function (pr) {
      return '<div class="card e-prod"><p class="e-lv">' + esc(pr.level) + '</p><h4>' + esc(pr.name) + '</h4><p>' + esc(pr.what) + '</p>' +
        '<p class="e-k">What we did</p><p>' + esc(pr.used) + '</p>' +
        '<p class="e-k">Arrays we read</p><p class="e-arr">' + pr.arrays.map(function (a) { return '<code>' + esc(a) + '</code>'; }).join(' ') + '</p>' +
        '<p class="e-k">On this laptop</p><p><code>' + esc(pr.local_dir) + pr.level + '_*.npz</code>: ' + pr.n_granules + ' granules, ' + pr.local_mb + ' MB, each a ' + pr.grid[0] + ' × ' + pr.grid[1] + ' grid at ' + pr.cell_m + ' m</p>' +
        '<p class="e-k">Example granule</p><p class="e-gr"><code>' + esc(pr.example_granule) + '</code><br>processed and public on ASF ' + esc(pr.asf_processed.replace('T', ' ').replace(':00Z', '').replace(/:\d\dZ$/, '')) + ' UTC</p></div>';
    }).join('');
    var stores = tbl(['Site', 'Folder', 'Granules', 'Size'], E.stores.map(function (s) { return '<tr><td>' + esc(s.site) + '</td><td><code>' + esc(s.path) + '</code></td><td>' + s.granules + '</td><td>' + s.mb.toLocaleString() + ' MB</td></tr>'; }));
    return '<h2 id="e-title">For engineers</h2>' +
      '<p class="lede">How Cascade Watch turns NISAR radar into a ranked watch list, on the real numbers from both floods. Every value here is read from the pipeline’s own files.</p>' +
      '<nav class="e-toc" aria-label="On this sheet"><a href="#e-score">The score</a><a href="#e-flow">Satellite to screen</a><a href="#e-prod">The three products</a><a href="#e-qa">37 questions</a></nav>' +
      '<h3 id="e-score">The score, piece by piece</h3>' +
      '<p>Each moving patch gets one number. Start with how clearly it moves, then multiply by what makes a slope more dangerous. Pick a flood and a patch:</p>' +
      '<div class="seg inline e-ex" role="radiogroup" aria-label="Flood">' + ex.map(function (x, i) { var on = i === ENG_S.ex; return '<button type="button" role="radio" data-ex="' + i + '" aria-checked="' + on + '" tabindex="' + (on ? 0 : -1) + '">' + esc(x.site) + ' · ' + esc(x.window_label) + '</button>'; }).join('') + '</div>' +
      '<div id="e-pick">' + engPatchPick() + '</div>' +
      '<div id="e-mach">' + engMachine() + '</div>' +
      '<div class="e-why">' +
      '<div class="card"><p class="e-sym">÷</p><b>Why divide?</b><p>9 cm in a calm pass can be surer than 1 m in a noisy one. Motion ÷ noise asks “how many times bigger than the wobble?”, a pure number, so the two radar channels can be ranked together.</p></div>' +
      '<div class="card"><p class="e-sym">×</p><b>Why multiply?</b><p>Each factor says how much more worried to be. A multiplier scales any score the same way, and a factor near 0 (chaotic motion) pulls the whole score down. Adding would let one big term hide a bad one.</p></div>' +
      '<div class="card"><p class="e-sym">log</p><b>Why a logarithm?</b><p>1,000 people should matter more than 100, but not ten times more than the radar evidence. With log10, each tenfold step adds the same ' + W.w_pop + '.</p></div></div>' +
      '<p class="note">In code (pipeline/rank.py and web/score.js, identical): <code>' + esc(E.formula) + '</code>, with opposing_bonus ' + W.opposing_bonus + ', ice_bonus ' + W.ice_bonus + ', w_pop ' + W.w_pop + ', w_season ' + W.w_season + '. Patches with mean coherence under ' + E.coherence_floor + ' score 0.</p>' +
      '<h3 id="e-flow">From the satellite to this page</h3>' +
      '<p>Everything ran on this laptop with Python, using the team’s NASA Earthdata login. Nothing is simulated: if a source failed, the pipeline says so and moves on.</p>' +
      '<ol class="e-flow">' + flow + '</ol>' +
      '<p class="e-anc"><b>Also read:</b> ' + E.ancillary.map(function (a) { return esc(a.name) + ' <span>(' + esc(a.use) + ')</span>'; }).join(' · ') + '. Study grid: ' + E.grid.km[0] + ' × ' + E.grid.km[1] + ' km in UTM zone 45N (EPSG ' + E.grid.epsg + '), ' + E.grid.cell_m + ' m cells.</p>' +
      '<p>We never downloaded whole granules. Each NISAR file is an HDF5 container (named arrays in folders) of up to a few GB on ASF’s cloud; the fetch script asks for only the byte ranges that cover the study box and saves them as compressed NumPy files:</p>' + stores +
      '<h3 id="e-prod">The three NISAR products</h3>' +
      '<p>All three are NASA/JPL Level 2 products: already placed on a map grid, so each pixel is a known spot on the ground. Radar wavelength: ' + (E.wavelength_m * 100).toFixed(1) + ' cm (L-band), from the product’s centre frequency.</p>' +
      '<div class="e-prods">' + prods + '</div>' +
      '<h3 id="e-qa">37 questions you may be asked</h3>' +
      '<p>Short, plain answers you can say out loud. Numbers are filled in from the data.</p>' +
      '<div id="e-qa-wrap">' + engQA() + '</div>';
  }
  function bindEng(dlg) {
    function radio(group, attr, set) {
      var bs = all('[' + attr + ']', group);
      bs.forEach(function (b) {
        b.addEventListener('click', function () { set(b.getAttribute(attr)); });
        b.addEventListener('keydown', function (e) {
          var d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key]; if (!d) return; e.preventDefault();
          var i = (bs.indexOf(b) + d + bs.length) % bs.length; set(bs[i].getAttribute(attr)); var n = dlg.querySelector('[' + attr + '="' + bs[i].getAttribute(attr) + '"]'); if (n) n.focus();
        });
      });
    }
    function rePick() { dlg.querySelector('#e-pick').innerHTML = engPatchPick(); radio(dlg.querySelector('.e-pts'), 'data-pt', function (v) { ENG_S.pt = +v; rePick(); dlg.querySelector('#e-mach').innerHTML = engMachine(); }); }
    function reQA() { dlg.querySelector('#e-qa-wrap').innerHTML = engQA(); radio(dlg.querySelector('#e-qa-wrap .seg'), 'data-tag', function (v) { ENG_S.tag = v; reQA(); }); }
    var exg = dlg.querySelector('.e-ex');
    if (!exg) return;
    radio(exg, 'data-ex', function (v) {
      ENG_S.ex = +v; ENG_S.pt = 1;
      all('[data-ex]', exg).forEach(function (b) { var on = b.dataset.ex === v; b.setAttribute('aria-checked', on); b.tabIndex = on ? 0 : -1; });
      rePick(); dlg.querySelector('#e-mach').innerHTML = engMachine();
    });
    rePick(); reQA();
    all('.e-toc a', dlg).forEach(function (a) { a.addEventListener('click', function (e) { e.preventDefault(); var t = dlg.querySelector(a.getAttribute('href')); if (t) t.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' }); }); });
  }

  /* ---------------------------------------------------------------- sheets */  /* ---------------------------------------------------------------- sheets */
  var KEYS = [['Go to a chapter', ['1', '2', '3', '4', '5']], ['Play or pause', ['Space']], ['Step through passes and minutes', ['←', '→']],
    ['Show or hide the sidebar', ['S']], ['Show or hide the inspector', ['I']], ['Next map layer', ['L']], ['Guided tour, with narration', ['G']], ['For engineers: the score on real numbers', ['E']], ['Keyboard shortcuts', ['?']], ['Close a sheet', ['Esc']]];
  function openSheet(which) {
    var dlg = $('sheet-' + which), body;
    if (which === 'data') body = dataSheet();
    else if (which === 'rule') body = ruleSheet();
    else if (which === 'eng') body = engSheet();
    else if (which === 'method') body = D.context.method_html;
    else if (which === 'sources') body = D.context.sources_html;
    else if (which === 'keys') body = '<h2 id="k-title">Keyboard shortcuts</h2><div class="keys">' + KEYS.map(function (k) { return '<div class="r"><span>' + k[0] + '</span><span>' + k[1].map(function (x) { return '<kbd>' + x + '</kbd>'; }).join('') + '</span></div>'; }).join('') +
      '<label class="r sw-row" for="k-single"><span>Single-key shortcuts<small>Turn off if you use speech input or other keyboard tools.</small></span><input type="checkbox" class="sw" id="k-single"' + (singleKeys ? ' checked' : '') + '></label></div>';
    body = body.replace(/<p>([A-Z][a-z]+)<\/p>/g, '<h3>$1</h3>');
    dlg.innerHTML = '<div class="sh-body">' + body + '</div><div class="sh-foot"><button class="btn primary" type="button" data-close>Done</button></div>';
    bindSheet(dlg);
    if (which === 'eng') bindEng(dlg);
    var ks = dlg.querySelector('#k-single');
    if (ks) ks.addEventListener('change', function () { singleKeys = ks.checked; try { localStorage.setItem('cw-keys', singleKeys ? 'on' : 'off'); } catch (e) { /* storage blocked */ } });
    dlg.showModal();
    var h = dlg.querySelector('h2'); if (h) { h.tabIndex = -1; h.focus(); }
  }
  function bindSheet(dlg) {
    all('[data-close]', dlg).forEach(function (b) { b.addEventListener('click', function () { dlg.close(); }); });
    dlg.onclick = function (e) { if (e.target === dlg) dlg.close(); };
  }
  function welcome() {
    var dlg = $('sheet-welcome'), f = fact('dead');
    dlg.innerHTML = '<div class="sh-body"><svg class="appicon" aria-hidden="true"><use href="#i-app"/></svg>' +
      '<h2 id="w-title">Cascade Watch</h2>' +
      '<p class="lede">On ' + fmtDate(eventDate(), true) + ' ice and rock broke away above the Langtang valley in Nepal and sent a flood down the Bhote Koshi and the Trishuli' + (f ? '. At least ' + esc(f.display) + ' people died in Nepal' : '') + '. NISAR had been imaging the slope all summer.</p>' +
      '<p class="q">With rules fixed in advance, would the radar have put this valley on a watch list before it fell?</p>' +
      '<div class="feats">' +
      '<div class="feat">' + icon('waves') + '<div><b>Replay the morning</b><span>The flood front runs the river through the gauge records, against the ' + D.replay.alert_min.toFixed(1) + '-minute alert.</span></div></div>' +
      '<div class="feat">' + icon('radar') + '<div><b>Read the radar</b><span>Slope motion NISAR measured, pass by pass, through monsoon cloud.</span></div></div>' +
      '<div class="feat">' + icon('sliders') + '<div><b>Test the rule</b><span>Switch the pre-registered rules on and off and watch where the slope that fell ranks.</span></div></div>' +
      '</div></div>' +
      '<div class="sh-foot"><button class="btn primary large" type="button" id="w-tour">' + icon('guide') + 'Take the guided tour<small>narrated · about 4 min</small></button>' +
      '<div class="w-alt"><button class="btn" type="button" id="w-start">' + icon('play') + 'Explore on my own</button><button class="btn plain" type="button" id="w-skip">Skip to the answer</button></div></div>';
    var done = function () { try { localStorage.setItem('cw-welcomed', '1'); } catch (e) { /* storage blocked */ } };
    $('w-tour').addEventListener('click', function () { done(); dlg.close(); if (window.CWTour) CWTour.start(); });
    $('w-start').addEventListener('click', function () { done(); dlg.close(); go(1, false); setTimeout(play, reduce ? 0 : 350); });
    $('w-skip').addEventListener('click', function () { done(); dlg.close(); go(5); });
    dlg.addEventListener('cancel', done);
    dlg.showModal();
    $('w-tour').focus();
  }
  function shouldWelcome() {
    if (/[?&]intro=0/.test(location.search)) return false;
    if (/[?&]intro=1/.test(location.search)) return true;
    try { return !localStorage.getItem('cw-welcomed'); } catch (e) { return true; }
  }

  /* ---------------------------------------------------------------- boot */
  var ready = { data: false, map: false }, started = false;
  function maybeStart() {
    if (!ready.data) return;
    if (!started) {
      started = true; S.win = lastPre().id;
      buildTimeline(); go(1, false); preloadPasses();
      if (shouldWelcome()) welcome();
    }
    if (ready.map) { lastWinPainted = null; legendKey = ''; setLayer(S.layer); update(true); }
  }
  var LAYERS = ['motion', 'phase', 'coh', 'radar', 'none'];
  function bindChrome() {
    function chapterClick(e) { var b = e.target.closest('button[data-ch]'); if (b) go(+b.dataset.ch); }
    $('nav').addEventListener('click', chapterClick);
    $('tabbar').addEventListener('click', chapterClick);
    $('prev').addEventListener('click', function () { go(Math.max(1, S.ch - 1)); });
    $('next').addEventListener('click', function () { go(S.ch < 5 ? S.ch + 1 : 1); });
    all('.layerbar button').forEach(function (b) { b.addEventListener('click', function () { setLayer(b.dataset.layer, true); }); });
    segKeys($('layerbar')); segKeys(document.querySelector('.trackpick'));
    all('.trackpick button').forEach(function (b) {
      b.addEventListener('click', function () {
        if (S.track === b.dataset.track) return;
        S.track = b.dataset.track;
        segSelect(b.parentNode, b);
        if (S.mode === 'window') S.win = lastPre().id;
        buildTimeline(); renderChapter(); preloadPasses(); update(true);
      });
    });
    $('passes').addEventListener('click', function (e) {
      var b = e.target.closest('button[data-win]'); if (!b) return;
      stop(); S.mode = 'window'; S.win = b.dataset.win; update(true);
    });
    document.addEventListener('click', function (e) { var b = e.target.closest('[data-open]'); if (b && !b.closest('dialog')) openSheet(b.dataset.open); });   // delegated: chapter content adds buttons later
    $('tg-sidebar').addEventListener('click', function () { togglePane('sidebar', true); });
    $('tg-sidebar-hide').addEventListener('click', function () { togglePane('sidebar', false); });
    $('tg-legend').addEventListener('click', function () { var o = APP.dataset.legend !== 'open'; APP.dataset.legend = o ? 'open' : ''; this.setAttribute('aria-expanded', String(o)); });
    phoneMQ.addEventListener('change', function () { if (isPhone()) { $('sidebar').inert = false; $('panel').inert = false; APP.dataset.inspector = 'open'; APP.dataset.sidebar = 'open'; } relayout(false); });
    $('tg-inspector').addEventListener('click', function () { togglePane('inspector'); });
    var gy = null, gr = $('grabber');
    gr.addEventListener('pointerdown', function (e) { gy = e.clientY; });
    gr.addEventListener('pointerup', function (e) { if (gy == null) return; var dy = e.clientY - gy; gy = null; var full = APP.dataset.sheet === 'full';
      if (dy < -24) { if (!full) togglePane('sheet'); } else if (dy > 24) { if (full) togglePane('sheet'); } else togglePane('sheet'); });
    gr.addEventListener('click', function (e) { if (e.detail === 0) togglePane('sheet'); });   // keyboard
    $('sc-mini').addEventListener('click', function () { go(5); });
    $('z-in').addEventListener('click', function () { map.zoomIn(); });
    $('z-out').addEventListener('click', function () { map.zoomOut(); });
    $('z-north').addEventListener('click', function () { map.easeTo({ bearing: 0, duration: 600 }); });
    $('z-3d').addEventListener('click', function () { var on = map.getPitch() > 5; map.easeTo({ pitch: on ? 0 : 52, duration: 800 }); });
    document.addEventListener('keydown', function (e) {
      if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented || !singleKeys) return;
      if (e.target.closest('input,textarea,select,dialog,#map,[role=radio]')) return;
      var k = e.key;
      if (k >= '1' && k <= '5') go(+k);
      else if (k === ' ') { if (e.target.closest('button,[role=button],summary')) return; e.preventDefault(); S.playing ? stop() : play(); }
      else if (k === 'ArrowRight' || k === 'ArrowLeft') { if (document.activeElement === $('track')) return; e.preventDefault(); step(k === 'ArrowRight' ? 1 : -1); }
      else if (k === 's' || k === 'S') togglePane('sidebar');
      else if (k === 'i' || k === 'I') togglePane('inspector');
      else if (k === 'l' || k === 'L') setLayer(LAYERS[(LAYERS.indexOf(S.layer) + 1) % LAYERS.length], true);
      else if (k === '?') openSheet('keys');
      else if (k === 'e' || k === 'E') openSheet('eng');
    });
    var rt; window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(function () { if (map) map.setPadding(pad()); }, 120); });
    darkMQ.addEventListener('change', function () {
      readColors();
      if (map && map.getLayer('bg')) map.setPaintProperty('bg', 'background-color', COL.mapbg);
      renderChapter(); update(false);
    });
  }

  window.CW = {
    S: S, D: D, LOG: LOG, go: go, play: play, stop: stop, update: update, setLayer: setLayer, step: step, togglePane: togglePane,
    map: function () { return map; }, cur: function () { return cur; }, markers: markers, pad: pad, lastPre: lastPre, preWindows: preWindows,
    winById: winById, winLabel: winLabel, clockAt: clockAt, rankWindow: rankWindow, blobs: BLOBS, isPhone: isPhone, hitKm: function () { return HIT_KM; },
    ready: function () { return ready.data && ready.map; }
  };
  Promise.all([
    fetchJSON('windows.json'), fetchJSON('scorecard.json'), fetchJSON('truth.geojson'), fetchJSON('replay.json'),
    fetchJSON('control.json', true), fetchJSON('source_series.json', true), fetchJSON('context.json'), fetchJSON('img/bounds.json'),
    fetchJSON('rivers.geojson', true), fetchJSON('glaciers.geojson', true), fetchJSON('observed.geojson', true),
    fetchJSON('config.json'), fetchJSON('provenance.json', true), fetchJSON('backtest_plan.json', true), fetchJSON('backtest.json', true), fetchJSON('atlas/view.json', true), fetchJSON('sensitivity_zone.json', true), fetchJSON('prereg_v2.json', true), fetchJSON('engineers.json', true)
  ]).then(function (r) {
    D.atlas = r[15]; D.sz = r[16]; D.v2 = r[17]; D.eng = r[18];
    D.observed = r[10]; D.config = r[11]; D.prov = r[12]; D.btPlan = r[13]; D.bt = r[14];
    var C = D.config;   // the committed, pre-registered rules: the page never carries its own copy
    S.w = { coherenceFloor: C.score.coherence_floor_for_visibility, motionSigma: 0, opposingBonus: C.score.opposing_bonus, iceBonus: C.score.ice_bonus,
      wPop: C.score.w_pop, wSeason: C.score.w_season, nSensors: C.pass_condition.top_n };
    S.w0 = Object.assign({}, S.w); S.d = Object.assign({}, C.tripwire_default_delays_min); HIT_KM = C.truth.hit_radius_km;
    D.windows = r[0]; D.scorecard = r[1]; D.truth = r[2]; D.replay = r[3]; D.control = r[4]; D.series = r[5]; D.context = r[6]; D.bounds = r[7]; D.rivers = r[8]; D.glaciers = r[9];
    var tp = D.truth.features[0];
    D.truthKind = (tp.properties.kind || '').split(' (')[0];
    D.truthCenter = tp.properties.center || (tp.geometry.type === 'Point' ? tp.geometry.coordinates : D.context.source_lonlat);
    var buf = D.truth.features.find(function (f) { return f.properties.role === 'hit_zone'; });
    if (buf) { D.truthBuffer = buf; D.truth = { type: 'FeatureCollection', features: D.truth.features.filter(function (f) { return f.properties.role !== 'hit_zone'; }) }; }
    D.truthLabel = D.context.truth_label || '';
    D.hasMotion = {}; D.windows.forEach(function (w) { D.hasMotion[w.id] = !!w.offsets_layer; });
    var fr = D.replay.front || []; if (fr.length) TL.tmax = Math.ceil(fr[fr.length - 1][0]);
    D.gcovPair = D.context.gcov_pair || [];
    bindChrome(); bindTimeline();
    ready.data = true;
    initMap();
    maybeStart();
  }).catch(function (e) {
    $('chapbody').innerHTML = '<div class="chap"><h2 class="title">Data not found</h2><p>Run the pipeline first (see README), then serve this folder over HTTP. ' + esc(e.message) + '</p></div>';
  });
})();
