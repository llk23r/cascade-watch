# Cascade Watch

**A replay of the 26 August 2026 Langtang flood on real NISAR radar.**
NASA Space Apps Challenge 2026 · *Dancing with the SARs* · Toronto

> With rules fixed in advance, would NISAR have put the Langtang valley on a watch list before it fell?

At 08:37:10 NPT on 26 August 2026 a combined ice and bedrock mass broke away high on the Langtang massif (USGS us7000tbwb; UNOSAT mapped a 1.96 km² detachment zone). The flood ran down the Bhote Koshi and the Trishuli and killed at least 1,455 people in Nepal, with 5,285 missing (USGS, as of 4 Oct). The three gauges nearest the source were destroyed before any crossed its warning level (last readings 27%, 69% and 87% of warning). The public SMS went out **38.8 minutes** after the collapse, 16–26 minutes after the wave hit Syabrubesi (Acharya & Paudel 2026).

Cascade Watch runs a pre-registered screening rule over every NISAR pass of that summer, explains each step with a narrated guided tour, and shows the raw records behind every number. It ranks every moving slope in a 37 × 33 km box and then checks where the slope that actually fell landed in that ranking.

## The answer, from real data

| | Result |
|---|---|
| Last NISAR pass before the collapse (26 Jul → 19 Aug, ascending track 98, on ASF from 24 Aug 13:30 UTC) | **source valley ranked #2 of 193** moving slopes |
| Raw-offset baseline (rank by offset magnitude alone, Meena-style) | #7 of 161 |
| Pass before that (14 → 26 Jul) | **not ranked**: no moving patch formed near the source (noisiest offsets of the summer: 3σ cut 0.75 m vs 0.50 m site motion) |
| Pre-registered pass test (top 5 in *both* of the last two passes) | **not passed** |
| Descending track 48, last pass (23 Jul → 16 Aug) | #14 of 219 |
| Same screen with the products' tropospheric screens removed (sensitivity) | #2 of 190 in the last pass; still not ranked the pass before |
| **Backtest: Budhi Gandaki rock avalanche, 11 Oct 2026 (rules unchanged)** | **not passed**: #10 of 211, then not ranked |
| False alarms (another valley in the top 5 two passes running) | Langtang 4 in 7 passes · Budhi Gandaki 5 in 11 · Manang, no failure, 3 in 8 |
| Sentinel-1 C-band control | pending (see *Not done* below) |

What this means:
- **By the pre-registered test: no.** The source valley ranked #2 in the last pass but was missing one pass earlier.
- **In the last pass alone it was on a short list.** That pass was acquired 7 days before the collapse and went public on ASF 37 hours before it. A "hit" is any moving patch within 2 km of the UNOSAT outline that drains to the same channel; here it was phase patch `p+43`, 1.3 km from the outline.
- **It was near the top most of the summer (6 of 7 ascending passes in the top five) and after the collapse too.** Only 4 of those 7 pairs were public before the collapse; 3 were processed later. The screen says *watch this valley*, not *it falls next week*.
- **The motion is real, but it is not a precursor signal by itself.** Inside the detachment zone the fastest tenth moved 1.02 m in the last pass (zone median 0.05 m), and 0.95 m in 20 Jun–2 Jul. Meena (2026) reports 0.96 m for the same last pass, as a crown median over a smaller area: a check on magnitude, not a like-for-like match.
- **The season was hot.** ERA5 (0.25° cell, 5,510 m): 1 Jul–25 Aug 2026 was the 2nd warmest of 36 years (+1.3 °C, z = +3.7). Snowfall was 7% of normal, but that figure depends on the elevation used (1.5% at 5,190 m).
- **A ground tripwire is a what-if.** If a sensor on the watched valley had raised a siren within ~4.5 minutes, Syabrubesi would have had about 13 minutes of warning instead of being hit about 21 minutes before the SMS.

## The rule, and why it is plausible

Every pass: find moving patches (≥ 5 cells of 80 m; offsets above 3× scene noise or phase beyond ±50 mm), score each, watch the top 5.

| Factor | Why it is there |
|---|---|
| motion ÷ its own noise | only motion the radar measured clearly counts; a clean 5 cm beats a noisy 50 cm |
| × direction coherence | a failing slope moves as one block; noise points every way |
| × 1.5 if neighbours move opposite ways | a crack opening between blocks |
| × 1.3 if glacier ice sits on rock ≥ 30° | where ice–rock avalanches start |
| × (1 + 0.25·log10(1 + people below)) | risk = hazard × exposure; log so towns do not swamp motion |
| × (1 + 0.5·(heat anomaly − 1)) | warm, low-snow summers weaken ice; the same for every patch in a pass, so it never changes the order |

The rules are in `pipeline/config.json`, committed (ecffd6d, 04:20 local) before the first results commit (83cc938, 05:02). They were written after the event, by people who had read post-event studies of it, so pre-registration only guards against tuning on our own results. Run unchanged on other places, it did not hold (see *Backtest*).

## Run it

```bash
# view (no build needed: everything the page reads is in /data)
python3 -m http.server 8000      # then open http://localhost:8000

# rebuild from scratch (needs a NASA Earthdata login in ~/.netrc)
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install numpy scipy h5py fsspec aiohttp requests earthaccess rasterio pyproj shapely pillow scikit-image asf_search pyogrio geopandas
python pipeline/fetch_ancillary.py          # DEM, glaciers, WorldPop, OSM, ERA5, Meena zones
python pipeline/fetch_event.py              # USGS, Zenodo, UNOSAT, Copernicus EMS, DHM gauges, GEOFON, papers
python pipeline/nisar_fetch.py GUNW GOFF    # box subsets of every NISAR pair (no full downloads)
python pipeline/nisar_fetch.py GCOV:20260726,20260819,20260831,20260912,20260816,20260828
cd pipeline
python clean.py && python rank.py && python build_replay.py && python build_layers.py && python build_context.py
python render.py
cd .. && node tests/score_parity.test.js    # browser scorer == pipeline ranking
```

## Using the app

The window works like a macOS app: the 3-D valley fills it, and glass panes float on top.

- **Sidebar.** The five chapters (The Morning, The Creep, The Haystack, The Race, The Answer). Below them, every NISAR pass with the rank of the slope that fell under the current rules, and the scorecard.
- **Inspector** (right). The chapter you are on: the story, charts and controls. **Back** and the next chapter's name sit at the bottom.
- **Toolbar.** Map layer (Motion, Phase, Coherence, Radar, Terrain), orbit (Ascending 98 or Descending 48), Method, Sources and keyboard shortcuts.
- **Timeline** (bottom). The summer's passes, then the 65 minutes of 26 August, then the pass after. Drag it, or press play.
- First visit opens a welcome sheet: **Start the replay** runs the wave, **Skip to the answer** jumps to the result.

Keyboard: `1`–`5` chapters, `Space` play/pause, `←` `→` step through passes and minutes, `S` sidebar, `I` inspector, `L` next layer, `?` all shortcuts.

The page follows the system light or dark appearance, reduced motion, reduced transparency and increased contrast. Below 860 px wide it switches to a phone layout: map on top, the chapter in a bottom sheet, chapters in a tab bar. Add `?intro=0` to the URL to skip the welcome sheet, or `?intro=1` to show it again.

`raw/event/{timeline,facts,villages_arrival}.json` are compiled by hand from the fetched sources. Every item carries its source URL and a verbatim quote, and the replay only uses items that have one.

## Backtest: did the rule hold?

At the exact slope, no. In the zone a warning needs, yes (found after the fact; now pre-registered as rule v2). `pipeline/backtest.json` fixed the sites, boxes, truth and windows in git before any NISAR value inside the new boxes was read; `pipeline/backtest.py` runs the same rules (`CW_SITE` selects the box) and writes `data/backtest.json`.

| Site | Role | Pre-registered test | False alarms |
|---|---|---|---|
| Budhi Gandaki (rock avalanche, 11 Oct 2026; same NISAR frame) | test positive | **not passed**: #10 of 211, then not ranked | 5 in 11 passes |
| Manang (no recorded failure) | control | — | 3 in 8 passes |
| Langtang (rules written after it) | development | **not passed**: not ranked, then #2 of 193 | 4 in 7 passes |

**Judged by the zone instead of the exact slope.** A warning protects a valley, not a slope. `pipeline/sensitivity_zone.py` counts passes where a top-N patch falls within a zone around the failure (same drainage), against what a random list of the same size would catch:

| Zone, top 10 | Langtang | Budhi Gandaki |
|---|---|---|
| 2 km | 6/7 (chance 3.08) | 1/11 (chance 1.0) |
| 3 km | 6/7 (chance 3.66) | 3/11 (chance 1.34) |
| 5 km | 6/7 (chance 4.71) | 7/11 (chance 3.59) |
| 8 km | 7/7 (chance 6.0) | 9/11 (chance 7.48) |
| tributary | 7/7 (chance 6.96) | 11/11 (chance 10.91) |
| Valleys on the list per pass | 5.9 | 3.9 |

At 5 km, about what one valley sensor can watch, Budhi Gandaki was on a top-10 list in both of its last two passes (#5, #9) and in 7 of 11 passes against 3.6 by chance (p = 0.026). Too small a zone is a coin-toss on an approximate point; the whole valley is caught by any list. A list entry never sounds a siren (that needs a detected collapse), so a longer list costs sensors, not evacuations. Zone and list size were chosen after seeing these results from about ten combinations, so `pipeline/prereg_v2.json` (commit 6ee6adc) fixes them in advance for the next events.

What that means: NISAR's L-band radar sees slopes move through the monsoon, where Sentinel-1 did not (Sah 2026). The rule cannot name the exact slope; at valley-sensor scale it covered the zone that failed before both disasters. The other proven gain is cutting alert time once a slope fails (Acharya & Paudel 2026). Budhi Gandaki has no mapped outline, so its truth is an approximate scar point; a few hundred metres either way could change its result.

## The guided tour and the deck

- **Guided tour** (Guide, or G): 15 steps, captions always, narrated by a local neural voice (Kokoro-82M, Apache-2.0). `demo/tts/make_tour_audio.py` records each step from the exact text the page builds from `/data`; a clip plays only if its hash matches the caption, so narration cannot drift from the data. The pointer is projected from each feature's real coordinates.
- **"How the rule works"** (button on the rule card): the rule, the evidence behind each factor (from the sourced records), what it would have done at Langtang, and the backtest.
- **The 2.5-minute presentation** (Rahul Acharya, Ganga Gautam) tells the story through a morning at Timure, with timed speaker notes on every slide.

## The 30-second film and the Atlas visuals

`demo/cascade-watch-30s.mp4` (29.9 s, 1080p, original score) tells it in seven beats: the place, the problem, the satellite, the question, the tool, what we found (including the miss), and the sources. Every number in it is read from `/data` when it renders.

- **Composition:** `demo/film/index.html` + `film.js` draw each frame from a time value; `demo/film/render.py` captures 30 fps frame by frame, `score.py` synthesises the soundtrack (no samples, no licensed music), ffmpeg muxes.
- **Opening shot, and "See the place" in The Morning:** an AI visualization, labelled as such. `demo/atlas/render_depth.py` ray-marches the real Copernicus DEM into a depth map for a camera above the source; World Labs Atlas (`atlasChisel`) paints that geometry. The "after" view is an Atlas inpaint confined to the UNOSAT detachment zone and the routed flood corridor. The yellow outline and red route on top are data, projected with the same camera; `demo/atlas/parallax.py` moves the camera with true parallax from the DEM depth. Prompts, seed, operation ids and camera are in `data/atlas/view.json`. Atlas renders the look of the place, not what happened: the measured story is in the rest of the app.

Rebuild: `python demo/atlas/parallax.py && python demo/film/render.py` (with the page served on port 8765).

## Pipeline

| Stage | Script | What it does |
|---|---|---|
| 1 Fetch | `nisar_fetch.py` | Reads only the study box (463 × 412 cells, 80 m, UTM 45N) out of each remote NISAR L2 GUNW / GOFF granule (~1–2 GB each) through authenticated HTTP range requests; GCOV 10 m backscatter for key dates. Prefers composite release P05023, the one that existed at the time. |
| 2 Clean | `clean.py` | **Offsets:** cull GOFF (σ ≤ 1 m, SNR ≥ 5, 5×5 median test), remove the bulk shift, project slant-range and along-track offsets onto the DEM fall line (variance-weighted), pick the lowest-noise layer per pass; the sign is checked against glacier flow. **Phase:** subtract the ionosphere screen, use λ from the product's centre frequency, apply a coherence floor of 0.30 / 0.40 and connected components, remove a robust plane, subtract the 3–5 km ring median. |
| 3 Rank | `rank.py` | Blobs (≥ 5 cells) from both channels; bootstrap σ, direction coherence, opposing-neighbour flag, ice-over-rock (GLIMS/RGI + slope), WorldPop residents along the first 30 km of the D8 flow path, ERA5 heat anomaly. Score = (motion/σ) × dir. coherence × bonuses × exposure × season. Truth (UNOSAT 4257 detachment zone) is used **only to score**. |
| 4 Replay | `build_replay.py` | Flood path = D8 routing on Copernicus DEM from the source; front timing = DHM ten-minute gauge brackets; tripwire = sum of four delays, armed only if the source valley was in the top N of the last pass. |
| 5 Show | `render.py`, `build_layers.py`, `build_context.py`, `index.html`, `web/` | Terrarium terrain tiles, shaded relief, motion/phase/coherence overlays, NISAR GCOV before/after change map; MapLibre 3-D page; scorer re-implemented in `web/score.js`. |

All thresholds and weights are in [`pipeline/config.json`](pipeline/config.json), committed **before the first real run** (see `git log`). Sliders in the page show sensitivity; the headline uses the committed values. `tests/score_parity.test.js` checks the browser ranking against the pipeline for every window.

## Data

| Data | Source | Use |
|---|---|---|
| NISAR L2 GUNW, GOFF (provisional, doi 10.5067/NIL2GUNW-P1) | NASA/JPL-ISRO via ASF DAAC / Earthdata | motion, coherence, geometry (20 pairs, May–Oct 2026, tracks 98 A and 48 D) |
| NISAR L2 GCOV | ASF DAAC | 10 m backscatter before/after; change map of the avalanche path |
| Detachment zone, flood extent, affected buildings/roads, barrier lakes | UNOSAT product 4257 | truth (scoring only), "what happened" layer |
| Rapid mapping EMSR927 | Copernicus EMS | event context |
| Ten-minute gauge series, 26 Aug | Nepal DHM river watch, archived in Zenodo 22679444 (Acharya & Paudel) | front timing, gauge failures |
| Event catalogue | USGS us7000tbwb / us7000tc90, GEOFON gfz2026qrfy | collapse time, classification history |
| Copernicus DEM GLO-30 | ESA / AWS open data | terrain, fall lines, flow routing |
| Glacier outlines | GLIMS / RGI | ice-over-rock flag, stable-ground mask |
| Population | WorldPop | people below each slope |
| Rivers, places | OpenStreetMap (ODbL) | channels, labels |
| 2 m temperature 1991–2026 | ERA5 via Open-Meteo | season anomaly |
| Source series, 101-zone screen | Meena 2026, Zenodo 22730419 (CC BY 4.0) | independent cross-check |

## Honest limits

- The pre-registered test **fails**: the source valley disappears in the 14–26 Jul pass.
- Phase blobs carry millimetre σ, so they dominate a motion/σ score.
- The headline phase is not corrected for tropospheric delay. A sensitivity run (`pipeline/sensitivity_tropo.py`) subtracts each GUNW's own wet + hydrostatic screens and gives the same answer: #2 in the last pass, still unranked one pass earlier (`data/sensitivity.json`).
- The 2 km hit zone around a 2 km² outline is generous: several blobs qualify in most passes.
- NISAR L2 is provisional; there is no earlier-year NISAR archive to separate seasonal snow change from precursor motion.
- Arrival times between gauges are interpolated along the flow path; beyond Betrawati they are extrapolated.

## Not done

- **Sentinel-1 C-band control.** `pipeline/fetch_s1.py` is ready: 8 S1 pairs on descending orbit 121, matched to the NISAR windows. ASF's HyP3 service first needs the account owner to accept the ASF Terms of Service at <https://urs.earthdata.nasa.gov/approve_app?client_id=BO_n7nTIlMljdvU6kRRB3g>. After that, run `python pipeline/fetch_s1.py all` (then `wait`, then `process manifest`) and rerun the ranking on the S1 rasters. Until then the page shows the control as pending, and quotes Sah (2026): routine 12-day Sentinel-1 products showed no motion above ~13 mm/month at this slope.

- **Test rule v2.** The next NISAR-era failures are its first real test.
- **More backtest events.** Only one clean test positive exists so far (Budhi Gandaki). Lete (Kali Gandaki) needs an optical scar map first; Weisshorn East is a partial ice break-off. Winter NISAR pairs back to Nov 2025 were not fetched.

## Challenge checklist

- NASA data from the challenge resources is the core: NISAR L2 GUNW/GOFF/GCOV via ASF and Earthdata.
- Interactive, open-source (MIT), runs from static files with no backend and no keys.
- Visualises Earth-surface change: glacier and slope motion per pass, coherence loss, before/after backscatter change.
- Every source is cited in the app (Sources panel) and here.
- `demo/cascade-watch-30s.mp4`: a 29.9-second film (problem, approach, tool, data, sources, findings).

## Layout

```
index.html, web/          the app: macOS-style glass UI, MapLibre GL JS 4.7.1 from unpkg, no build step
data/                     everything the app reads (pipeline output)
pipeline/                 fetch → clean → rank → replay → render
tests/                    score parity test, headless screenshots, demo recorder
raw/                      downloads and intermediates (git-ignored)
```
