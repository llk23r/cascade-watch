/* Cascade Watch — guided tour. Narrated (browser speech synthesis) with captions, and a pointer that
   follows what is being described: map features (it tracks the camera) or controls on the page.
   Every number it says is read from the loaded data at the moment it is said. */
(function () {
  'use strict';
  var CW, D, S;
  var $ = function (id) { return document.getElementById(id); };
  var T = { on: false, i: 0, paused: false, muted: false, timer: null, raf: 0, steps: [], token: 0 };
  var synth = window.speechSynthesis || null;
  var voice = null;
  try { T.muted = localStorage.getItem('cw-tour-muted') === '1'; } catch (e) { /* storage blocked */ }

  function pickVoice() {
    if (!synth) return;
    var vs = synth.getVoices(); if (!vs.length) return;
    var pref = ['Samantha', 'Daniel', 'Karen', 'Google UK English Female', 'Google US English', 'Microsoft Aria'];
    for (var i = 0; i < pref.length; i++) { var v = vs.find(function (x) { return x.name.indexOf(pref[i]) === 0; }); if (v) { voice = v; return; } }
    voice = vs.find(function (x) { return /^en(-|_)/i.test(x.lang); }) || null;
  }
  if (synth) { pickVoice(); synth.addEventListener && synth.addEventListener('voiceschanged', pickVoice); }

  /* ------------------------------------------------------------ live values */
  function num(v, d) { return v == null ? 'unknown' : Number(v).toFixed(d == null ? 0 : d); }
  function facts() {
    var lp = CW.lastPre(), sc = D.scorecard.windows[lp.id] || {}, topN = D.config.pass_condition.top_n;
    var pre = CW.preWindows();
    var ranks = pre.map(function (w) { return (D.scorecard.windows[w.id] || {}).source_rank; });
    var syab = (D.replay.villages || []).find(function (v) { return v.name === 'Syabrubesi'; }) || (D.replay.villages || [])[0];
    var g = ((D.prov && D.prov.gauges) || []).find(function (x) { return x.name === (syab && syab.name); });
    var ser = (D.series || []).find(function (x) { return x.id === lp.id; }) || {};
    var meena = (D.context.meena || []).find(function (m) { return m.id === lp.id; });
    var earlier = (D.series || []).filter(function (x) { return x.direction === lp.direction && x.end <= lp.start && CW.preWindows().some(function (w) { return w.id === x.id; }) && x.offsets_p90_m != null; })
      .sort(function (a, b) { return b.offsets_p90_m - a.offsets_p90_m; })[0];
    var days = pre.map(function (w) { return w.stack_days; }).sort(function (a, b) { return a - b; })[0];
    var hit = (CW.blobs[lp.id] && CW.rankWindow(CW.blobs[lp.id]).hit) || null;
    var pass = D.scorecard.pass || {};
    return {
      A: D.replay.alert_min, collapse: D.replay.collapse_npt, lp: lp, rank: sc.source_rank, n: sc.n_ranked, topN: topN,
      total: pre.length, inTop: ranks.filter(function (r) { return r && r <= topN; }).length,
      missing: pre.filter(function (w, i) { return !ranks[i]; }).map(function (w) { return CW.winLabel(w).replace('→', 'to'); }),
      syab: syab, g: g, p90: ser.offsets_p90_m, meena: meena && meena.disp_m, earlier: earlier, days: days,
      hitKm: hit ? Number(hit.p.dist_truth_km) : null, pass: pass, tripT: S.d.record + S.d.recognise + S.d.confirm + S.d.broadcast
    };
  }
  function spokenWin(w) { return CW.winLabel(w).replace('→', 'to'); }

  /* ------------------------------------------------------------ the script */
  function build() {
    var F = facts();
    var passRanks = (F.pass.ranks || []).map(function (r, i) { var w = CW.winById(F.pass.windows[i]); return (r ? 'ranked ' + r : 'was not ranked') + ' in ' + (w ? spokenWin(w) : 'one pass'); });
    var el = function (sel) { return function () { return document.querySelector(sel); }; };
    var marker = function (fn) { return function () { var m = fn(); return m ? m.getElement() : null; }; };
    return [
      { ch: 1, title: 'Where it started', target: marker(function () { return CW.markers.label; }),
        say: 'This is the Langtang valley in Nepal, in 3D. The yellow outline is where ice and rock broke away, at ' + F.collapse + ' on the 26th of August 2026, as mapped by the UN satellite centre, UNOSAT.' },
      { ch: 1, title: 'The flood', prep: function () { CW.play(); }, target: function () { var f = CW.markers.front; return f && f.getElement().style.display !== 'none' ? f.getElement() : CW.markers.label.getElement(); },
        say: 'The red line is the flood front. Each of the three gauges on this stretch went silent when the wave hit it, so the front passes each gauge between its last normal reading and its first missing one. Places in between are interpolated.' },
      { ch: 1, title: 'The first town', target: marker(function () { var v = CW.markers.villages.find(function (m) { return m.v === F.syab; }); return v && v.marker; }),
        say: F.syab ? (F.syab.name + ' was hit about ' + num(F.syab.arrival_min) + ' minutes after the collapse.' +
          (F.g ? ' Its gauge sent its last reading at ' + F.g.last_row.npt + ': ' + num(F.g.last_row.stage_m, 1) + ' metres, below its warning level of ' + num(F.g.warning_m, 1) + ' metres.' : '') +
          ' The public text alert went out at ' + num(F.A, 1) + ' minutes.') : '' },
      { ch: 1, title: 'The question', target: el('.callout.q'),
        say: 'NISAR, the new NASA and ISRO radar satellite, had imaged this valley all summer: ' + F.total + ' pairs of passes on this track before the collapse, usually ' + F.days + ' days apart. So the question is: with a rule written down in advance, would it have put this valley on a short watch list before it fell?' },
      { ch: 2, title: 'Radar passes', target: el('#segs'),
        say: 'Each bar on the timeline is one pair of NISAR passes. Hatched bars are passes where the rule found no moving patch near the source.' },
      { ch: 2, title: 'What the colours are', target: el('#legend'),
        say: 'The colours on the map are real NISAR measurements: how far the ground slid downhill between two passes, from the pixel offset product. Glaciers flow every day, so a lot of this map moves.' },
      { ch: 2, title: 'Motion at the source', target: el('#creep'),
        say: 'Inside the mapped detachment zone, the fastest tenth of the ground moved ' + num(F.p90, 2) + ' metres downhill in the last pass before the collapse.' +
          (F.meena != null ? ' An independent study, Meena 2026, reports ' + num(F.meena, 2) + ' metres for the same pass, measured over a smaller area, so it confirms the size of the motion rather than the exact number.' : '') +
          (F.earlier ? ' But it moved ' + num(F.earlier.offsets_p90_m, 2) + ' metres in ' + spokenWin(F.earlier) + ' too, so motion alone does not say when.' : '') },
      { ch: 3, title: 'The rule', target: el('.rulecard .rc-steps'),
        say: 'Here is the rule. In every pass it finds the moving patches of ground, scores each one, and puts the top ' + F.topN + ' on a watch list. The rule file was committed to the project history before any result was computed.' },
      { ch: 3, title: 'One score, factor by factor', target: el('#fx'),
        say: 'This is the score of the patch that counts as the source valley, factor by factor: its motion divided by its own measurement noise, how consistently it moves, whether neighbours pull apart, hanging ice, and the people living downstream. Each line says why it is there.' },
      { ch: 3, title: 'On the map', target: function () { var r = CW.markers.ranks.find(function (m) { return m.marker.getElement().classList.contains('src'); }); return r ? r.marker.getElement() : null; },
        say: 'On the map, the yellow badge is that patch' + (F.hitKm != null ? ', ' + num(F.hitKm, 1) + ' kilometres from the mapped detachment,' : '') + ' draining into the same river. In the last pass it ranked ' + F.rank + ' of ' + F.n + ' moving patches.' },
      { ch: 3, title: 'Every pass', target: function () { return CW.isPhone() || document.getElementById('app').dataset.sidebar !== 'open' ? document.getElementById('segs') : document.getElementById('passes'); },
        say: 'Here is its rank in every pass. It was in the top ' + F.topN + ' in ' + F.inTop + ' of ' + F.total + ' summer passes' + (F.missing.length ? ', but it was not ranked at all in ' + F.missing.join(' and ') + '. In that pass the offsets were too noisy for the slope’s motion to clear the threshold.' : '.') },
      { ch: 3, title: 'Try it yourself', target: el('label[for="r-sigma"]'),
        say: 'Try switching a rule off. Every patch is re-scored in your browser, with the same formula the pipeline used, and the map and the list re-sort.' },
      { ch: 4, title: 'The what-if', prep: function () { CW.play(); }, target: el('#race'),
        say: 'Now the what-if. If the valley was on the watch list, a ground sensor on the slope could sound sirens about ' + num(F.tripT, 1) + ' minutes after a collapse. Each dot is a place on the river; the dark line is the real alert at ' + num(F.A, 1) + ' minutes, and the green line is the tripwire.' },
      { ch: 5, title: 'The answer', target: el('#answer'),
        say: 'The answer, by the test fixed in advance: ' + (F.pass.passed ? 'yes' : 'no') + '. The source valley ' + passRanks.join(', but ') + '. So the screen says: watch this valley. It does not say: it will fall next week.' },
      { ch: 5, title: 'Check everything', target: el('[data-open="data"]'),
        say: 'Every number you heard comes from files you can open: the NISAR granules from NASA’s archive, the river gauge records, and the rules file. Press Data to see them, or Method for how it works. You can explore on your own now.' }
    ].filter(function (s) { return s.say; });
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
    ui.box = document.createElement('div'); ui.box.className = 'tour-box'; ui.box.setAttribute('aria-hidden', 'true');
    document.body.appendChild(ui.ring); document.body.appendChild(ui.box);
    $('tc-x').addEventListener('click', end);
    $('tc-prev').addEventListener('click', function () { goStep(T.i - 1); });
    $('tc-next').addEventListener('click', function () { goStep(T.i + 1); });
    $('tc-pause').addEventListener('click', function () { T.paused ? resume() : pause(); });
    $('tc-mute').addEventListener('click', function () { T.muted = !T.muted; try { localStorage.setItem('cw-tour-muted', T.muted ? '1' : '0'); } catch (e) { /* storage blocked */ } paintCtl(); if (T.muted && synth) synth.cancel(); goStep(T.i); });
  }
  function paintCtl() {
    $('tc-pause').innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-' + (T.paused ? 'play' : 'pause') + '"/></svg>' + (T.paused ? 'Resume' : 'Pause');
    var m = $('tc-mute'); m.innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-' + (T.muted ? 'mute' : 'speaker') + '"/></svg>';
    m.title = T.muted ? 'Turn narration on' : 'Turn narration off (captions stay)'; m.setAttribute('aria-label', m.title);
    $('tc-prev').disabled = T.i === 0;
    $('tc-next').innerHTML = T.i >= T.steps.length - 1 ? 'Finish' : 'Next<svg class="ic" aria-hidden="true"><use href="#i-chev-r"/></svg>';
  }

  /* pointer: a ring on map features, a highlight box on controls; follows them every frame */
  function track() {
    if (!T.on) return;
    var st = T.steps[T.i], el = st && st.target && st.target(), ring = 0, box = 0;
    if (el && el.getBoundingClientRect) {        // measure first, then write: no forced style flush mid-frame
      var r = el.getBoundingClientRect(), onMap = !!el.closest('#map');
      var vis = r.width + r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
      if (vis && onMap) { ring = 1; ui.ring.style.transform = 'translate(' + (r.left + r.width / 2 - 28) + 'px,' + (r.top + r.height / 2 - 28) + 'px)'; }
      else if (vis) { box = 1; ui.box.style.transform = 'translate(' + (r.left - 6) + 'px,' + (r.top - 6) + 'px)'; ui.box.style.width = (r.width + 12) + 'px'; ui.box.style.height = (r.height + 12) + 'px'; }
    }
    if (+ui.ring.style.opacity !== ring) ui.ring.style.opacity = ring;
    if (+ui.box.style.opacity !== box) ui.box.style.opacity = box;
    T.raf = requestAnimationFrame(track);
  }

  function speak(text, done) {
    var tok = ++T.token;
    var fallback = function () { clearTimeout(T.timer); T.timer = setTimeout(function () { if (tok === T.token && !T.paused) done(); }, 1200 + text.split(/\s+/).length * 380); };
    if (T.muted || !synth || !voice && !synth.getVoices().length) { fallback(); return; }
    synth.cancel();
    var parts = text.match(/[^.!?]+[.!?]+(\s|$)/g) || [text], k = 0;
    var next = function () {
      if (tok !== T.token) return;
      if (k >= parts.length) { clearTimeout(T.timer); T.timer = setTimeout(function () { if (tok === T.token && !T.paused) done(); }, 700); return; }
      var u = new SpeechSynthesisUtterance(parts[k++].trim());
      if (voice) u.voice = voice; u.rate = 1; u.pitch = 1;
      u.onend = next; u.onerror = function () { if (tok === T.token) fallback(); };
      synth.speak(u);
    };
    next();
    // safety net if the engine never fires onend
    clearTimeout(T.safety); T.safety = setTimeout(function () { if (tok === T.token && !T.paused && synth.speaking === false) fallback(); }, 2500 + text.length * 90);
  }

  function goStep(i) {
    if (i < 0) return;
    if (i >= T.steps.length) { end(); return; }
    T.i = i; T.paused = false; clearTimeout(T.timer);
    var st = T.steps[i];
    var needChapter = CW.S.ch !== st.ch;
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
      if (t && t.scrollIntoView && !t.closest('#map')) t.scrollIntoView({ block: 'center', behavior: 'smooth' });
      speak(st.say, function () { goStep(i + 1); });
    }, needChapter ? 900 : 150);
  }
  function pause() { T.paused = true; clearTimeout(T.timer); if (synth) synth.pause(); paintCtl(); }
  function resume() { T.paused = false; paintCtl(); if (synth && synth.paused && !T.muted) synth.resume(); else goStep(T.i); }

  function start() {
    CW = window.CW; D = CW.D; S = CW.S;
    if (!CW.ready()) { setTimeout(start, 300); return; }
    makeUI();
    T.steps = build(); T.on = true; document.getElementById('app').classList.add('touring');
    $('tour-btn').setAttribute('aria-pressed', 'true');
    cancelAnimationFrame(T.raf); track();
    goStep(0);
  }
  function end() {
    T.on = false; T.token++; clearTimeout(T.timer); clearTimeout(T.safety); if (synth) synth.cancel();
    cancelAnimationFrame(T.raf);
    if (ui.cap) { ui.ring.style.opacity = ui.box.style.opacity = 0; }
    document.getElementById('app').classList.remove('touring');
    $('tour-btn').setAttribute('aria-pressed', 'false');
    $('tour-btn').focus();
  }

  window.CWTour = { start: start, end: end, active: function () { return T.on; } };
  var b0 = $('tour-btn'); if (b0) b0.addEventListener('click', function () { T.on ? end() : start(); });
  document.addEventListener('keydown', function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (T.on && e.key === 'Escape') { e.preventDefault(); end(); return; }
    if (e.target.closest && e.target.closest('input,textarea,select,dialog')) return;
    if ((e.key === 'g' || e.key === 'G') && window.CW && CW.ready()) { T.on ? end() : start(); }
  });
})();
