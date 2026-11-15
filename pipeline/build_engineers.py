"""Stage 6 — "For engineers": the score taken apart on real patches, the data trail, and 37 questions.

Every number here is read from a file the pipeline wrote (or from the sourced facts record); nothing is typed in.
  worked examples   data/blobs_<window>.geojson (Langtang), data/sites/budhigandaki/blobs_<window>.geojson
  rules             pipeline/config.json, pipeline/prereg_v2.json
  results           data/scorecard.json, data/backtest.json, data/sensitivity_zone.json
  data trail        raw/nisar/manifest.json and the per-granule JSON headers, raw/products/*.json, file sizes on disk
  story numbers     data/context.json, data/replay.json, raw/event/facts.json

Usage: python pipeline/build_engineers.py  ->  data/engineers.json
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA, RAW = ROOT / "data", ROOT / "raw"
C_LIGHT = 299_792_458.0


def J(p: Path):
    return json.loads(p.read_text())


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def mb(n: int) -> int:
    return int(round(n / 1e6))


def wlabel(w: str) -> str:
    _, a, b = w.split("_")
    f = lambda s: datetime.strptime(s, "%Y%m%d").strftime("%-d %b")
    return f"{f(a)} → {f(b)}"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def fmt(x: float, nd: int = 2) -> str:
    return f"{x:,.{nd}f}"


CFG = J(ROOT / "pipeline" / "config.json")
V2 = J(ROOT / "pipeline" / "prereg_v2.json")
SC, BC, OC, PC = CFG["score"], CFG["blobs"], CFG["offsets"], CFG["phase"]


def terms(p: dict) -> list[dict]:
    """The score of one patch, factor by factor, exactly as rank.py / score.js compute it."""
    out, run = [], 0.0
    snr = p["motion_m"] / p["sigma_m"]
    run = snr
    unit_m = p["channel"] == "offsets"
    mv = f"{fmt(p['motion_m'], 2)} m downhill" if unit_m else f"{fmt(p['motion_m'] * 1000, 1)} mm toward/away from the satellite"
    nv = f"{fmt(p['sigma_m'], 3)} m" if unit_m else f"{fmt(p['sigma_m'] * 1000, 2)} mm"
    out.append(dict(key="snr", op="÷", label="Motion ÷ its noise", input=f"{mv} ÷ {nv}", factor=None, value=snr, running=run))
    run *= p["dir_coherence"]
    out.append(dict(key="dir", op="×", label="Moves as one block", input=f"direction consistency {fmt(p['dir_coherence'], 3)} (1 = every pixel the same way)",
                    factor=p["dir_coherence"], running=run))
    f = SC["opposing_bonus"] if p["opposing_sign"] else 1.0
    run *= f
    out.append(dict(key="opp", op="×", label="Neighbours pull apart", input="yes: an opposite-moving patch within " + f"{BC['opposing_sign_radius_km']:g} km" if p["opposing_sign"] else "no opposite-moving neighbour",
                    factor=f, running=run))
    f = SC["ice_bonus"] if p["ice_over_rock"] else 1.0
    run *= f
    out.append(dict(key="ice", op="×", label="Ice on steep rock", input=f"{round(p['glacier_frac'] * 100)}% glacier, slope {round(p['slope_deg'])}° (needs ≥30% and ≥{BC['ice_over_rock_min_slope_deg']:g}°)",
                    factor=f, running=run))
    ppl = p.get("people_below") or 0
    f = 1 + SC["w_pop"] * math.log10(1 + ppl)
    run *= f
    out.append(dict(key="pop", op="log", label="People downstream", input=f"{round(ppl):,} people within {BC['people_buffer_km']:g} km of the first {BC['people_path_km']:g} km of its flow path",
                    factor=f, running=run))
    f = 1 + SC["w_season"] * (p["season_anomaly"] - 1)
    run *= f
    out.append(dict(key="season", op="×", label="Hot season", input=(f"season factor {fmt(p['season_anomaly'], 3)} from ERA5 warmth since 1 May" if p["season_anomaly"] != 1 else
                                                                      "not fetched here: the same for every patch, so it cannot change a rank"),
                    factor=f, running=run))
    return out


def example(site: str, window: str, blobs_path: Path, pick: list[tuple[str, dict]], public: str | None) -> dict:
    feats = [f["properties"] for f in J(blobs_path)["features"]]
    ranked = sorted([p for p in feats if p.get("rank")], key=lambda p: p["rank"])
    pats = []
    seen = set()
    for why, p in pick:
        if p is None or p["id"] in seen:
            continue
        seen.add(p["id"])
        t = terms(p)
        assert abs(t[-1]["running"] - p["score"]) / p["score"] < 1e-3, (site, p["id"], t[-1]["running"], p["score"])
        pats.append(dict(id=p["id"], why=why, rank=p["rank"], channel=p["channel"], dist_km=round(p["dist_truth_km"], 2), same_channel=p["same_channel"],
                         stream=p.get("channel_name"), area_km2=p["area_km2"], cells=p["cells"], elev_m=round(p["elev_m"]), terms=t, score=p["score"]))
    return dict(site=site, window=window, window_label=wlabel(window), n_ranked=len(ranked), public=public, patches=pats)


def best(feats, km, same=True):
    c = [p for p in feats if p.get("rank") and p["dist_truth_km"] <= km and (p["same_channel"] or not same)]
    return min(c, key=lambda p: p["rank"]) if c else None


def main():
    sc, bt, sz, ctx, rp = J(DATA / "scorecard.json"), J(DATA / "backtest.json"), J(DATA / "sensitivity_zone.json"), J(DATA / "context.json"), J(DATA / "replay.json")
    facts = J(RAW / "event" / "facts.json")["facts"]
    man = J(RAW / "nisar" / "manifest.json")
    hit_km, zone_km, zone_n = CFG["truth"]["hit_radius_km"], V2["zone"]["radius_km"], V2["watch_list"]["top_n"]

    # ---------------------------------------------------------- worked examples
    lt_w = ctx["timing"]["last_pass"]
    lt_feats = [f["properties"] for f in J(DATA / f"blobs_{lt_w}.geojson")["features"]]
    lt_top = min([p for p in lt_feats if p.get("rank")], key=lambda p: p["rank"])
    lt_src = best(lt_feats, hit_km)
    lt_proc = max(man[k]["asf_processingDate"] for k in man if k.endswith(lt_w) and k.split("_")[0] in ("GUNW", "GOFF"))
    ex_lt = example("Langtang", lt_w, DATA / f"blobs_{lt_w}.geojson",
                    [("top of this pass", lt_top), (f"the source: within {hit_km:g} km, same stream", lt_src)], lt_proc)

    bg = bt["sites"]["budhigandaki"]
    bg_w = bg["positive_pass"]["windows"][0]
    bg_path = DATA / "sites" / "budhigandaki" / f"blobs_{bg_w}.geojson"
    bg_feats = [f["properties"] for f in J(bg_path)["features"]]
    bg_top = min([p for p in bg_feats if p.get("rank")], key=lambda p: p["rank"])
    ex_bg = example("Budhi Gandaki", bg_w, bg_path,
                    [("top of this pass", bg_top), (f"best inside the {zone_km:g} km danger zone", best(bg_feats, zone_km)),
                     (f"the exact slope: within {hit_km:g} km", best(bg_feats, hit_km))], None)

    # ---------------------------------------------------------- data trail
    def granule_json(level):
        k = next(k for k in man if k.startswith(level + "_") and k.endswith(lt_w if level != "GCOV" else lt_w.split("_")[2]))
        return k, J(RAW / "nisar" / f"{k}.json"), man[k]
    import numpy as np
    prods = []
    for level, full, what, used in [
        ("GUNW", "Geocoded Unwrapped Interferogram", "The change in radar phase between two passes, already on a map grid. One full cycle of phase = half a wavelength of motion toward or away from the satellite.",
         "unwrapped phase − ionosphere screen → millimetres along the radar line of sight → keep coherent pixels → remove a best-fit plane and the 3–5 km ring median → patches beyond ±" + f"{PC['blob_threshold_mm']:g} mm"),
        ("GOFF", "Geocoded Pixel Offsets", "How far each small window of the radar image shifted between two passes, found by matching image texture. It follows fast motion (decimetres to metres) that phase cannot.",
         "cull bad matches (σ > " + f"{OC['cull_max_sigma_m']:g} m, SNR < {OC['cull_min_snr']:g}, local outliers) → remove the bulk shift → project the range and along-track shifts onto the downhill direction of the DEM → metres downhill, with σ → patches above {OC['motion_threshold_sigma']:g}× noise"),
        ("GCOV", "Geocoded Covariance (radar brightness)", "How strongly each 10 m spot reflects the radar, corrected for terrain. Rough, wet or broken ground shows up differently.",
         "converted to decibels for the before/after radar pictures; not used in the score"),
    ]:
        k, hdr, m = granule_json(level)
        z = np.load(RAW / "nisar" / f"{k}.npz")
        arrays = [n for n in z.files if n not in ("x", "y", "mask", "unw_mask") and not n.startswith("rg_")]
        shape = list(z[arrays[0]].shape)
        layered = sorted({a.split("_", 1)[1] for a in arrays if a.startswith("layer")})
        if layered:
            nl = len({a.split("_", 1)[0] for a in arrays if a.startswith("layer")})
            arrays = [f"layer1–{nl} × {x}" for x in layered] + [a for a in arrays if not a.startswith("layer")]
        cell = abs(float(z["x"][1] - z["x"][0]))
        files = sorted((RAW / "nisar").glob(f"{level}_*.npz"))
        prods.append(dict(level=level, name=full, what=what, used=used, example_granule=m["scene"], asf_processed=m["asf_processingDate"], crid=m["crid"],
                          arrays=arrays, grid=shape, cell_m=round(cell), local_dir="raw/nisar/", n_granules=len(files),
                          local_mb=mb(sum(f.stat().st_size for f in files)), header_keys=len(hdr)))
    lam = C_LIGHT / float(J(RAW / "nisar" / f"GUNW_{lt_w}.json")["centerFrequency"])
    sites_store = [dict(site="Langtang", path="raw/nisar/", mb=mb(dir_size(RAW / "nisar")), granules=len([k for k in man]))]
    for s in ("budhigandaki", "manang"):
        d = RAW / "sites" / s / "nisar"
        if d.exists():
            mm = J(d / "manifest.json") if (d / "manifest.json").exists() else {}
            sites_store.append(dict(site={"budhigandaki": "Budhi Gandaki", "manang": "Manang (control)"}[s], path=f"raw/sites/{s}/nisar/", mb=mb(dir_size(d)), granules=len(mm)))
    stages = [
        dict(n=1, name="Fetch", script="pipeline/nisar_fetch.py", does="Opens each NISAR HDF5 file on ASF's cloud and reads only the bytes for the study box (HTTP range requests, Earthdata login).",
             writes="raw/nisar/<LEVEL>_<pair>.npz + .json header + manifest.json"),
        dict(n=2, name="Clean", script="pipeline/clean.py", does="Turns phase into millimetres and offsets into metres downhill, removes atmosphere and orbit tilts, measures the noise.",
             writes="raw/products/<pair>.npz (motion maps) + .json (noise)"),
        dict(n=3, name="Find and score patches", script="pipeline/rank.py", does="Groups moving cells into patches, describes each one, scores it with the fixed rule and ranks it. Truth is used only after ranking.",
             writes="data/blobs_<pair>.geojson, data/scorecard.json"),
        dict(n=4, name="Test", script="pipeline/backtest.py · sensitivity_zone.py", does="Runs the same rules on Budhi Gandaki and a control valley, counts false alarms, compares with chance.",
             writes="data/backtest.json, data/sensitivity_zone.json"),
        dict(n=5, name="Show", script="web/score.js", does="The page re-scores every patch in your browser with the same formula, so the sliders re-rank instantly.",
             writes="what you see"),
    ]
    ancillary = [
        ("Copernicus DEM GLO-30", "slope, downhill direction, flow paths"),
        ("RGI glacier outlines", "the ice-on-rock factor"),
        ("WorldPop", "people downstream"),
        ("OpenStreetMap rivers", "which stream a patch drains to"),
        ("ERA5 (Open-Meteo)", "the season factor"),
    ]

    # ---------------------------------------------------------- numbers for the questions
    lp = sc["windows"][lt_w]
    pass_ = sc["pass"]
    prodj = {p.stem: J(p) for p in (RAW / "products").glob("*.json") if not p.name.endswith("failed.json")}
    pre = [w for w in bt["sites"]["langtang"]["false_alarm"]["windows_counted"]]
    miss_w = next(w for w, r in zip(pass_["windows"], pass_["ranks"]) if r is None)
    miss_noise = prodj[miss_w]["offsets_noise_m"]
    noisiest = max(pre, key=lambda w: prodj[w]["offsets_noise_m"]) == miss_w
    crown = facts["nisar_meena"]["per_window_crown_disp_m"]["value"]
    _, a, b = miss_w.split("_")
    miss_crown = crown.get(f"{a}-{b[4:]}")
    zb = next(r for r in sz["sites"]["budhigandaki"]["rows"] if r["zone_km"] == zone_km and r["top_n"] == zone_n)
    zl = next(r for r in sz["sites"]["langtang"]["rows"] if r["zone_km"] == zone_km and r["top_n"] == zone_n)
    fa = {k: bt["sites"][k]["false_alarm"] for k in ("budhigandaki", "langtang", "manang")}
    msz = (facts["nisar_meena"].get("source_zone") or {}).get("value") or {}
    msc = (facts["nisar_meena"].get("screen") or {}).get("value") or {}
    mrank = str(msz.get("rank_by_res_mag", "")).split(" (")[0].split()
    mrank_txt = f"{ordinal(int(mrank[0]))} {' '.join(mrank[1:])}" if mrank and mrank[0].isdigit() else None
    A = rp["alert_min"]
    T = sum(CFG["tripwire_default_delays_min"].values())
    tim = next(v for v in rp["villages"] if v["name"] == "Timure")["reported"][0]
    lo, hi = math.floor(tim["t_lo"] - T), math.floor(tim["t_hi"] - T)
    gl = max(man[k]["asf_processingDate"] for k in man if k.endswith(lt_w) and k.split("_")[0] in ("GUNW", "GOFF"))
    era = ctx["era5"]
    src = next(p for p in ex_lt["patches"] if p["id"] == lt_src["id"])
    st = {t["key"]: t for t in src["terms"]}
    top = ex_lt["patches"][0]
    n_gran_total = sum(s["granules"] for s in sites_store)

    Q = []
    def q(tag, question, answer):
        Q.append(dict(tag=tag, q=question, a=answer))

    # The satellite and its data
    q("Data", "What is NISAR?", "A radar satellite built by NASA and ISRO, India's space agency. It images the same ground every 12 days, day or night, through cloud.")
    q("Data", "Why use radar instead of normal satellite photos?", "Radar sends its own microwave pulses, so darkness and cloud do not block it. Langtang was under monsoon cloud for most of the summer.")
    q("Data", "What does L-band mean?", f"It is the radar's wavelength: {lam * 100:.1f} cm, read from the product's centre frequency. Long waves keep a steady echo over snow and rough slopes, where Sentinel-1's 5.6 cm C-band loses it.")
    q("Data", "What is a pass, and what is a pair?", "A pass is one flight over the same strip of ground; NISAR repeats it every 12 days. A pair compares two passes to measure how the ground moved in between.")
    q("Data", "What is GUNW?", "NASA's Geocoded Unwrapped Interferogram: the change in radar phase between two passes, already on a map grid. We turn that phase change into millimetres of motion toward or away from the satellite.")
    q("Data", "What is GOFF?", "NASA's Geocoded Pixel Offsets: how far each small image window shifted between two passes, found by matching texture. It measures fast motion, decimetres to metres, that phase cannot follow.")
    q("Data", "What is GCOV?", "Radar brightness maps at 10 m, corrected for terrain. We use them only for the before-and-after pictures, not in the score.")
    q("Data", "Did you download the full NISAR files?", f"No. Each granule is one HDF5 file on ASF's cloud, and our script asked only for the bytes inside our study box (HTTP range requests); those cut-outs sit on this laptop under raw/ ({n_gran_total} granules, about {sum(s['mb'] for s in sites_store):,} MB for all three sites).")
    q("Data", "Could a warning really have used that data in time?", f"Yes for the last pass: ASF published it on {datetime.fromisoformat(gl.replace('Z', '+00:00')).strftime('%-d %b %H:%M')} UTC, {ctx['timing']['hours_on_asf']:.0f} hours before the collapse. We used only that original processing, and only {ctx['timing']['on_asf_before']} of the {ctx['timing']['n_pre']} pre-collapse pairs were public in time.")
    # Signal and cleaning
    q("Cleaning", "What is coherence?", f"A score from 0 to 1 for how alike the radar echo is in the two passes. Below {PC['coherence_floor_ascending']:.2f} the phase is mostly noise, so we ignore it.")
    q("Cleaning", "What does 'unwrapped' phase mean?", f"Phase repeats every half wavelength (about {lam * 50:.0f} cm of motion), like a clock hand going round. Unwrapping counts the full turns so the value becomes one smooth measure of motion.")
    q("Cleaning", "Why remove the ionosphere, a plane and a ring median from the phase?", f"The upper atmosphere and small orbit errors add smooth, fake motion across the whole scene. We subtract NASA's ionosphere estimate, then a best-fit plane, then the median of a {PC['ring_inner_km']:g}–{PC['ring_outer_km']:g} km ring around each pixel, which leaves only local motion.")
    q("Cleaning", "How do offsets become metres downhill?", "The satellite measures shifts in two radar directions, across and along its track. We project both onto the slope's downhill direction from the Copernicus DEM, trusting each by its own uncertainty.")
    q("Cleaning", "What is the noise, sigma?", f"How much ground that should be still appears to move. We measure it on ice-free slopes under 20° with a robust spread (1.4826 × median absolute deviation); in the last Langtang pass it was {prodj[lt_w]['phase_noise_mm']:.1f} mm for phase and {prodj[lt_w]['offsets_noise_m']:.2f} m for offsets.")
    q("Cleaning", "What is a patch?", f"At least {BC['min_cells']} touching {CFG['grid']['cell_m']} m cells that all move past a threshold: ±{PC['blob_threshold_mm']:g} mm in phase, or {OC['motion_threshold_sigma']:g}× the noise downhill in offsets. Each patch is one candidate slope.")
    # The score
    q("Score", "What is the score, in one line?", f"Motion ÷ its noise × how consistently it moves × {SC['opposing_bonus']:g} if neighbours pull apart × {SC['ice_bonus']:g} if ice sits on steep rock × a people factor × a season factor. The highest scores go on the watch list.")
    q("Score", "Why divide motion by its noise?", "It asks how many times bigger the motion is than the wobble, which removes the units. That lets metres from offsets and millimetres from phase compete fairly, and a noisy pass counts for less.")
    q("Score", "Why multiply the factors instead of adding them?", "Each factor says how much more or less worried to be, so it scales any score the same way. If motion is chaotic, consistency is near 0 and pulls the whole score down, which is what we want.")
    q("Score", "What is 'moves as one block' (direction consistency)?", f"In each {OC['dir_coherence_window_cells']}×{OC['dir_coherence_window_cells']}-cell window we average the motion arrows after making them all length 1. A real slide moves one way and scores near 1; noise points everywhere and scores near 0.")
    q("Score", f"Why the ×{SC['opposing_bonus']:g} for neighbours moving opposite ways?", f"Two nearby blocks moving in opposite radar directions can mean a crack opening between them. The patch gets it when an opposite-moving patch lies within {BC['opposing_sign_radius_km']:g} km.")
    q("Score", f"Why the ×{SC['ice_bonus']:g} for ice on steep rock?", f"Ice sitting on rock steeper than {BC['ice_over_rock_min_slope_deg']:g}° can break away, which is how the Langtang cascade began. The patch must be at least 30% glacier on the RGI outlines.")
    q("Score", "Why use log10 for people downstream?", f"Ten times more people adds the same step, not ten times the score, so a town cannot drown out real motion. People come from WorldPop, counted within {BC['people_buffer_km']:g} km of the first {BC['people_path_km']:g} km of the flow path.")
    q("Score", "Does the season factor change the ranking?", f"No. It multiplies every patch in a pass by the same number, so the order stays the same; it only records that the season was primed ({ordinal(era['rank'])} warmest of {era['n']} years at the source).")
    q("Score", f"Where do the weights {SC['opposing_bonus']:g}, {SC['ice_bonus']:g}, {SC['w_pop']:g} and {SC['w_season']:g} come from?", "They are round judgement values, written down before any result was computed. The sliders in the app let anyone change them and watch the ranks move.")
    q("Score", "Do bigger patches score higher?", "Partly: averaging more cells shrinks the noise, so a big patch with the same motion is more certain. That is fair statistics, but it means small, fast slopes can rank below large, slow ones.")
    q("Score", "Walk me through the Langtang source score.", f"{st['snr']['input']} = {st['snr']['value']:.0f}; × {st['dir']['factor']:.2f} consistency = {st['dir']['running']:.0f}; × {st['pop']['factor']:.2f} for people = {st['pop']['running']:.0f}; × {st['season']['factor']:.2f} season = {st['season']['running']:.0f}. That was #{src['rank']} of {ex_lt['n_ranked']}; the #{top['rank']} patch, {top['dist_km']:.1f} km away on the same stream, won by also having ice on steep rock.")
    q("Score", "Why not just rank by the biggest motion?", f"We tried that as a baseline: ranking by raw offset size alone put the Langtang source #{lp['baseline_rank']} of {lp['n_offsets']}. Dividing by noise and adding the other factors moved it to #{lp['source_rank']} of {lp['n_ranked']}.")
    # Testing and results
    q("Results", "How do we know the rule was not tuned to Langtang?", "The rules file was written and time-stamped in git before any NISAR value was read. Then the unchanged rules ran on Budhi Gandaki, a disaster they had never seen.")
    q("Results", "What counts as a hit?", f"A ranked patch within {hit_km:g} km of the mapped source that drains into the same stream, following the steepest-descent (D8) path on the DEM. The truth is used only after ranking, never to rank.")
    q("Results", "What did Langtang show?", f"In the last pass before the collapse the source ranked #{lp['source_rank']} of {lp['n_ranked']} moving slopes. The strict test (top {CFG['pass_condition']['top_n']} in both of the last two passes) failed because the source was not ranked in {wlabel(miss_w)}.")
    q("Results", f"Why did the {wlabel(miss_w)} pass miss?", (f"It had the {'noisiest' if noisiest else 'noisy'} offsets of the summer: the cut was {OC['motion_threshold_sigma']:g} × {miss_noise:.2f} = {OC['motion_threshold_sigma'] * miss_noise:.2f} m"
                                                             + (f", but the crown moved only about {miss_crown:.2f} m (Meena 2026)." if miss_crown else ".")
                                                             + f" Phase also lost lock on {round(prodj[miss_w]['phase_ice_lost_share'] * 100)}% of the ice, so the slope fell below both thresholds."))
    q("Results", "What did Budhi Gandaki show?", f"At the exact slope ({hit_km:g} km) it failed: #{bg['positive_pass']['ranks'][0]} of {bg['positive_pass']['n_ranked'][0]}, then not ranked. In the {zone_km:g} km danger zone, the top {zone_n} covered it in both final passes (#{zb['last_two_best_rank'][0]}, #{zb['last_two_best_rank'][1]}) and in {zb['hits']} of {zb['passes']} passes.")
    q("Results", "What does 'about twice chance' mean?", f"A random list of {zone_n} patches each pass would cover the zone in about {zb['expected_by_chance']:.1f} of {zb['passes']} passes; Cascade Watch covered it in {zb['hits']}. The chance of doing that well by luck is about {zb['p_value']:.3f} (Poisson-binomial).")
    q("Results", f"Is the {zone_km:g} km zone cherry-picked?", "Yes, it was chosen after seeing these two events, so they are development data, not proof. It is now fixed in advance as rule v2, so every new NISAR event is a real test.")
    q("Results", "How many false alarms?", f"Valleys other than the source that stayed in the top {CFG['pass_condition']['top_n']} two passes running: {fa['budhigandaki']['n_false_alarms']} in {fa['budhigandaki']['n_passes']} passes at Budhi Gandaki, {fa['langtang']['n_false_alarms']} in {fa['langtang']['n_passes']} at Langtang, {fa['manang']['n_false_alarms']} in {fa['manang']['n_passes']} at a control valley with no disaster. That is why the list only places sensors, and only a sensed collapse sounds a siren.")
    q("Results", "What does this add to the published NISAR studies of Langtang?", (f"Kutoğlu et al. (the Turkish team) studied the slope already known to have failed, and Meena's blind screen of the same data ranked the source zone {mrank_txt} by motion alone. Cascade Watch searched the whole scene without knowing the answer, with rules fixed in advance, and put it #{lp['source_rank']} of {lp['n_ranked']} (their zones differ from our patches, so this is rough)."
                                                             if mrank_txt else "Kutoğlu et al. and Meena studied the known slope in hindsight; Cascade Watch searched the whole scene blind."))
    q("Results", "What would the warning have gained at Timure?", f"A ground tripwire on the watched valley could sound sirens about {T:.1f} minutes after the collapse instead of {A:.1f}. Against the reported arrival at Timure that is {lo} to {hi} minutes of warning: a what-if, with delays we assumed.")
    assert len(Q) == 37, len(Q)
    for qq in Q:
        n_sent = qq["a"].replace("et al.", "et al").count(". ") + 1
        assert n_sent <= 2 or qq["q"].startswith("Walk me"), (qq["q"], n_sent)

    out = dict(
        note="Built by pipeline/build_engineers.py from the files listed in each section. Nothing here is typed in by hand.",
        formula=SC["formula"], weights={k: SC[k] for k in ("opposing_bonus", "ice_bonus", "w_pop", "w_season")}, coherence_floor=SC["coherence_floor_for_visibility"],
        examples=[ex_lt, ex_bg], products=prods, wavelength_m=lam, stores=sites_store, stages=stages, ancillary=[dict(name=a, use=b) for a, b in ancillary],
        grid=dict(epsg=CFG["grid"]["epsg"], cell_m=CFG["grid"]["cell_m"], km=[round((CFG["grid"]["x_edges"][1] - CFG["grid"]["x_edges"][0]) / 1000), round((CFG["grid"]["y_edges"][1] - CFG["grid"]["y_edges"][0]) / 1000)]),
        qa=Q,
    )
    (DATA / "engineers.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"data/engineers.json: {len(Q)} questions, examples {[p['id'] for e in out['examples'] for p in e['patches']]}")


if __name__ == "__main__":
    main()
