"""Stage 3 — Moving blobs per window: extract, describe, score, rank, score against truth.

Truth (UNOSAT outline, else the USGS source point; for a backtest site the point
in pipeline/backtest.json) is used ONLY after ranking, to measure where the true
source landed. A negative control has no truth. Writes, in the site's data dir
(data/ for Langtang, data/sites/<site>/ otherwise, see grid.py / CW_SITE):
  windows.json, blobs_<window>.geojson, truth.geojson, source_series.json, scorecard.json
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import rasterio.features
from scipy import ndimage
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform as stransform
from shapely.ops import unary_union

import hydro
from clean import PROD, glacier_mask
from grid import (CELL, CFG, DATA, DEFAULT_SITE, EVENT_UTC, NX, NY, RAW, ROOT, SITE, SITE_CFG, TO_LL, TO_UTM, TRANSFORM, TRUTH,
                  XC, YC, dem, find, slope_fall_line)

BC, SC, TC = CFG["blobs"], CFG["score"], TRUTH  # TC is None for a negative control
RNG = np.random.default_rng(20260826)
EVENT = datetime.fromisoformat(EVENT_UTC.replace("Z", "+00:00")) if EVENT_UTC else None
SEASON_NOT_FETCHED = ("ERA5 not fetched for this backtest site: the season factor is the same for every patch in a pass "
                      "and cannot change ranks, so it is set to 1")


def to_ll(g):
    return stransform(lambda x, y, z=None: TO_LL.transform(x, y), g)


def to_utm(g):
    return stransform(lambda x, y, z=None: TO_UTM.transform(x, y), g)


# ---------------------------------------------------------------- truth
def truth():
    if TC is None:
        return None, {"kind": "none: negative control"}
    p = find(TC["outline_file"]) if TC["outline_file"] else None
    if p:
        gj = json.loads(Path(p).read_text())
        geom = unary_union([shape(f["geometry"]) for f in gj["features"]])
        return to_utm(geom), {"kind": "UNOSAT possible-detachment outline (product 4257)", "path": str(Path(p).relative_to(ROOT))}
    lon, lat = TC["lonlat"]
    kind = "USGS event location us7000tbwb (outline unavailable)" if SITE == DEFAULT_SITE else TC["kind"]
    return Point(*TO_UTM.transform(lon, lat)), {"kind": kind, "lonlat": [lon, lat]}


# ---------------------------------------------------------------- season
def season_anomaly(end: date) -> dict:
    """z-score of mean 2 m temperature, 1 May -> window end, 2026 vs 1991–2020 (ERA5 via Open-Meteo)."""
    if not SITE_CFG["era5"]:
        return {"value": 1.0, "z": None, "note": SEASON_NOT_FETCHED}
    p = find("openmeteo_raw_era5_default_elev.json")
    if p is None:
        return {"value": 1.0, "z": None, "note": "ERA5 unavailable"}
    d = json.loads(Path(p).read_text())
    daily = d.get("daily", d)
    t = np.array(daily["time"], dtype="datetime64[D]")
    v = np.array(daily["temperature_2m_mean"], dtype=float)
    vals = {}
    for y in range(1991, 2027):
        a, b = np.datetime64(f"{y}-05-01"), np.datetime64(f"{y}-{end.month:02d}-{end.day:02d}")
        m = (t >= a) & (t <= b)
        if m.any():
            vals[y] = float(np.nanmean(v[m]))
    clim = np.array([vals[y] for y in range(1991, 2021) if y in vals])
    z = (vals[2026] - clim.mean()) / clim.std(ddof=1)
    return {"value": float(np.clip(1 + 0.25 * z, 0.5, 1.5)), "z": float(z), "t2026_c": vals[2026], "clim_c": float(clim.mean())}


# ---------------------------------------------------------------- blobs
def polygons(lab: np.ndarray):
    out = {}
    for geom, val in rasterio.features.shapes(lab.astype(np.int32), mask=lab > 0, transform=TRANSFORM, connectivity=8):
        out.setdefault(int(val), []).append(shape(geom))
    return {k: unary_union(v) for k, v in out.items()}


def boot_sigma(vals: np.ndarray, noise: float) -> float:
    n = vals.size
    b = np.array([RNG.choice(vals, n).mean() for _ in range(BC["bootstrap_draws"])]).std()
    floor = noise / np.sqrt(max(n / 9.0, 1.0))  # neighbouring 80 m cells are correlated (~3x3)
    return float(max(b, floor))


def extract(name: str, ctx: dict) -> list[dict]:
    P = np.load(PROD / f"{name}.npz")
    info = json.loads((PROD / f"{name}.json").read_text())
    st = structure = np.ones((3, 3), bool)
    blobs = []
    rel = P["los_rel_mm"]
    R = P["dircoh"] if "dircoh" in P else np.full((NY, NX), np.nan)
    Rmed = float(np.nanmedian(R)) if np.isfinite(R).any() else np.nan
    masks = []
    if "s" in P:
        s = P["s"]
        noise = info["offsets_noise_m"]
        thr = CFG["offsets"]["motion_threshold_sigma"] * noise
        masks.append(("offsets", (s > thr) & (R > Rmed), noise))
    pnoise = info["phase_noise_mm"]
    masks.append(("phase+", rel > CFG["phase"]["blob_threshold_mm"], pnoise))
    masks.append(("phase-", rel < -CFG["phase"]["blob_threshold_mm"], pnoise))
    for chan, m, noise in masks:
        lab, n = ndimage.label(np.nan_to_num(m).astype(bool), structure=st)
        sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1))
        keep = {i + 1 for i, sz in enumerate(sizes) if sz >= BC["min_cells"]}
        lab = np.where(np.isin(lab, list(keep)), lab, 0)
        polys = polygons(lab)
        for k in sorted(keep):
            cells = lab == k
            rr, cc = np.nonzero(cells)
            x, y = XC[cc].mean(), YC[rr].mean()
            lon, lat = TO_LL.transform(x, y)
            if chan == "offsets":
                vals = P["s"][cells]
                vals = vals[np.isfinite(vals)]
                motion = float(vals.mean())
                coh = float(np.nanmean(P["corr"][cells]))
                raw_mag = float(np.nanmean(np.hypot(P["rg"][cells], P["az"][cells])))
            else:
                vals = rel[cells] / 1000.0
                vals = vals[np.isfinite(vals)]
                motion = float(np.abs(vals.mean()))
                coh = float(np.nanmean(P["coh"][cells]))
                raw_mag = None
            sigma = boot_sigma(vals, noise if chan == "offsets" else noise / 1000.0)
            rv = R[cells]
            dc = float(np.nanmean(rv)) if np.isfinite(rv).sum() >= 3 else Rmed
            lv = rel[cells]
            los_mean = float(np.nanmean(lv)) if np.isfinite(lv).mean() >= 0.3 else None
            gl = float(ctx["ice"][cells].mean())
            sl = float(np.nanmean(ctx["slope"][cells]))
            blobs.append(dict(
                id=f"{chan[0]}{'' if chan == 'offsets' else chan[-1]}{k}", channel="offsets" if chan == "offsets" else "phase",
                cells=int(cells.sum()), area_km2=round(float(cells.sum()) * CELL * CELL / 1e6, 4),
                motion_m=motion, sigma_m=sigma, dir_coherence=dc, coherence=coh,
                los_mean_mm=los_mean, raw_offset_m=raw_mag,
                elev_m=float(np.nanmean(ctx["z"][cells])), slope_deg=sl, glacier_frac=gl,
                ice_over_rock=bool(gl >= 0.3 and sl >= BC["ice_over_rock_min_slope_deg"]),
                lon=float(lon), lat=float(lat), _x=float(x), _y=float(y), _geom=polys[k],
            ))
    # opposing sign: a phase blob of the opposite LOS sign within 1.5 km
    ph = [b for b in blobs if b["channel"] == "phase"]
    for b in blobs:
        b["opposing_sign"] = False
        if b["los_mean_mm"] is None or abs(b["los_mean_mm"]) < 10:
            continue
        for o in ph:
            if o is b or o["los_mean_mm"] is None:
                continue
            if np.sign(o["los_mean_mm"]) != np.sign(b["los_mean_mm"]) and np.hypot(o["_x"] - b["_x"], o["_y"] - b["_y"]) <= BC["opposing_sign_radius_km"] * 1000:
                if not o["_geom"].intersects(b["_geom"]):
                    b["opposing_sign"] = True
                    break
    return blobs


def drainage(b: dict, ctx: dict):
    rows, cols, xs, ys, along = hydro.path_from(b["_x"], b["_y"])
    cells = rows * hydro.HNX + cols
    S = ctx["src_index"]
    join = None
    for c in cells:
        if c in S:
            join = S[c]
            break
    b["same_channel"] = bool(join is not None and join <= ctx["trib_len"])
    nm, _ = hydro.first_named_river(xs[:400], ys[:400], along[:400])
    b["channel_name"] = nm
    b["people_below"] = hydro.people_along(rows, cols, along, BC["people_path_km"], BC["people_buffer_km"])


def score(b: dict, season: float, w=SC) -> float:
    if b["coherence"] < w["coherence_floor_for_visibility"]:
        return 0.0
    s = (b["motion_m"] / b["sigma_m"]) * b["dir_coherence"]
    if b["opposing_sign"]:
        s *= w["opposing_bonus"]
    if b["ice_over_rock"]:
        s *= w["ice_bonus"]
    s *= 1 + w["w_pop"] * np.log10(1 + (b["people_below"] or 0))
    s *= 1 + w["w_season"] * (season - 1)
    return float(s)


def window_dates(name: str):
    _, a, b = name.split("_")
    return date(int(a[:4]), int(a[4:6]), int(a[6:])), date(int(b[:4]), int(b[4:6]), int(b[6:]))


def run():
    z = dem()
    slope, _ = slope_fall_line(z)
    ice = glacier_mask()
    tgeom, tmeta = truth()
    src_index, trib_len, src_river, main = {}, 0.0, None, None
    if tgeom is not None:
        # source path and the tributary length before it meets the main stem
        sx, sy = TO_UTM.transform(*TC["lonlat"])
        rows, cols, xs, ys, along = hydro.path_from(sx, sy)
        src_cells = rows * hydro.HNX + cols
        src_index = {int(c): float(a) for c, a in zip(src_cells, along)}
        src_river, _ = hydro.first_named_river(xs, ys, along)
        # Langtang: the source drains down an (unnamed in OSM) tributary, the Lhende Khola, into the Bhote Koshi.
        # "Same channel" = a slope whose flow path joins the source path before that main stem. Backtest sites
        # name no river: the main stem starts where the source path first has 500 km2 upstream area.
        names = SITE_CFG["main_stem_names"]
        if names:
            main, trib_len = hydro.first_named_river(xs, ys, along, within_m=200.0, names=names)
        if trib_len is None or not names:
            trib_len = hydro.main_stem_along(rows, cols, along)
            if not names:
                main, _ = hydro.first_named_river(xs[along >= trib_len], ys[along >= trib_len], along[along >= trib_len])
    ctx = dict(z=z, slope=slope, ice=ice, src_index=src_index, trib_len=trib_len)
    manifest = json.loads((RAW / "nisar" / "manifest.json").read_text())

    windows, series, card = [], [], {"windows": {}}
    yy, xx = np.meshgrid(YC, XC, indexing="ij")
    zone_mask = None
    if tgeom is not None:
        if tgeom.geom_type == "Point":
            zone_mask = np.hypot(xx - sx, yy - sy) <= 1000
        else:
            zone_mask = rasterio.features.rasterize([(tgeom, 1)], out_shape=(NY, NX), transform=TRANSFORM, all_touched=True).astype(bool)
        tll = to_ll(tgeom)
        c = tll.centroid
        zone = to_ll(tgeom.buffer(TC["hit_radius_km"] * 1000, 64))
        (DATA / "truth.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {**tmeta, "role": "truth", "center": [round(c.x, 5), round(c.y, 5)]}, "geometry": mapping(tll)},
            {"type": "Feature", "properties": {"role": "hit_zone", "radius_km": TC["hit_radius_km"]}, "geometry": mapping(zone)}]}))
    names = sorted(p.stem for p in PROD.glob("*.npz"))
    for name in names:
        info = json.loads((PROD / f"{name}.json").read_text())
        d0, d1 = window_dates(name)
        days = (d1 - d0).days
        season = season_anomaly(d1)
        blobs = extract(name, ctx)
        for b in blobs:
            drainage(b, ctx)
            b["dist_truth_km"] = float(b["_geom"].distance(tgeom) / 1000) if tgeom is not None else None
            b["season_anomaly"] = season["value"]
            b["score"] = score(b, season["value"])
        ranked = sorted([b for b in blobs if b["score"] > 0], key=lambda b: -b["score"])
        for i, b in enumerate(ranked):
            b["rank"] = i + 1

        def is_hit(b):
            return tgeom is not None and b["dist_truth_km"] <= TC["hit_radius_km"] and b["same_channel"]

        hit = next((b for b in ranked if is_hit(b)), None)
        offs = sorted([b for b in blobs if b["channel"] == "offsets" and b["raw_offset_m"] is not None], key=lambda b: -b["raw_offset_m"])
        base_hit = next((i + 1 for i, b in enumerate(offs) if is_hit(b)), None)
        near = [b for b in blobs if is_hit(b)]
        gun = manifest.get(f"GUNW_{name}", {})
        gof = manifest.get(f"GOFF_{name}", {})
        avail = gof.get("asf_processingDate") or gun.get("asf_processingDate")
        w = dict(id=name, direction=info["direction"], start=str(d0), end=str(d1), stack_days=days,
                 phase_ice_lost_share=round(info["phase_ice_lost_share"], 3),
                 ice_lost_share=round(info.get("offsets_ice_lost_share", info["phase_ice_lost_share"]), 3),
                 offsets_layer=info.get("offsets_layer"), offsets_noise_m=info.get("offsets_noise_m"),
                 phase_noise_mm=info["phase_noise_mm"], season=season, n_blobs=len(blobs), n_ranked=len(ranked),
                 gunw=gun.get("scene"), goff=gof.get("scene"), crid=gof.get("crid") or gun.get("crid"), asf_processed=avail,
                 phase=("control" if EVENT is None else
                        "pre" if d1 <= EVENT.date() else ("co" if d0 <= EVENT.date() < d1 else "post")))
        windows.append(w)
        card["windows"][name] = dict(source_rank=hit["rank"] if hit else None, blobs_above=(hit["rank"] - 1) if hit else None,
                                     n_ranked=len(ranked), baseline_rank=base_hit, n_offsets=len(offs),
                                     source_visible=bool(near) and any(b["score"] > 0 for b in near),
                                     hit_id=hit["id"] if hit else None,
                                     top5=[dict(id=b["id"], channel=b["channel"],
                                                dist_truth_km=round(b["dist_truth_km"], 2) if tgeom is not None else None,
                                                lon=round(b["lon"], 4), lat=round(b["lat"], 4)) for b in ranked[:5]])
        feats = []
        for b in blobs:
            g = to_ll(b["_geom"].simplify(30, preserve_topology=True))
            props = {k: (float(f"{v:.6g}") if isinstance(v, float) else v) for k, v in b.items() if not k.startswith("_")}
            feats.append({"type": "Feature", "properties": props, "geometry": mapping(g)})
        (DATA / f"blobs_{name}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
        print(name, "ranked", len(ranked), "source rank", card["windows"][name]["source_rank"], "baseline", base_hit, flush=True)
        if zone_mask is None:
            continue
        # the source site itself, independent of blob detection
        P = np.load(PROD / f"{name}.npz")
        rr = zone_mask  # cells inside the UNOSAT detachment zone (or 1 km around the point)
        srow = dict(id=name, start=str(d0), end=str(d1), days=days, direction=info["direction"], cells=int(rr.sum()),
                    offsets_noise_m=info.get("offsets_noise_m"), phase_noise_mm=info["phase_noise_mm"])
        if "s" in P:
            v = P["s"][rr]
            ok = np.isfinite(v)
            srow.update(offsets_median_m=float(np.median(v[ok])) if ok.sum() >= 3 else None,
                        offsets_p90_m=float(np.percentile(v[ok], 90)) if ok.sum() >= 3 else None,
                        offsets_valid_share=float(ok.mean()),
                        offsets_sem_m=float(info["offsets_noise_m"] / np.sqrt(max(ok.sum() / 9.0, 1))) if ok.sum() >= 3 else None)
        c = P["coh"][rr]
        lv = P["los_rel_mm"][rr]
        srow.update(phase_coh_median=float(np.nanmedian(c)), phase_valid_share=float(np.isfinite(lv).mean()),
                    phase_median_mm=float(np.nanmedian(lv)) if np.isfinite(lv).sum() >= 3 else None)
        series.append(srow)

    last2 = SITE_CFG["last_two_ascending"]
    if last2:
        ranks = [card["windows"].get(w, {}).get("source_rank") for w in last2]
        card["pass"] = dict(rule=CFG["pass_condition"]["rule"], windows=last2, ranks=ranks,
                            passed=all(r is not None and r <= CFG["pass_condition"]["top_n"] for r in ranks))
    else:
        card["pass"] = None  # negative control: no event, no pass test
    card["config_sha256"] = hashlib.sha256((ROOT / "pipeline" / "config.json").read_bytes()).hexdigest()
    card["truth"] = tmeta
    card["source_path_first_river"] = src_river
    card["tributary_length_km"] = round(trib_len / 1000, 2)
    if SITE != DEFAULT_SITE:
        card.update(site=SITE, role=SITE_CFG["role"], event_utc=EVENT_UTC, main_stem_first_river=main,
                    backtest_sha256=hashlib.sha256((ROOT / "pipeline" / "backtest.json").read_bytes()).hexdigest(),
                    season_note=SEASON_NOT_FETCHED)
    (DATA / "windows.json").write_text(json.dumps(windows, indent=1))
    (DATA / "source_series.json").write_text(json.dumps(series, indent=1))
    (DATA / "scorecard.json").write_text(json.dumps(card, indent=1))
    print(json.dumps(card["pass"]))


if __name__ == "__main__":
    run()
