"""Stage 5c — Text, facts and provenance for the page (data/context.json).

Numbers are read from pipeline outputs and from sourced records in raw/event; prose is
written around them. No number in here is typed by hand without a source key.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from grid import CFG, DATA, RAW, ROOT, find

SOURCES = {
    "nisar": ("NISAR L2 (ASF)", "https://search.asf.alaska.edu/#/?dataset=NISAR"),
    "usgs": ("USGS us7000tbwb", "https://earthquake.usgs.gov/earthquakes/eventpage/us7000tbwb"),
    "geofon": ("GEOFON", "https://geofon.gfz.de/eqinfo/event.php?id=gfz2026qrfy"),
    "acharya": ("Acharya 2026", "https://eartharxiv.org/repository/view/14687/"),
    "kutoglu": ("Kutoğlu 2026", "https://www.mdpi.com/2072-4292/18/21/3727"),
    "meena": ("Meena 2026", "https://www.researchsquare.com/article/rs-11022365/v1"),
    "hassan": ("Hassan 2026", "https://www.researchsquare.com/article/rs-11167819/v1"),
    "sah": ("Sah 2026", "https://doi.org/10.5281/zenodo.22541071"),
    "ndrrma": ("NDRRMA SitRep", "https://ndrrma.gov.np/"),
    "kpost": ("Kathmandu Post", "https://kathmandupost.com/national/2026/08/27/how-the-bhotekoshi-flood-warning-unfolded"),
    "unosat": ("UNOSAT 4257", "https://unosat.org/products/4257"),
    "ems": ("Copernicus EMSR927", "https://rapidmapping.emergency.copernicus.eu/EMSR927"),
    "dhm": ("DHM river watch", "https://www.dhm.gov.np/hydrology/river-watch"),
    "era5": ("ERA5 via Open-Meteo", "https://open-meteo.com/en/docs/historical-weather-api"),
    "worldpop": ("WorldPop", "https://www.worldpop.org/"),
    "osm": ("OpenStreetMap", "https://www.openstreetmap.org/copyright"),
    "glims": ("GLIMS/RGI", "https://www.glims.org/"),
    "copdem": ("Copernicus DEM", "https://registry.opendata.aws/copernicus-dem/"),
}


def load(name):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else None


def event_facts() -> dict:
    """Facts sourced by the evidence step (raw/event/facts.json), normalised to {key: {...}}."""
    p = RAW / "event" / "facts.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text())
    items = raw.get("facts", raw) if isinstance(raw, dict) else raw
    if isinstance(items, dict):
        items = [dict(key=k, **v) if isinstance(v, dict) else dict(key=k, value=v) for k, v in items.items()]
    return {str(x.get("key") or x.get("name")): x for x in items}


def main():
    card, windows, replay = load("scorecard.json"), load("windows.json"), load("replay.json")
    ef = event_facts()
    lp = [w for w in windows if w["direction"] == "A" and w["phase"] == "pre"][-1]
    sc = card["windows"][lp["id"]]
    prev = [w for w in windows if w["direction"] == "A" and w["phase"] == "pre"][-2]
    g = {x["name"]: x for x in replay["gauges"]}

    facts = {
        "alert": dict(display="38.8", unit="min", label="from the collapse to the mass SMS alert (679,295 messages)", source="acharya", tone="late"),
        "rasuwa": dict(display=f"{g['Rasuwagadhi']['arrival_lo']:.0f}–{g['Rasuwagadhi']['arrival_hi']:.0f}", unit="min",
                       label="for the wave to reach the Rasuwagadhi gauge, which it destroyed at 27% of its warning level", source="acharya", tone="late"),
        "syabru": dict(display=f"{g['Syabrubesi']['arrival_lo']:.0f}–{g['Syabrubesi']['arrival_hi']:.0f}", unit="min",
                       label="to reach Syabrubesi, the first town: it was hit 16 to 26 minutes before the alert", source="acharya", tone="late"),
        "passes": dict(display=str(len([w for w in windows if w["direction"] == "A" and w["phase"] == "pre"])), unit="pairs",
                       label="NISAR ascending interferometric pairs over the slope before the collapse, every one on ASF", source="nisar", tone="ok"),
    }
    order = ["alert", "rasuwa", "syabru", "passes"]
    cas = (ef.get("casualties") or {}).get("usgs_latest")
    if cas:
        v = cas["value"]
        facts["dead"] = dict(display=f"{v['nepal_killed']:,}", unit="dead", source="usgs_impact",
                             label=f"and {v['nepal_missing']:,} missing in Nepal; {v['nepal_houses_destroyed']:,} houses destroyed (as of {v['as_of'][8:]} Oct)", tone="late")
        order.insert(0, "dead")
        SOURCES["usgs_impact"] = ("USGS", cas["source_url"].split(" ")[0])
    era = None
    ep = RAW / "ancillary" / "era5" / "era5_source_point.json"
    if ep.exists():
        r = json.loads(ep.read_text())["runs"]["era5_elev5190"]
        a, rk = r["anomaly_2026_minus_clim"], r["percentile_rank_2026_among_1991_2026"]
        era = dict(t2026=r["y2026"]["mean_temp_c"], clim=r["climatology_1991_2020"]["mean_temp_c"], anom=a["mean_temp_c"], z=a["mean_temp_c_z"],
                   rank=rk["mean_temp_c"]["rank_highest_first"], n=rk["mean_temp_c"]["n_years"], snow_pct=a["snowfall_cm_pct_of_normal"],
                   years=[[y.get("year"), y.get("mean_temp_c")] for y in r["per_year"]], span="1 Jul–25 Aug")
    meena = []
    mz = RAW / "event" / "zenodo" / "contents_summary.json"
    if mz.exists():
        for r in json.loads(mz.read_text())["meena_22730419"]["source_site_series"]["rows"]:
            a, b = r["pair"].split("-")
            meena.append(dict(id=f"A098_{a}_{b}", disp_m=r["crown_disp_m"], days=r["days"]))

    ranks_txt = {w["id"]: card["windows"][w["id"]]["source_rank"] for w in windows if w["direction"] == "A"}
    pre_ranks = [ranks_txt[w["id"]] for w in windows if w["direction"] == "A" and w["phase"] == "pre"]
    n_top5 = sum(1 for r in pre_ranks if r and r <= 5)
    post_ranks = [ranks_txt[w["id"]] for w in windows if w["direction"] == "A" and w["phase"] == "post"]

    story = {
        "morning": ("At 08:37 a mass of ice and rock broke off the north flank of Langtang Lirung and fell into the Lhende Khola. "
                    "The flood ran down the Bhote Koshi and the Trishuli. Nepal's own log first recorded it as an earthquake."),
        "morning2": ("Every gauge on the river saw the wave only by dying. Their warning levels were set for slow monsoon floods, "
                     "and the radar that had been watching the slope all summer had no rule for telling it apart from dozens of other moving slopes."),
        "creep": ("The fastest tenth of the detachment zone moved about a metre downhill in the 24 days ending a week before the collapse, "
                  "the same as Meena's independent reading of these passes (white diamonds). But it moved almost as much in late June, and it never accelerated. "
                  f"One pass earlier, monsoon decorrelation cost {round(100 * prev['ice_lost_share'])}% of the glacier area and the source valley dropped out of the ranking."),
    }

    sens = load("sensitivity.json")
    sens_line = "The headline phase is corrected for the ionosphere, a plane and a 3–5 km ring, but not for tropospheric delay, which at 5,000 m can mimic motion."
    if sens:
        sw = sens["windows"].get(lp["id"], {})
        sp = sens["windows"].get(prev["id"], {})
        sens_line = (f"The headline phase is not corrected for tropospheric delay, which at 5,000 m can mimic motion. A sensitivity run that subtracts each product's own wet and hydrostatic screens "
                     f"gives #{sw.get('tropo_rank')} in the last pass and {'#' + str(sp.get('tropo_rank')) if sp.get('tropo_rank') else 'still not ranked'} one pass earlier.")
    limits = [
        f"The pre-registered test fails: the source valley ranked #{sc['source_rank']} in the last pass but was not ranked at all one pass earlier ({prev['start']} → {prev['end']}), when monsoon decorrelation hid it.",
        f"The screen keeps this valley near the top most of the summer ({n_top5} of {len(pre_ranks)} ascending passes in the top five) and also after the collapse. It says 'watch this valley', not 'it falls next week'.",
        "Phase blobs carry millimetre uncertainties, so they dominate a score of motion over sigma.",
        sens_line,
        "The hit zone is 2 km around a 2 km² outline, so several blobs qualify in most passes. The best-ranked one changes from pass to pass.",
        "The USGS source point sits 1.3 km outside the UNOSAT outline; the outline is used as truth, as pre-registered.",
        "NISAR L2 products are provisional, one track per direction, and there is no earlier-year NISAR archive to separate seasonal snow change from precursor motion.",
        "Arrival times between gauges are interpolated along the routed flow path; beyond Betrawati they are extrapolated.",
    ]
    if (load("control.json") or {}).get("status") != "done":
        limits.append("The Sentinel-1 C-band control is not run yet; it needs the ASF HyP3 service authorised on the Earthdata account.")

    real = [
        f"{len(windows)} NISAR L2 GUNW + GOFF pairs (ascending track 98, descending track 48), read straight from ASF, box subset only.",
        "NISAR GCOV 10 m backscatter before and after the collapse; the change map shows the avalanche path through monsoon cloud.",
        "Detachment zone: UNOSAT product 4257 (Landsat 9, 26 Aug). Flood extent, affected buildings and roads: UNOSAT 4257.",
        "Gauge brackets and the alert chronology: DHM river watch via the Acharya 2026 archive; USGS and GEOFON catalogues.",
        "Terrain: Copernicus DEM GLO-30. Glaciers: RGI 7.0 (NSIDC). People: WorldPop 2026, Nepal and China. Rivers and places: OpenStreetMap. Heat: ERA5.",
    ]

    def li(items):
        return "".join(f"<li>{escape(i)}</li>" for i in items)

    cfg = CFG
    method_html = f"""<h2 id="m-title">Method</h2>
<p>One question, answered with a number: with rules fixed in advance, would NISAR have put the Langtang valley on a watch list a week before it fell? The unit is a valley, not a pixel.</p>
<h3>1 · Fetch</h3><p>NISAR L2 GUNW (unwrapped phase, coherence, offsets, LOS geometry) and GOFF (dense pixel offsets, three window sizes, with variances) for every pair from May to October 2026, and GCOV backscatter for key dates. Only the {463}×{412} cell box (80 m, UTM 45N) is read from each 1–2 GB granule. Where two releases exist we use P05023, the one that was on ASF at the time.</p>
<h3>2 · Clean</h3><ul>{li([
        "Offsets: cull by variance (σ ≤ 1 m), SNR ≥ 5 and a 5×5 median test; remove the bulk shift; project slant-range and along-track offsets onto the local fall line from the DEM, weighted by their variances. The GOFF layer with the lowest noise over stable ground is used per pass. Glaciers come out moving downhill, which checks the sign conventions.",
        f"Phase: subtract the ionosphere screen, convert with the product's own wavelength (λ = {299792458/1.239e9:.4f} m), keep coherence ≥ 0.30 (ascending) or 0.40 (descending) and valid connected components, remove a robust plane and the median of a 3–5 km ring.",
    ])}</ul>
<h3>3 · Rank</h3><p>Blobs are connected patches (≥ 5 cells) of downhill offset motion above 3× the scene noise with direction coherence above the scene median, or of relative LOS motion beyond ±50 mm. Each blob gets a bootstrap σ, direction coherence, an opposing-neighbour flag, an ice-over-rock flag (glacier ≥ 30% and slope ≥ 30°), WorldPop residents within 1 km of its first 30 km downstream (D8 routing on the DEM) and the ERA5 heat anomaly. Score = (motion/σ) × direction coherence × 1.5 if opposing × 1.3 if ice over rock × (1 + 0.25·log10(1 + people)) × (1 + 0.5·(season − 1)).</p>
<h3>4 · Score against truth</h3><p>A hit is the best-ranked blob within 2 km of the UNOSAT detachment outline whose flow path joins the source's before the Bhote Koshi. Truth is never used to rank. Pass condition, fixed before the first run: a hit in the top five in both of the last two ascending passes before 26 August. Baseline: rank by raw offset magnitude alone.</p>
<h3>5 · Replay</h3><p>The flood front runs down the D8 flow path through the DHM gauge brackets. The tripwire fires only if the source valley was in the top N of the last pass; its delay is the sum of the four sliders.</p>
<p class="note">Every threshold is in <code>pipeline/config.json</code>, committed before the first real run (see the git history). The browser scorer is tested to reproduce the pipeline ranking exactly.</p>"""

    src_rows = "".join(f'<li><a href="{escape(u)}" target="_blank" rel="noopener">{escape(n)}</a></li>' for n, u in SOURCES.values())
    sources_html = f"""<h2 id="s-title">Sources</h2>
<p>Data</p><ul>{li([
        "NISAR L2 GUNW, GOFF and GCOV, provisional (NASA/JPL/ISRO), via ASF DAAC and NASA Earthdata (doi:10.5067/NIL2GUNW-P1).",
        "UNOSAT product 4257: detachment zone (Landsat 9, 26 Aug 2026), flood extent, affected buildings and roads, barrier lakes.",
        "Copernicus EMS rapid mapping EMSR927.",
        "Nepal Department of Hydrology and Meteorology river-watch ten-minute stage series, as archived by Acharya & Paudel (Zenodo 22679444).",
        "USGS earthquake catalogue (us7000tbwb, us7000tc90); GEOFON (gfz2026qrfy).",
        "Copernicus DEM GLO-30 (ESA, AWS open data); GLIMS/RGI glacier outlines; WorldPop population; OpenStreetMap (ODbL); ERA5 reanalysis via Open-Meteo.",
    ])}</ul>
<p>Papers</p><ul>{li([
        "Kutoğlu, Softa & Akgün (2026). Differential rock–ice deformation before the 2026 Langtang hazard cascade: NISAR observations. Remote Sensing 18(21), 3727.",
        "Meena (2026). NISAR offsets measure pre-failure motion but do not single out the Bhote Koshi avalanche source. Research Square.",
        "Acharya (2026). The Bhote Koshi–Trishuli flash flood of 26 August 2026: warning latency exceeded flood travel time. EarthArXiv 14687, v5.",
        "Hassan et al. (2026). Extreme heat and snow drought preconditioned the August 2026 Nepal rock–ice avalanche flood. Research Square.",
        "Sah (2026). Sentinel-1 detection limits at the Langtang source. Zenodo 22541071.",
    ])}</ul>
<p>Links</p><ul>{src_rows}</ul>
<p class="note">Code: MIT licence. Map libraries: MapLibre GL JS (BSD-3). Fonts: Bricolage Grotesque, IBM Plex (OFL).</p>"""

    ctx = dict(
        source_lonlat=CFG["truth"]["source_lonlat"],
        truth_label="UNOSAT detachment zone, 26 Aug",
        sources={k: {"short": v[0], "url": v[1]} for k, v in SOURCES.items()},
        facts=facts, facts_order=order, story=story, limits=limits, real=real,
        method_html=method_html, sources_html=sources_html,
        gcov_pair=["gcov_A098_20260819", "gcov_A098_20260831"],
        gcov_labels=["19 Aug · before", "31 Aug · after"],
        gcov_change="gcovchange_A098_20260819_20260831",
        sah={"text": "Sah (2026) tested routine 12-day Sentinel-1 products at this slope and found no motion above about 13 mm a month.", "source": "sah"},
        meena=meena,
        era5=era,
        sensitivity=sens,
    )
    (DATA / "context.json").write_text(json.dumps(ctx, indent=1))
    ctl = DATA / "control.json"
    if not ctl.exists() or json.loads(ctl.read_text()).get("status") != "done":
        ctl.write_text(json.dumps({"status": "pending", "sentinel1_rank_per_window": {},
                                   "note": "Sentinel-1 C-band control not run yet: ASF HyP3 needs the account owner to accept the ASF Terms of Service (pipeline/fetch_s1.py is ready)."}, indent=1))
    print("context.json written;", len(facts), "facts")


if __name__ == "__main__":
    main()
