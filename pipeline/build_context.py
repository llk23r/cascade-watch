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


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def dshort(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {d.strftime('%b')}"


def main():
    card, windows, replay = load("scorecard.json"), load("windows.json"), load("replay.json")
    ef = json.loads((RAW / "event" / "facts.json").read_text())["facts"]
    topn, hit_km = CFG["pass_condition"]["top_n"], CFG["truth"]["hit_radius_km"]
    event_utc = CFG["windows"]["event_utc"]
    asc = [w for w in windows if w["direction"] == "A"]
    pre = [w for w in asc if w["phase"] == "pre"]
    lp, prev = pre[-1], pre[-2]
    sc = card["windows"][lp["id"]]
    series = {r["id"]: r for r in (load("source_series.json") or [])}
    gf = ef["gauges_at_failure"]
    tm = ef["timing"]
    syab = next((v for v in replay["villages"] if v["name"] == "Syabrubesi"), {})
    on_asf = [w for w in pre if w.get("asf_processed") and w["asf_processed"] < event_utc]
    later = [w for w in pre if w not in on_asf]
    days_before = (__import__("datetime").date.fromisoformat(event_utc[:10]) - __import__("datetime").date.fromisoformat(lp["end"])).days
    hours_on_asf = (__import__("datetime").datetime.fromisoformat(event_utc.replace("Z", "+00:00")) -
                    __import__("datetime").datetime.fromisoformat(lp["asf_processed"].replace("Z", "+00:00"))).total_seconds() / 3600

    def gline(key):
        v = gf[key]["value"]
        return f"{round(v['fraction'] * 100)}%"

    facts = {
        "alert": dict(display=f"{tm['alert_latency_sms_min']['value']:.1f}", unit="min", tone="late", source="acharya",
                      label=f"from the collapse to the mass SMS alert ({ef['timing']['sms_count']['value']:,} messages)" if "sms_count" in tm else "from the collapse to the mass SMS alert"),
        "rasuwa": dict(display=f"{replay['gauges'][0]['arrival_lo']:.0f}–{replay['gauges'][0]['arrival_hi']:.0f}", unit="min", tone="late", source="acharya",
                       label=f"for the wave to reach the Rasuwagadhi gauge; its last reading was {gline('rasuwagadhi')} of its warning level, then it went silent"),
        "syabru": dict(display=f"{replay['gauges'][1]['arrival_lo']:.0f}–{replay['gauges'][1]['arrival_hi']:.0f}", unit="min", tone="late", source="acharya",
                       label=(f"to reach Syabrubesi: {abs(syab['lead_time']['range'][1])} to {abs(syab['lead_time']['range'][0])} minutes before the alert" if syab.get("lead_time") else "to reach Syabrubesi")),
        "passes": dict(display=str(len(pre)), unit="pairs", tone="ok", source="nisar",
                       label=f"NISAR ascending pairs over the source before the collapse; {len(on_asf)} were public on ASF before it, {len(later)} were processed later"),
    }
    order = ["alert", "rasuwa", "syabru", "passes"]
    cas = ef["casualties"]["usgs_latest"]
    v = cas["value"]
    facts["dead"] = dict(display=f"{v['nepal_killed']:,}", unit="dead", source="usgs_impact",
                         label=f"at least, with {v['nepal_missing']:,} missing in Nepal and {v['nepal_houses_destroyed']:,} houses destroyed (USGS, {dshort(v['as_of'])})", tone="")
    order.insert(0, "dead")
    SOURCES["usgs_impact"] = ("USGS", cas["source_url"].split(" ")[0])
    SOURCES["sah"] = ("Sah 2026", ef["sentinel1_sah"]["detection_bound_48d"]["source_url"].split(" ")[0])
    SOURCES["kpost"] = ("Kathmandu Post (not archived)", "https://kathmandupost.com/national/2026/08/27/how-the-bhotekoshi-flood-unfolded-over-10-hours")

    era = None
    ep = RAW / "ancillary" / "era5" / "era5_source_point.json"
    if ep.exists():
        E = json.loads(ep.read_text())
        run = E.get("primary_run", "era5_default_elev")
        r = E["runs"][run]
        raw = json.loads(Path(find(f"openmeteo_raw_{run}.json")).read_text())
        a, rk = r["anomaly_2026_minus_clim"], r["percentile_rank_2026_among_1991_2026"]
        alt = E["runs"].get("era5_elev5190")
        win = E["window"]
        era = dict(t2026=r["y2026"]["mean_temp_c"], clim=r["climatology_1991_2020"]["mean_temp_c"], anom=a["mean_temp_c"], z=a["mean_temp_c_z"],
                   rank=rk["mean_temp_c"]["rank_highest_first"], n=rk["mean_temp_c"]["n_years"], snow_pct=a["snowfall_cm_pct_of_normal"],
                   elev_m=raw.get("elevation"), point=[E["point"]["lon"], E["point"]["lat"]], grid=r.get("grid_cell"),
                   snow_pct_alt=(alt or {}).get("anomaly_2026_minus_clim", {}).get("snowfall_cm_pct_of_normal"),
                   years=[[y.get("year"), y.get("mean_temp_c")] for y in r["per_year"]],
                   span=f"{dshort('2026-' + win['start'])}–{dshort('2026-' + win['end'])}", ordinal=ordinal(rk["mean_temp_c"]["rank_highest_first"]),
                   y0=r["per_year"][0].get("year"))

    # Meena 2026, Table S4 (CC BY 4.0), read straight from the archived package
    meena = []
    t4 = next(iter(sorted((RAW / "event" / "zenodo").rglob("TableS4_site_series.json"))), None)
    if t4:
        T = json.loads(t4.read_text())
        rows = T if isinstance(T, list) else T.get("rows", T.get("data", []))
        for r in rows:
            pair = str(r.get("pair") or r.get("Pair") or "")
            if "-" not in pair:
                continue
            import re as _re
            a_, b_ = _re.findall(r"\d{8}", pair)[:2]
            val = r.get("crown_disp_m", r.get("crown_median_disp_m"))
            if val is None:
                continue
            meena.append(dict(id=f"A098_{a_}_{b_}", disp_m=float(val), days=r.get("days"), stat="crown median"))

    ranks = {w["id"]: card["windows"][w["id"]]["source_rank"] for w in windows}
    pre_ranks = [ranks[w["id"]] for w in pre]
    n_top = sum(1 for r in pre_ranks if r and r <= topn)
    post = [w for w in asc if w["phase"] in ("co", "post")]
    post_txt = ", ".join(f"#{ranks[w['id']]}" if ranks[w["id"]] else "not ranked" for w in post)

    # why the source valley is missing in the pass before the last: from the numbers, not a guess
    ps, ls_ = series.get(prev["id"], {}), series.get(lp["id"], {})
    k_sig = CFG["offsets"]["motion_threshold_sigma"]
    cut = k_sig * prev["offsets_noise_m"]
    noisiest = prev["offsets_noise_m"] >= max(w["offsets_noise_m"] for w in pre)
    coh_rank = sorted([series.get(w["id"], {}).get("phase_coh_median") or 0 for w in pre], reverse=True).index(ps.get("phase_coh_median") or 0) + 1
    why_missing = (f"In {dshort(prev['start'])} → {dshort(prev['end'])} no moving patch formed within {hit_km:g} km of the source. That pass had "
                   f"{'the noisiest offsets of the summer' if noisiest else 'noisy offsets'}: the {k_sig:g}σ cut was {cut:.2f} m, above the "
                   f"{ps.get('offsets_p90_m', float('nan')):.2f} m that the fastest tenth of the detachment zone moved. Phase coherence at the site was the "
                   f"{'best' if coh_rank == 1 else ordinal(coh_rank) + ' best'} of the {len(pre)} passes, but no phase motion there passed the ±{CFG['phase']['blob_threshold_mm']:g} mm threshold.")
    earlier = max((w for w in pre if w is not lp and series.get(w["id"], {}).get("offsets_p90_m") is not None), key=lambda w: series[w["id"]]["offsets_p90_m"])
    mm = {m["id"]: m["disp_m"] for m in meena}

    story = {
        "morning": (f"At {tm['collapse_npt']['value']} Nepal time a combined ice and bedrock mass broke away high on the Langtang massif; UNOSAT mapped the "
                    f"detachment zone at {ef['geometry']['detachment_area_unosat']['value']:.2f} km². The flood ran down the Bhote Koshi and the Trishuli. "
                    "Nepal's operational log first recorded it as an M4.4 earthquake."),
        "morning2": ("The three gauges nearest the source were destroyed before any of them crossed its warning level: their last readings were "
                     f"{gline('rasuwagadhi')}, {gline('syabrubesi')} and {gline('betrawati')} of warning. Gauges further down survived and recorded the flood. "
                     f"Radar had watched the slope all summer, but it was one of {sc['n_ranked']} moving patches in the box, with no rule to pick it out."),
        "creep": (f"In the {lp['stack_days']} days ending {days_before} days before the collapse ({dshort(lp['start'])} → {dshort(lp['end'])}), the fastest tenth of the "
                  f"detachment zone moved {ls_.get('offsets_p90_m', float('nan')):.2f} m downhill; the zone's median moved {ls_.get('offsets_median_m', float('nan')):.2f} m. "
                  + (f"Meena 2026 reports {mm[lp['id']]:.2f} m for the same pass, as the median over the crown, a smaller area, so it checks the size of the motion, not the exact number. " if lp["id"] in mm else "")
                  + f"It moved {series[earlier['id']]['offsets_p90_m']:.2f} m in {dshort(earlier['start'])} → {dshort(earlier['end'])} too, so the motion did not single out the last weeks. "
                  + why_missing),
    }

    sens = load("sensitivity.json")
    elev_txt = f"{era['elev_m']:,.0f} m" if era and era.get("elev_m") else "high altitude"
    sens_line = ""
    if sens:
        sw, sp = sens["windows"].get(lp["id"], {}), sens["windows"].get(prev["id"], {})
        sens_line = (f"The headline phase is not corrected for tropospheric delay, which at {elev_txt} can mimic motion. A sensitivity run that subtracts each product's own wet and hydrostatic screens "
                     f"gives #{sw.get('tropo_rank')} in the last pass and {'#' + str(sp.get('tropo_rank')) if sp.get('tropo_rank') else 'still not ranked'} one pass earlier.")
    geo = ef["geometry"]
    usgs_out = (geo.get("distances_to_unosat_detachment_computed") or {}).get("value", {}).get("usgs_epicentre_outside_by_km")
    limits = [
        f"The pre-registered test fails: the source valley ranked #{sc['source_rank']} in the last pass but was not ranked one pass earlier. " + why_missing,
        f"The screen keeps this valley near the top most of the summer ({n_top} of {len(pre)} ascending passes in the top {topn}) and after the collapse too ({post_txt}). It says 'watch this valley', not 'it falls next week'.",
        f"Only {len(on_asf)} of those {len(pre)} pairs were public on ASF before the collapse; {len(later)} ({', '.join(dshort(w['start']) + '→' + dshort(w['end']) for w in later)}) were processed afterwards. The last pass went public {hours_on_asf:.0f} hours before the collapse.",
        "The rules were written after the event, by people who had read post-event studies of it. Pre-registration only guards against tuning on our own results; the real test is other events.",
        "Phase patches carry millimetre uncertainties (the bootstrap error of the patch mean, which shrinks with patch size), so large phase patches dominate a score of motion over noise.",
        sens_line,
        f"The hit zone is {hit_km:g} km around a {geo['detachment_area_unosat']['value']:.1f} km² outline, so several patches qualify in most passes, and the one that counts changes from pass to pass.",
        (f"The USGS source point sits {usgs_out:.1f} km outside the UNOSAT outline; the outline is used as truth, as pre-registered. The flow path and the same-channel test start at the USGS point." if usgs_out else
         "The flow path and the same-channel test start at the USGS source point, which lies outside the UNOSAT outline."),
        "NISAR L2 products are provisional, one track per direction, and there is no earlier-year NISAR archive to separate seasonal snow change from precursor motion.",
        "Arrival times between gauges are interpolated along the routed flow path and differ from some reported times (Timure); beyond Betrawati they are extrapolated.",
        f"ERA5 is a 0.25° grid cell at {elev_txt}. The temperature anomaly is stable across elevations; the snowfall figure is not ({era['snow_pct']:.0f}% of normal here, {era['snow_pct_alt']:.1f}% at 5,190 m)." if era and era.get("snow_pct_alt") is not None else "",
        "A few implementation constants live in code, not in config.json: the 30% glacier fraction for ice over rock, the 10 mm gate for opposing neighbours, the season mapping 1 + 0.25·z (clipped 0.5–1.5) from 1 May, and the 30% valid-share and 20° stable-ground cuts.",
    ]
    limits = [x for x in limits if x]
    if (load("control.json") or {}).get("status") != "done":
        limits.append("The Sentinel-1 C-band control is not run yet; it needs the ASF HyP3 service authorised on the Earthdata account.")

    man = json.loads((RAW / "nisar" / "manifest.json").read_text())
    tracks = sorted({(v["direction"], v["track"]) for v in man.values() if v.get("track")})
    gcov = sorted(k for k in man if k.startswith("GCOV_A"))
    collection = sorted({v["url"].split("/")[4] for v in man.values() if v.get("url") and v["level"] == "GUNW"})
    real = [
        f"{len(windows)} NISAR L2 GUNW + GOFF pairs (" + ", ".join(f"{'ascending' if d == 'A' else 'descending'} track {t}" for d, t in tracks) + "), read from ASF, box subset only.",
        f"NISAR GCOV backscatter for {len(gcov)} ascending dates; the change map between {dshort(gcov[1][-8:-4] and '2026-' + gcov[1][-4:-2] + '-' + gcov[1][-2:])} and {dshort('2026-' + gcov[2][-4:-2] + '-' + gcov[2][-2:])} shows the avalanche path through monsoon cloud." if len(gcov) >= 3 else "NISAR GCOV backscatter before and after the collapse.",
        "Detachment zone, flood extent, affected buildings and roads: UNOSAT product 4257.",
        "Gauge series and the alert chronology: DHM river watch via the Acharya & Paudel archive (Zenodo 22679444); USGS and GEOFON catalogues.",
        "Terrain: Copernicus DEM GLO-30. Glaciers: RGI 7.0. People: WorldPop 2026. Rivers and places: OpenStreetMap. Heat: ERA5 via Open-Meteo.",
    ]
    lam = sorted({round(json.loads(p.read_text()).get("lambda_m", 0), 4) for p in (RAW / "products").glob("*.json")} - {0})
    from grid import NX, NY
    def li(items):
        return "".join(f"<li>{escape(i)}</li>" for i in items)

    cfg = CFG
    method_html = f"""<h2 id="m-title">Method</h2>
<p>One question, answered with a number: with rules fixed in advance, would NISAR have put the Langtang valley on a watch list a week before it fell? The unit is a valley, not a pixel.</p>
<h3>1 · Fetch</h3><p>NISAR L2 GUNW (unwrapped phase, coherence, offsets, LOS geometry) and GOFF (dense pixel offsets, three window sizes, with variances) for every pair from {dshort(min(w['start'] for w in windows))} to {dshort(max(w['end'] for w in windows))} 2026, and GCOV backscatter for key dates. Only the {NX}×{NY} cell box ({CFG['grid']['cell_m']} m, UTM 45N) is read from each granule. Where two releases exist we use P05023, the composite that was on ASF before the collapse; {len(later)} of the {len(pre)} ascending pre-collapse pairs exist only as X05026, processed after it.</p>
<h3>2 · Clean</h3><ul>{li([
        f"Offsets: cull by variance (σ ≤ {CFG['offsets']['cull_max_sigma_m']:g} m), SNR ≥ {CFG['offsets']['cull_min_snr']:g} and a {CFG['offsets']['cull_local_window_cells']}×{CFG['offsets']['cull_local_window_cells']} median test; remove the bulk shift; project slant-range and along-track offsets onto the local fall line from the DEM, weighted by their variances. The GOFF layer with the lowest noise over stable ground is used per pass. Glaciers come out moving downhill, which checks the sign conventions.",
        f"Phase: subtract the ionosphere screen, convert with the product's own wavelength (λ = {', '.join(f'{x:.4f}' for x in lam)} m), keep coherence ≥ {CFG['phase']['coherence_floor_ascending']:g} (ascending) or {CFG['phase']['coherence_floor_descending']:g} (descending) and valid connected components, remove a robust plane and the median of a {CFG['phase']['ring_inner_km']:g}–{CFG['phase']['ring_outer_km']:g} km ring.",
    ])}</ul>
<h3>3 · Rank</h3><p>Patches are connected groups (≥ {CFG['blobs']['min_cells']} cells) of downhill offset motion above {CFG['offsets']['motion_threshold_sigma']:g}× the scene noise with direction coherence above the scene median, or of relative LOS motion beyond ±{CFG['phase']['blob_threshold_mm']:g} mm. Each patch gets a bootstrap σ, direction coherence, an opposing-neighbour flag (within {CFG['blobs']['opposing_sign_radius_km']:g} km), an ice-over-rock flag (glacier ≥ 30% and slope ≥ {CFG['blobs']['ice_over_rock_min_slope_deg']:g}°), WorldPop residents within {CFG['blobs']['people_buffer_km']:g} km of its first {CFG['blobs']['people_path_km']:g} km downstream (D8 routing on the DEM) and the ERA5 heat anomaly. Score = {escape(CFG['score']['formula'])}, with opposing bonus {CFG['score']['opposing_bonus']:g}, ice bonus {CFG['score']['ice_bonus']:g}, w_pop {CFG['score']['w_pop']:g}, w_season {CFG['score']['w_season']:g}.</p>
<h3>4 · Score against truth</h3><p>A hit is the best-ranked patch within {hit_km:g} km of the UNOSAT detachment outline whose flow path joins the source's before the main river. Truth is never used to rank. Pass condition, fixed before the first run: {escape(CFG['pass_condition']['rule'])}. Baseline: {escape(CFG['pass_condition']['baseline'])}.</p>
<h3>5 · Replay</h3><p>The flood front runs down the D8 flow path through the DHM gauge brackets. The tripwire fires only if the source valley was in the top N of the last pass; its delay is the sum of the four sliders.</p>
<p class="note">The thresholds and weights above are in <code>pipeline/config.json</code>, committed before the first results (see Data, and the git history). A few implementation constants live in <code>rank.py</code> and <code>clean.py</code>; they are listed under Limits. The browser scorer is tested to reproduce the pipeline ranking exactly.</p>"""

    src_rows = "".join(f'<li><a href="{escape(u)}" target="_blank" rel="noopener">{escape(n)}</a></li>' for n, u in SOURCES.values())
    sources_html = f"""<h2 id="s-title">Sources</h2>
<p>Data</p><ul>{li([
        f"NISAR L2 GUNW, GOFF and GCOV, provisional (NASA/JPL/ISRO), via ASF DAAC and NASA Earthdata (collection {', '.join(collection)}).",
        "UNOSAT product 4257: detachment zone (Landsat 9, 26 Aug 2026), flood extent, affected buildings and roads, barrier lakes.",
        "Copernicus EMS rapid mapping EMSR927.",
        "Nepal Department of Hydrology and Meteorology river-watch ten-minute stage series, as archived by Acharya & Paudel (Zenodo 22679444).",
        "USGS earthquake catalogue (us7000tbwb, us7000tc90); GEOFON (gfz2026qrfy).",
        "Copernicus DEM GLO-30 (ESA, AWS open data); GLIMS/RGI glacier outlines; WorldPop population; OpenStreetMap (ODbL); ERA5 reanalysis via Open-Meteo.",
    ])}</ul>
<p>Papers</p><ul>{li([
        "Kutoğlu, Softa & Akgün (2026). Remote Sensing 18(21), 3727 (NISAR observations of differential rock–ice deformation before the Langtang cascade).",
        "Meena (2026). NISAR offsets measure pre-failure motion but do not single out the Bhote Koshi avalanche source. Research Square.",
        "Acharya (2026). The Bhote Koshi–Trishuli flash flood of 26 August 2026: warning latency exceeded flood travel time. EarthArXiv 14687, v5.",
        "Hassan et al. (2026). Extreme heat and snow drought preconditioned the August 2026 Nepal rock–ice avalanche flood. Research Square.",
        f"Sah (2026). Sentinel-1 analysis of the source slope, code and data release: {SOURCES['sah'][1]}.",
    ])}</ul>
<p>Links</p><ul>{src_rows}</ul>
<p class="note">Code: MIT licence. Map library: MapLibre GL JS (BSD-3). The Kathmandu Post article is cited from Acharya's archive; the article itself was not saved.</p>"""

    ctx = dict(
        source_lonlat=CFG["truth"]["source_lonlat"],
        truth_label="UNOSAT detachment zone, 26 Aug",
        sources={k: {"short": v[0], "url": v[1]} for k, v in SOURCES.items()},
        facts=facts, facts_order=order, story=story, limits=limits, real=real,
        method_html=method_html, sources_html=sources_html,
        gcov_pair=["gcov_" + k[5:] for k in gcov[1:3]],
        gcov_labels=[dshort("2026-" + gcov[1][-4:-2] + "-" + gcov[1][-2:]) + " · before", dshort("2026-" + gcov[2][-4:-2] + "-" + gcov[2][-2:]) + " · after"],
        gcov_change="gcovchange_" + gcov[1][5:] + "_" + gcov[2][-8:],
        sah={"text": f"Sah (2026) tested routine Sentinel-1 products at this slope and found no motion above {ef['sentinel1_sah']['detection_bound_48d']['value']:g} mm a month (48-day pairs).", "source": "sah"},
        timing=dict(days_before=days_before, hours_on_asf=round(hours_on_asf, 1), last_pass=lp["id"], on_asf_before=len(on_asf), n_pre=len(pre)),
        why_missing=why_missing,
        detachment_km2=ef['geometry']['detachment_area_unosat']['value'],
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
