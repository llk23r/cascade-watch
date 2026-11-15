# Cascade Watch: build spec

<!-- Exported 2026-11-15 from the Claude Docs doc "Cascade Watch: build spec" (rev 18): https://claude.ai/artifact/NisU717bJep6TTrgxVyt1K . Four embedded diagrams/mocks did not export; see context/prototype-cascade-watch.html for the interactive mock. -->

Nov 15, 2026 · @Rahul

A replay of the Langtang flood of 26 August 2026 that answers one question with a number: with rules fixed in advance, would NISAR have put that valley on a watch list a week before it fell? Built for the NASA Space Apps 2026 challenge "Dancing with the SARs". Self-contained: an agent with this page, an Earthdata login and a laptop can deliver it.

## The problem

On 26 August 2026 at 02:52 UTC an ice-rock mass fell off the Langtang massif, dammed the Lhende Khola, and sent a flood down the Bhote Koshi and Trishuli that left at least 1,454 dead and more than 5,000 missing in Nepal. The wave reached the first river gauge in about 13 minutes. The public alert came 38.8 minutes after the trigger.

The sensors were not the problem. NISAR imaged the slope five times before it fell, with no gaps, and its pixel offsets show the source moving about 0.96 m in the 24 days ending a week before failure. Routine 12-day Sentinel-1 products saw nothing (Sah 2026): the signal needs L-band and offsets, and a rule. Public ten-minute river gauges saw the wave only by dying: their warning level was 6 m, set for slow floods, and three were destroyed at 27, 69 and 87% of it. A seismic network catalogued the collapse as a magnitude 5.2 landslide within seconds. What failed was interpretation: the creeping slope looked like about 100 other moving slopes in the same radar footprint, and nobody had a rule that ranked it, so nobody was watching that valley.

## What we build, and for whom

A fortnightly watch list of valleys. Every NISAR pass, every slope in a footprint is scored by how suspiciously it is creeping, how many people sit in the channel below it, and whether the season has primed it. The top few valleys go to Nepal's Department of Hydrology and Meteorology and the district disaster office, who put fast ground sensors on those slopes, drill the villages below, and wire a tripwire to a siren. Radar looks everywhere slowly; ground sensors look at a few places fast. The list connects the two. Precedent: Blatten, Switzerland, 2025, where monitoring led to evacuation before the collapse.

The unit of output is a valley, not a pixel. A hit means a flagged patch within 2 km of the true source and draining into the same channel; a district office acts on a valley, not on a millimetre.

For the hackathon the build is a replay, not a live service: one fixed box of about 37 by 33 km around Langtang, June to September 2026, on a web page with a time scrubber. Radar cannot ring a bell on the day; the 12-day cycle is for the slow creep before. Seconds and minutes belong to the tripwire, which the replay shows from published timings.

## Pipeline at a glance

[embedded content: pipeline · 5 stages, 4 handoffs]

Stages 1 to 3 answer the eval on their own; 4 and 5 turn the answer into something a person can scrub.

## Stage by stage

Every rule below is fixed before any result is seen. No stage may hard-code a success; every stage emits a number the page shows.

| Stage | Input and where it comes from | What happens | Hands on |
| --- | --- | --- | --- |
| 1 Fetch | NISAR L2 GOFF offsets and L2 GUNW phase (provisional) from ASF, via asf_search or Vertex, Earthdata login. Ascending track 98 confirmed: five pairs, 20 June to 31 August, no gaps (Meena). Ascending track 98 frame 16, descending track 48 frame 74. Box centred 28.289°N 85.521°E, 17 June to 30 Sep 2026. Each granule: unwrapped phase, coherence, connected components, incidence, 80 m grid | Download every pair. Record which dates exist | One HDF5 per pair, plus the list of dates |
| 2 Clean | Stage 1 files; Copernicus DEM; glacier outlines (GLIMS or ICIMOD) | Two channels. Offsets (GOFF): metre-scale motion of the moving mass, as Meena measured; keep the along-fall-line component and the directional coherence. Phase (GUNW): mm-scale motion of the edges, as Kutoğlu measured; phase to mm with λ = 0.2384 m, drop coherence below 0.30 ascending and 0.40 descending, subtract the 3 to 5 km ring median, remove scene tilt, split east and up where both tracks overlap. Use 24-day stacks by default; 12-day pairs where coherence holds | One GeoTIFF of relative motion per window, with the share of ice lost to low coherence |
| 3 Rank | Stage 2 rasters; DEM; population layer (WorldPop or HRSL); ERA5 summer temperature and snow cover | Blobs from offsets (fall-line motion above threshold, directional coherence above the scene's) and from phase (cells beyond 50 mm). Per blob: mean motion, bootstrap uncertainty, directional coherence, whether its vertical sign opposes a neighbour, change since the last window, and whether it sits on a hanging glacier over rock. Score = motion / uncertainty × 1.5 if opposing × exposure (people within 30 min of flood travel downstream) × preconditioning (season anomaly). Rank per window | GeoJSON of ranked blobs per window, and the rank of the first blob within 2 km of the UNOSAT outline |
| 4 Replay | USGS catalogue (M5.2 at 02:52:16 UTC); DHM river-watch ten-minute gauges for 26 Aug; village points with DEM flood travel time | Fix the real timeline: 08:37 collapse (USGS first posted M4.4 earthquake, later M5.2 landslide); 08:40 Rasuwagadhi gauge at 1.62 m against a 6 m warning level; 08:50 Syabrubesi gauge dead; 09:00 district office informs DHM; 09:15 text alerts, 38 minutes after. Lead time at the first settlement −26 to −16 min, at the next +4 to +14 (Acharya). Compute a tripwire alert time from slider delays (record, recognise, confirm, broadcast). Per village: wave arrival minus alert time | One JSON of villages with minutes gained, real and simulated |
| 5 Show | Stages 3 and 4 outputs; DEM tiles | Static web page; CesiumJS or deck.gl terrain; a scorer in JavaScript so sliders are instant | The page in the next section |

Control, not cross-check: Sah (2026) showed routine Sentinel-1 12-day products held no signal at the source (no motion above about 13 mm per month over seven weeks). Run the same screen on Sentinel-1 for the same windows; it should rank the source nowhere. If NISAR ranks it and Sentinel-1 does not, that is the L-band story in one number. Ground truth is the UNOSAT possible-detachment outline (product 4257), used only to score, never to rank.

## Pass or fail

Pass: in the last two windows before 26 August, a blob within 2 km of the source and in the Lhende Khola catchment ranks in the top five of the footprint, with the false-alarm count shown beside it. Baseline to beat: Meena's raw-offset rank, where the source did not exceed the 95th percentile of size-matched random regions. The build adds what Meena did not: directional coherence, the opposing-sign phase signal, ice-over-rock terrain, and people downstream.

A miss is still a result. If the source ranks 40th or is invisible under low coherence, the page says so; that is a true statement about what repeat-pass L-band radar can see before an ice-rock failure. Nobody has run this test: Meena (2026) screened the whole footprint blind but on raw displacement, and Langtang was one of about 101 movers; Kutoğlu et al. (2026) found the opposing-motion signal 1.4 km from the source, but after the event, knowing where to look, on 24-day stacks. Combining the blind screen with the opposing-motion score and a 2 km hit rule is the new part.

The scorecard, always on screen: rank of the true source per window, blobs above it, Sentinel-1 RMSE, minutes gained per village.

## The web page

[embedded content: web page · map, list, sliders, scrubber, scorecard]

Drag the scrubber through June to August and the ranked list reshuffles while the true source's rank is always shown. Cross 02:52 UTC on 26 August and the scale changes to minutes: the seismic marker fires, the first gauge jumps at 13 minutes, the real alert lands at 38.8, and the tripwire's alert lands wherever the slider delays put it. Every slider re-runs the scorecard live. No live mode, no generative 3D; the terrain is the real DEM.

**Interactive mock (synthetic numbers, real shapes).** Drag the scrubber through the five windows and into the minutes of 26 August; move the coherence floor to see the source vanish in the monsoon windows; set N to see whether a sensor would have been placed.

[embedded content: interactive mock · scrubber, coherence floor, N sensors, scorecard]

## Order of work

1. **Check the descending track** (10 minutes, do first). Ascending track 98 is confirmed. Search ASF Vertex for GOFF and GUNW on descending track 48 over the box, 17 June to 30 September 2026. If it is missing, east-up splitting drops out and the opposing-sign signal comes from line-of-sight only; say so on the page. Meena's Zenodo record has the ascending GOFF series ready to use.
2. Stages 1 to 3 in Python (asf_search, rasterio, numpy, scipy.ndimage). Export one GeoJSON per window and the scorecard JSON. This alone answers the eval.
3. Stage 4: the replay JSON from the published timings and the gauge feed.
4. Stage 5: the page. Terrain and blobs first, scrubber second, sliders third.
5. Sentinel-1 cross-check, last.

Definition of done: the page loads, the scrubber moves, and the scorecard answers "would this valley have been on the list a week early" with a rank and a false-alarm count.

## From replay to a running system

[embedded content: the running system · radar to siren, and the agents around it]

The pieces already exist separately. At Brienz, Switzerland, a ground radar watches one slope and turns the road's traffic lights red within seconds of rock moving. At Blatten in 2025, monitoring led to evacuation before the collapse. The USGS showed in 2026 that seismic plus infrasound can tell a large ice-rock avalanche from an ordinary earthquake quickly. What nobody has is the front of the chain: a way to decide which of ten thousand Himalayan slopes deserves that kit. That is the watch list.

Honest about the clock: on 26 August the first village had about 13 minutes, and a siren at minute one would not have saved everyone there. Villages further down the Trishuli had half an hour or more, and the real alert reached them at minute 39. The minutes-gained number on the page is computed per village, so the demo shows both the limit and the value.

The agents are the next step, not this build. Once the pipeline is deterministic and logged, an agent can run it every pass, draft the bulletin for a human to sign, and keep a running record of what the rule flagged and what happened, which is the evidence a ministry needs before it trusts a siren to a rule.

## Sources

Papers

- Kutoğlu, Softa and Akgün (2026). Differential rock–ice deformation before the 2026 Langtang hazard cascade: NISAR observations and geological interpretation. Remote Sensing 18(21), 3727. Peer reviewed. The motion rules, the opposing-motion signal, the 1.4 km offset.
- Meena, S. R. (2026). NISAR offsets measure pre-failure motion but do not single out the Bhote Koshi avalanche source. Research Square preprint; data and code on Zenodo. The blind footprint screen, about 101 movers.
- [Acharya, S. (2026)](https://eartharxiv.org/repository/view/14687/). The Bhote Koshi–Trishuli flash flood of 26 August 2026: warning latency exceeded flood travel time. EarthArXiv preprint v5. The 13 and 38.8 minute timings, from public gauges.
- Hassan et al. (2026). Extreme heat and snow drought preconditioned the August 2026 Nepal rock–ice avalanche flood. Research Square preprint. The preconditioning factor.
- [HESS 30, 6339 (2026)](https://hess.copernicus.org/articles/30/6339/2026/). Probabilistic forecasts and a learned warning rule, Swiss rivers. Measurements stay deterministic; forecasts carry a range. [Code on Zenodo](https://zenodo.org/records/22959931).

Data

- NISAR L2 GUNW, ASF Data Search Vertex and NASA Earthdata (login required).
- UNOSAT possible-detachment outline, product 4257.
- Nepal DHM river-watch, ten-minute stage series; USGS earthquake catalogue.
- Copernicus DEM; GLIMS or ICIMOD glacier outlines; WorldPop or HRSL population; ERA5 reanalysis.
- Sentinel-1 via ASF for the cross-check.

Fall detectors that exist today, for the tripwire story: [Geopraevent rockfall radar, Brienz](https://www.geopraevent.ch/project/rockfall-radar-brienz/?lang=en); [USGS seismic plus infrasound, Denali 2026](https://www.usgs.gov/publications/rapid-seismic-and-infrasound-assessment-large-landslides-a-case-study-denali-national).
