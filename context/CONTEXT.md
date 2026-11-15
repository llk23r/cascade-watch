# Context: last 24 h (pulled from Kapsicum, 2026-11-15 04:05 Toronto)

## Files in this repo
- `PROMPT.md`: the Claude Code build prompt for Cascade Watch (Phase A mock page, Phase B Python pipeline, data schemas, rules). Copied from claude.ai chat "Cascade Watch NISAR landslide screening".
- `SPEC.md`: exported from the Claude Docs doc "Cascade Watch: build spec" (https://claude.ai/artifact/NisU717bJep6TTrgxVyt1K). Its four embedded diagrams didn't export.
- `context/prototype-cascade-watch.html`: the claude.ai prototype artifact (https://claude.ai/artifact/TZbWQyLQDNsyNo7o27m13b). A single-file, 5-chapter story page (The morning, The creep, The haystack, The race, The answer) with a canvas relief map and a synthetic `CW` data/rules core. Labelled "Illustrative data". Use it as the design reference for Phase A.

## Challenge
- NASA Space Apps 2026, Toronto local event (spaceappsto.webtegy.com). Challenge: **Dancing with the SARs**. Resources: https://www.spaceappschallenge.org/2026/challenges/dancing-with-the-sars/?tab=resources
- Must-haves (from Manus resource cards): an interactive, open-source, user-friendly app that uses NISAR data to show Earth-surface change. Optical, GIS and other radar are supplements only.
- Toronto prizes: no amounts are published. Selected projects can go on to global judging. The Winners Celebration is virtual under the official rules.
- Submission deadline: not found in the captures.

## Submission checklist (from the Saakshi brief)
- At least one NASA data source from the Resources tab must be a core part of the project (NISAR via ASF/Earthdata).
- Team of 6 at most. Everyone registered for the same local event, not waitlisted, and listed on the team page.
- Demo: a video of 30 s or less, OR 7 slides or fewer (title slide included).
- Everything in English. A video in another language needs English subtitles.
- Every required field on the project page filled in.
- Every source cited in References: data, code, text, images.
- Plan: features freeze at hour 36. The last 12 h go to the video, project page and references.

## How the idea evolved (local times)
- **Nov 14, 12:47–15:03**: Background reading. Earthdata SAR surface-water training, the ArcGIS "Floods" storymap, a summary of 37 NISAR flood/landslide papers, and Manus "nepal-sar" challenge cards.
- **Nov 14, ~14:15 to Nov 15, ~01:44**: **Saakshi** project brief (claude.ai doc LaPPvPne6Pe2ETJW8wXLmu, owned by another org, so this session can't read it). It was a broader concept:
  - a pass-by-pass replay
  - a "land ledger" (hectares by land type along the corridor)
  - a what-if for a chosen slope: NASADEM steepest-descent path with pysheds, arrival times from the 26 Aug front speed (~193 km/h over the first 22 km), exposure from OSM Nepal plus Microsoft building footprints
  - an action card
  - a test on a second event: the 11 Oct Budhi Gandaki / Chumchet landslide-dam failure (bridges lost, Budhigandaki 341 MW hydro camp swept away)
- **Nov 15, 00:09–00:37**: Papers. EarthArXiv 14687 (Acharya, timings), Research Square rs-11022365 (Meena, NISAR offsets, 101 zones), rs-11167819, and MDPI Remote Sensing 18(21) 3727 (Kutoğlu, opposing-motion signal).
- **01:48–02:48**: Perplexity question: "Is there anything provided by NASA or someone to track massive things falling?" This led to the tripwire idea (Brienz rockfall radar, USGS seismic plus infrasound).
- **~03:35**: Narrowed to **Cascade Watch**. Wrote the build spec doc and the prototype.
- **03:42–03:49**: Earthdata account created and activated, via the Instinct agent on WhatsApp.
- **04:02**: Copied the build prompt for Claude Code, to run in an empty repo (this one).

## Useful facts from the Saakshi brief that aren't in the spec
- Meena publishes the 101 moving zones as a CC BY 4.0 CSV with coordinates, area, motion, slope and a glacier flag: https://assets-eu.researchsquare.com/files/rs-11022365/v1/b0dbe1d825f6eb03362df3d2.csv . 8 zones are not glaciers, and 3 of those are in the Budhi Gandaki headwaters.
- Radar saw the site 14 times. Only 3 of 17 optical passes were under half cloud. The Landsat 9 pass on the collapse morning was 48% cloud.
- NISAR repeats every 12 days per track, and 3 tracks cross the valley. With Sentinel-1 added, the longest radar gap was 5 days.
- Released volume varies by source: ~99 M m³ (NISAR study, via EconoTimes), 132 M m³ (ICIMOD), >142 M m³ (Hassan et al.).
- ICIMOD says the SMS went out 25–30 min after the corridor was hit. Acharya gives 38.8 min after the collapse. These are different reference points: state which one you use.
- Hypothesis (not proven): snow loss plus wetting brightens L-band and darkens C-band before failure. It was absent in the October miss.

## Status
- `~/.netrc` holds the Earthdata credentials, mode 600. Login checked: HTTP 200 from urs.earthdata.nasa.gov.
- Next step, per PROMPT.md: Phase A, the mock page with the /data schemas. Then check whether descending track 48 frame 74 exists.
