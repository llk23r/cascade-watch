/* Cascade Watch — guided tour. Captions always; narration from pre-recorded audio (data/tour/), made from this
   same text by demo/tts/make_tour_audio.py. A step's audio plays only if it was recorded from exactly the text on
   screen (hash match), so narration can never drift from the data. The pointer is projected from the feature's real
   coordinates (map features) or framed on the control being described. */
(function () {
  'use strict';
  var CW, D, S;
  var $ = function (id) { return document.getElementById(id); };
  var T = { on: false, i: 0, paused: false, muted: false, timer: null, raf: 0, steps: [], token: 0, audio: null, manifest: null };
  try { T.muted = localStorage.getItem('cw-tour-muted') === '1'; } catch (e) { /* storage blocked */ }

  function hash(s) { var h = 5381; for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0; return ('0000000' + h.toString(16)).slice(-8); }

  /* ------------------------------------------------------------ live values */
  function num(v, d) { return v == null ? 'unknown' : Number(v).toFixed(d == null ? 0 : d); }
  function spokenWin(w) { return CW.winLabel(w).replace('→', 'to'); }
  function facts() {
    var lp = CW.lastPre(), sc = D.scorecard.windows[lp.id] || {}, topN = D.config.pass_condition.top_n;
    var pre = CW.preWindows();
    var ranks = pre.map(function (w) { return (D.scorecard.windows[w.id] || {}).source_rank; });
    var syab = (D.replay.villages || []).find(function (v) { return v.name === 'Syabrubesi'; }) || (D.replay.villages || [])[0];
    var g = ((D.prov && D.prov.gauges) || []).find(function (x) { return x.name === (syab && syab.name); });
    var ser = (D.series || []).find(function (x) { return x.id === lp.id; }) || {};
    var meena = (D.context.meena || []).find(function (m) { return m.id === lp.id; });
    var earlier = (D.series || []).filter(function (x) { return x.direction === lp.direction && x.end <= lp.start && pre.some(function (w) { return w.id === x.id; }) && x.offsets_p90_m != null; })
      .sort(function (a, b) { return b.offsets_p90_m - a.offsets_p90_m; })[0];
    var days = pre.map(function (w) { return w.stack_days; }).sort(function (a, b) { return a - b; })[0];
    var hit = (CW.blobs[lp.id] && CW.rankWindow(CW.blobs[lp.id]).hit) || null;
    var bt = D.bt && D.bt.sites && D.bt.sites.budhigandaki;
    return {
      A: D.replay.alert_min, collapse: D.replay.collapse_npt.slice(0, 5), lp: lp, rank: sc.source_rank, n: sc.n_ranked, topN: topN,
      total: pre.length, inTop: ranks.filter(function (r) { return r && r <= topN; }).length,
      missing: pre.filter(function (w, i) { return !ranks[i]; }).map(spokenWin),
      syab: syab, g: g, p90: ser.offsets_p90_m, meena: meena && meena.disp_m, earlier: earlier, days: days, hit: hit,
      pass: D.scorecard.pass || {}, tripT: S.d.record + S.d.recognise + S.d.confirm + S.d.broadcast, bt: bt, zs: D.context.zone_summary
    };
  }

  /* targets: {el} frames a control; {pt:[lon,lat]} rings a map point; {ring:[[lon,lat],...]} frames a map area */
  function proj(ll) { var p = CW.map().project(ll); return [p.x, p.y]; }

  function build() {
    var F = facts();
    var passRanks = (F.pass.ranks || []).map(function (r, i) { var w = CW.winById(F.pass.windows[i]); return (r ? 'ranked ' + r : 'was not ranked') + ' in ' + (w ? spokenWin(w) : 'one pass'); });
    var el = function (sel) { return function () { var e = document.querySelector(sel); return e ? { el: e } : null; }; };
    var ring = D.truth.features[0].geometry.coordinates[0];
    var steps = [
      { ch: 1, title: 'Where it started', target: function () { return { ring: ring }; },
        say: 'This is the Langtang valley in Nepal, in 3D. The yellow outline is where ice and rock broke away at ' + F.collapse + ' on the 26th of August 2026, as mapped by the UN satellite centre, UNOSAT.' },
      { ch: 1, title: 'The flood', prep: function () { CW.play(); }, target: function () { var f = CW.markers.front; return f && f.getElement().style.display !== 'none' ? { pt: f.getLngLat().toArray() } : { ring: ring }; },
        say: 'The red line is the flood front. Each of the three gauges on this stretch went silent when the wave hit it, so the front passes each gauge between its last normal reading and its first missing one. Places in between are interpolated.' },
      { ch: 1, title: 'A town on the river', target: function () { return F.syab ? { pt: [F.syab.lon, F.syab.lat] } : null; },
        say: F.syab ? (F.syab.name + ' was hit about ' + num(F.syab.arrival_min) + ' minutes after the collapse.' +
          (F.g ? ' Its gauge sent its last reading at ' + F.g.last_row.npt + ': ' + num(F.g.last_row.stage_m, 1) + ' metres, below its warning level of ' + num(F.g.warning_m, 1) + ' metres.' : '') +
          ' The public text alert went out at ' + num(F.A, 1) + ' minutes.') : '' },
      { ch: 1, title: 'The question', target: el('.callout.q'),
        say: 'NISAR, the NASA and ISRO radar satellite, had imaged this valley all summer: ' + F.total + ' pairs of passes on this track before the collapse, usually ' + F.days + ' days apart. So the question is: with a rule written down in advance, would it have put this valley on a short watch list before it fell?' },
      { ch: 2, title: 'Radar passes', target: el('#segs'),
        say: 'Each bar on the timeline is one pair of NISAR passes. Hatched bars are passes where the rule found no moving patch near the source.' },
      { ch: 2, title: 'What the colours are', target: el('#legend'),
        say: 'The colours on the map are real NISAR measurements: how far the ground slid downhill between two passes, from the pixel offset product. Glaciers flow every day, so a lot of this map moves.' },
      { ch: 2, title: 'Motion at the source', target: el('#creep'),
        say: 'Inside the mapped detachment zone, the fastest tenth of the ground moved ' + num(F.p90, 2) + ' metres downhill in the last pass before the collapse.' +
          (F.meena != null ? ' An independent study, Meena 2026, reports ' + num(F.meena, 2) + ' metres for the same pass, over a smaller area.' : '') +
          (F.earlier ? ' But it moved ' + num(F.earlier.offsets_p90_m, 2) + ' metres in ' + spokenWin(F.earlier) + ' too, so motion alone does not say when.' : '') },
      { ch: 3, title: 'The rule', target: el('.rulecard .rc-steps'),
        say: 'Here is the rule. In every pass it finds the moving patches of ground, scores each one, and puts the top ' + F.topN + ' on a watch list. The rule file was committed to the project history before any result was computed.' },
      { ch: 3, title: 'One score, factor by factor', target: el('#fx'),
        say: 'This is the score of the patch that counts as the source valley, factor by factor: motion divided by its own measurement noise, how consistently it moves, whether neighbours pull apart, hanging ice, and the people living downstream. Each line says why it is there.' },
      { ch: 3, title: 'On the map', target: function () { return F.hit ? { pt: [F.hit.p.lon, F.hit.p.lat] } : null; },
        say: 'On the map, the yellow badge is that patch' + (F.hit ? ', ' + num(F.hit.p.dist_truth_km, 1) + ' kilometres from the mapped detachment,' : '') + ' draining into the same river. In the last pass it ranked ' + F.rank + ' of ' + F.n + ' moving patches.' },
      { ch: 3, title: 'Every pass', target: function () { var e = CW.isPhone() || document.getElementById('app').dataset.sidebar !== 'open' ? $('segs') : $('passes'); return { el: e }; },
        say: 'Here is its rank in every pass. It was in the top ' + F.topN + ' in ' + F.inTop + ' of ' + F.total + ' summer passes' + (F.missing.length ? ', but it was not ranked at all in ' + F.missing.join(' and ') + '. In that pass the offsets were too noisy for the slope’s motion to clear the threshold.' : '.') },
      { ch: 4, title: 'The what-if', prep: function () { CW.play(); }, target: el('#race'),
        say: 'Now the what-if. If the valley was on the watch list, a ground sensor on the slope could sound sirens about ' + num(F.tripT, 1) + ' minutes after a collapse. Each dot is a place on the river; the dark line is the real alert at ' + num(F.A, 1) + ' minutes, and the green line is the tripwire.' },
      { ch: 5, title: 'The answer', target: el('#answer'),
        say: 'The answer, by the test fixed in advance: ' + (F.pass.passed ? 'yes' : 'no') + '. The source valley ' + passRanks.join(', but ') + '.' },
      { ch: 5, title: 'The real test', target: el('#backtest'),
        say: F.bt && F.zs ? ('We then ran the same rule, unchanged, on a second disaster: the Budhi Gandaki rock avalanche of October 2026. At the exact slope it missed. But a warning protects a valley, not a slope. Within ' + F.zs.zone_km + ' kilometres, about what one valley sensor can watch, a top-' + F.zs.top_n + ' list covered it in both of its last two passes, roughly twice as often as chance. We found that scale afterwards, so we have fixed it in advance for the next events.') :
          'The same rule, unchanged, is being run on other events; the results appear here.' },
      { ch: 5, title: 'Check everything', target: el('[data-open="data"]'),
        say: 'Every number you heard comes from files you can open: the NISAR granules from NASA’s archive, the river gauge records, and the rules file. Press Data to see them, or Method for how it works. You can explore on your own now.' }
    ];
    return steps.filter(function (s) { return s.say; });
  }

  /* ------------------------------------------------------------ UI */
  var ui = {};
  function makeUI() {
    if (ui.cap) return;
    ui.cap = document.createElement('div');
    ui.cap.className = 'tour-cap glass'; ui.cap.setAttribute('role', 'region'); ui.cap.setAttribute('aria-label', 'Guided tour');
    ui.cap.innerHTML = '<div class="tc-top"><span class="tc-n" id="tc-n"></span><b class="tc-title" id="tc-title"></b>' +
      '<button type="button" class="tb-btn" id="tc-mute"></button><button type="button" class="tb-btn" id="tc-x" aria-label="End the tour (Esc)" title="End the tour (Esc)"><svg class="ic" aria-hidden="true"><use href="#i-x"/></svg></button></div>' +
      '<p class="tc-text" id="tc-text" aria-live="polite"></p><div class="tc-bar"><i id="tc-prog"></i></div>' +
      '<div class="tc-ctl"><button type="button" class="btn" id="tc-prev"><svg class="ic" aria-hidden="true"><use href="#i-chev-l"/></svg>Back</button>' +
      '<button type="button" class="btn" id="tc-pause"></button>' +
      '<button type="button" class="btn primary" id="tc-next">Next<svg class="ic" aria-hidden="true"><use href="#i-chev-r"/></svg></button></div>';
    document.getElementById('app').appendChild(ui.cap);
    ui.ring = document.createElement('div'); ui.ring.className = 'tour-ring'; ui.ring.setAttribute('aria-hidden', 'true');
    ui.dot = document.createElement('div'); ui.dot.className = 'tour-dot'; ui.dot.setAttribute('aria-hidden', 'true');
    ui.box = document.createElement('div'); ui.box.className = 'tour-box'; ui.box.setAttribute('aria-hidden', 'true');
    document.body.appendChild(ui.ring); document.body.appendChild(ui.dot); document.body.appendChild(ui.box);
    $('tc-x').addEventListener('click', end);
    $('tc-prev').addEventListener('click', function () { goStep(T.i - 1); });
    $('tc-next').addEventListener('click', function () { goStep(T.i + 1); });
    $('tc-pause').addEventListener('click', function () { T.paused ? resume() : pause(); });
    $('tc-mute').addEventListener('click', function () { T.muted = !T.muted; try { localStorage.setItem('cw-tour-muted', T.muted ? '1' : '0'); } catch (e) { /* storage blocked */ } paintCtl(); goStep(T.i); });
  }
  function paintCtl() {
    $('tc-pause').innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-' + (T.paused ? 'play' : 'pause') + '"/></svg>' + (T.paused ? 'Resume' : 'Pause');
    var m = $('tc-mute'), has = !!audioFor(T.i);
    m.innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-' + (T.muted || !has ? 'mute' : 'speaker') + '"/></svg>';
    m.disabled = !has;
    m.title = !has ? 'No narration recorded for this step (captions only)' : T.muted ? 'Turn narration on' : 'Turn narration off (captions stay)'; m.setAttribute('aria-label', m.title);
    $('tc-prev').disabled = T.i === 0;
    $('tc-next').innerHTML = T.i >= T.steps.length - 1 ? 'Finish' : 'Next<svg class="ic" aria-hidden="true"><use href="#i-chev-r"/></svg>';
  }

  function track() {
    if (!T.on) return;
    var st = T.steps[T.i], tg = st && st.target && st.target(), mode = 0, x = 0, y = 0, w = 0, h = 0;
    try {
      if (tg && tg.el && tg.el.getBoundingClientRect) {
        var r = tg.el.getBoundingClientRect();
        if (r.width + r.height > 0 && r.bottom > 0 && r.top < innerHeight) { mode = 3; x = r.left - 6; y = r.top - 6; w = r.width + 12; h = r.height + 12; }
      } else if (tg && tg.pt && CW.map()) {
        var p = proj(tg.pt); if (p[0] > -40 && p[0] < innerWidth + 40 && p[1] > -40 && p[1] < innerHeight + 40) { mode = 1; x = p[0]; y = p[1]; }
      } else if (tg && tg.ring && CW.map()) {
        var ps = tg.ring.map(proj), xs = ps.map(function (q) { return q[0]; }), ys = ps.map(function (q) { return q[1]; });
        x = Math.min.apply(null, xs) - 14; y = Math.min.apply(null, ys) - 14; w = Math.max.apply(null, xs) - x + 14; h = Math.max.apply(null, ys) - y + 14;
        mode = w > 0 && h > 0 ? 2 : 0;
      }
    } catch (e) { mode = 0; }
    ui.ring.style.opacity = mode === 1 ? 1 : 0; ui.dot.style.opacity = mode === 1 ? 1 : 0;
    if (mode === 1) { ui.ring.style.left = (x - 26) + 'px'; ui.ring.style.top = (y - 26) + 'px'; ui.dot.style.left = (x - 4) + 'px'; ui.dot.style.top = (y - 4) + 'px'; }   // left/top: the pulse's scale would also scale a translate
    ui.box.style.opacity = mode >= 2 ? 1 : 0; ui.box.classList.toggle('geo', mode === 2);
    if (mode >= 2) { ui.box.style.transform = 'translate(' + x + 'px,' + y + 'px)'; ui.box.style.width = w + 'px'; ui.box.style.height = h + 'px'; }
    T.raf = requestAnimationFrame(track);
  }

  function audioFor(i) {
    var st = T.steps[i], m = T.manifest; if (!st || !m || !m.steps) return null;
    var h = hash(st.say), rec = m.steps.find(function (x) { return x.hash === h; });
    return rec ? 'data/tour/' + rec.file : null;
  }
  function stopAudio() { if (T.audio) { T.audio.onended = T.audio.onerror = null; T.audio.pause(); T.audio = null; } }
  function speak(i, done) {
    var tok = ++T.token, st = T.steps[i], src = audioFor(i);
    var fallback = function () { clearTimeout(T.timer); T.timer = setTimeout(function () { if (tok === T.token && !T.paused) done(); }, 1500 + st.say.split(/\s+/).length * 400); };
    stopAudio();
    if (T.muted || !src) { fallback(); return; }
    var a = new Audio(src); T.audio = a;
    a.onended = function () { if (tok !== T.token) return; clearTimeout(T.timer); T.timer = setTimeout(function () { if (tok === T.token && !T.paused) done(); }, 700); };
    a.onerror = function () { if (tok === T.token) fallback(); };
    var pr = a.play(); if (pr && pr.catch) pr.catch(function () { if (tok === T.token) fallback(); });
  }

  function goStep(i) {
    if (i < 0) return;
    if (i >= T.steps.length) { end(); return; }
    T.i = i; T.paused = false; clearTimeout(T.timer);
    var st = T.steps[i], needChapter = CW.S.ch !== st.ch;
    if (needChapter) CW.go(st.ch);
    if (!CW.isPhone() && document.getElementById('app').dataset.inspector !== 'open') CW.togglePane('inspector', true);
    $('tc-n').textContent = (i + 1) + ' / ' + T.steps.length;
    $('tc-title').textContent = st.title;
    $('tc-text').textContent = st.say;
    $('tc-prog').style.width = ((i + 1) / T.steps.length * 100) + '%';
    paintCtl();
    setTimeout(function () {
      if (!T.on || T.i !== i) return;
      if (st.prep) st.prep();
      var t = st.target && st.target();
      if (t && t.el && t.el.scrollIntoView) t.el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      speak(i, function () { goStep(i + 1); });
    }, needChapter ? 1100 : 200);
  }
  function pause() { T.paused = true; clearTimeout(T.timer); if (T.audio) T.audio.pause(); paintCtl(); }
  function resume() { T.paused = false; paintCtl(); if (T.audio && !T.muted) { var pr = T.audio.play(); if (pr && pr.catch) pr.catch(function () { goStep(T.i); }); } else goStep(T.i); }

  function init() { CW = window.CW; D = CW.D; S = CW.S; }
  function start() {
    init();
    if (!CW.ready()) { setTimeout(start, 300); return; }
    makeUI();
    T.steps = build(); T.on = true; document.getElementById('app').classList.add('touring');
    $('tour-btn').setAttribute('aria-pressed', 'true');
    cancelAnimationFrame(T.raf); track();
    var go0 = function () { goStep(0); };
    if (T.manifest) go0();
    else fetch('data/tour/manifest.json').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }).then(function (m) { T.manifest = m; go0(); });
  }
  function end() {
    T.on = false; T.token++; clearTimeout(T.timer); stopAudio();
    cancelAnimationFrame(T.raf);
    if (ui.cap) { ui.ring.style.opacity = ui.box.style.opacity = ui.dot.style.opacity = 0; }
    document.getElementById('app').classList.remove('touring');
    $('tour-btn').setAttribute('aria-pressed', 'false');
    $('tour-btn').focus();
  }

  window.CWTour = { start: start, end: end, active: function () { return T.on; },
    script: function () { init(); return build().map(function (s, i) { return { i: i, title: s.title, say: s.say, hash: hash(s.say) }; }); } };
  var b0 = $('tour-btn'); if (b0) b0.addEventListener('click', function () { T.on ? end() : start(); });
  document.addEventListener('keydown', function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (T.on && e.key === 'Escape') { e.preventDefault(); end(); return; }
    if (e.target.closest && e.target.closest('input,textarea,select,dialog')) return;
    if ((e.key === 'g' || e.key === 'G') && window.CW && CW.ready()) { T.on ? end() : start(); }
  });
})();
