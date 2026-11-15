# Task: build "Cascade Watch", a replay of the 26 Aug 2026 Langtang flood

## What this is
A hackathon entry (NASA Space Apps 2026, "Dancing with the SARs"). One static web
page that answers one question with a number: with rules fixed in advance, would
NISAR radar have put the Langtang valley on a watch list a week before it fell?

On 26 Aug 2026 at 02:52:10 UTC (08:37 Nepal time) an ice-rock mass fell from the
north flank of Langtang Lirung (USGS event us7000tbwb, 28.271N 85.515E), and the
flood went down the Lhende Khola, Bhote Koshi and Trishuli. The public SMS alert
went out 38.8 minutes later. The output unit is a valley, not a pixel: a "hit" is
a flagged blob within 2 km of the true source.

## Build in two phases. Phase A must work with no network and no credentials.

### Phase A: the page, on mock data
Build the full page against synthetic files that follow the schemas below exactly,
so real files drop in later with no front-end change. Watermark the page
"MOCK DATA" whenever it is reading mock files.

Layout, one screen:
- Map: 3D terrain of a 37 x 33 km box centred 28.289N 85.521E (deck.gl
  TerrainLayer or CesiumJS; a flat hillshade is an acceptable fallback). Blobs
  coloured by score, top N outlined and numbered. The detachment outline and a
  2 km circle around it are always drawn. River channel, villages, and a wave
  front that moves once the clock passes the collapse.
- Ranked list for the current window; the row within 2 km of the source is
  highlighted.
- One scrubber: steps through the radar windows (20 Jun to 19 Aug), then
  switches scale to minutes from 08:37 to about 09:35 on 26 Aug. Mark 08:37
  collapse, 08:40 Rasuwagadhi gauge reads 1.62 m against a 6 m warning level,
  08:50 Syabrubesi gauge stops transmitting, 09:00 district office informs DHM,
  09:15 SMS alert.
- Sliders that re-rank instantly in the browser: coherence floor, motion
  threshold, opposing-sign bonus, ice-over-rock bonus, people-below weight,
  season weight, N sensors placed, and four tripwire delays (record, recognise,
  confirm, broadcast).
- Scorecard, always visible: rank of the true source this window, blobs above
  it, Sentinel-1 control rank, whether the source is visible at this coherence
  floor, tripwire alert time, and minutes gained per village (real alert vs
  tripwire). The tripwire only fires if the source valley was in the top N.

Schemas (write these to /data, mock first):
- windows.json: [{id, start, end, stack_days, ice_lost_share}]
- blobs_<window_id>.geojson: polygon features with properties {id, area_km2,
  motion_m, sigma_m, dir_coherence, coherence, opposing_sign, ice_over_rock,
  people_below, season_anomaly, channel}
- truth.geojson: the detachment outline
- replay.json: {collapse_utc, events:[{t_min, label}], villages:[{name, lon,
  lat, arrival_min, population}]}
- control.json: {sentinel1_rank_per_window: [...]}

Scoring runs in the browser from the blob properties, so sliders are instant:
score = (motion / sigma) x dir_coherence x (opposing bonus if opposing_sign)
x (ice bonus if ice_over_rock) x (1 + w_pop x log10(1 + people_below))
x (1 + w_season x (season_anomaly - 1)). Blobs under the coherence floor or the
motion threshold score zero and are drawn greyed.

Make the mock realistic: about 50 blobs, the source moving roughly 0.96 m in the
last 24-day window with directional coherence 0.87, and the two mid-July windows
losing most ice to monsoon decorrelation.

### Phase B: the real pipeline (Python), writing the same files
1. Fetch. NISAR L2 GOFF (pixel offsets) and L2 GUNW (unwrapped phase) from ASF
   with asf_search. Ascending track 98 frame 16 is known to have five pairs,
   20 Jun to 31 Aug, no gaps. Check descending track 48 frame 74 and report
   what exists. Window 17 Jun to 30 Sep 2026. Read Earthdata credentials from
   ~/.netrc; never write them to the repo. Before fetching, look at the public
   Zenodo records for Meena 2026 (zenodo.org/records/22730419, GOFF site series
   and screen code) and Sah 2026 (doi 10.5281/zenodo.22541071, Sentinel-1
   detection limits) and reuse what is there.
2. Clean, two channels. Offsets: metre-scale motion, component along the
   topographic fall line, directional coherence. Phase: mm of line-of-sight
   motion = -(0.2384 x phase) / (4 pi) x 1000; drop coherence below 0.30
   ascending and 0.40 descending; subtract the median of a 3 to 5 km ring;
   remove scene tilt with a robust plane fit; split east and up where both
   tracks overlap. Default to 24-day stacks, 12-day where coherence holds.
   Record the share of glacier area lost to low coherence per window.
3. Rank. Blobs from both channels. Bootstrap sigma per blob. Attach
   opposing_sign, ice_over_rock (Copernicus DEM slope plus GLIMS or ICIMOD
   glacier outlines), people_below (WorldPop within 30 minutes of flood travel
   downstream), season_anomaly (ERA5 summer temperature and snow cover).
4. Replay. Timeline from the USGS catalogue and the Nepal DHM ten-minute gauge
   series; village arrival times from gauges where they exist, DEM routing
   otherwise.
5. Control. Run the same screen on Sentinel-1 for the same windows. Published
   work says 12-day C-band shows nothing at the source, so report its rank as
   it comes out.

## Rules that are not negotiable
- Fix every threshold and weight in one config file, committed before the first
  real run. Sliders are for showing sensitivity, not for tuning to a result.
- The truth outline (UNOSAT product 4257) is used only to score, never to rank.
- No stage may hard-code a success. If the source ranks 40th or is invisible,
  the page shows that. A miss is a valid result.
- Pass condition to report, not to engineer: a blob within 2 km of the source
  ranks in the top five in the last two windows before 26 Aug. Also report the
  baseline: rank by raw offset magnitude alone.
- Never present mock numbers as real. The watermark comes off only when every
  file in /data came from the pipeline.
- If a dataset is missing or a download fails, stop and tell me what is
  missing. Do not substitute invented values.

## Order of work and done
1. Phase A end to end, then show me the page running locally.
2. Phase B stages 1 to 3, then print the scorecard numbers.
3. Stages 4 and 5, swap the data, remove the watermark.
Done means: the page loads from static files, the scrubber and sliders work,
and the scorecard gives a rank and a false-alarm count from real data. Write a
README with how to run both phases and where every number came from.