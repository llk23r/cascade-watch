"""Pre-registered backtest: the metrics of pipeline/backtest.json over the outputs of rank.py.

Run after `CW_SITE=<site> python clean.py && CW_SITE=<site> python rank.py` for every site.
Reads, per site, the site data dir (data/ for Langtang, data/sites/<site>/ otherwise):
scorecard.json, windows.json, blobs_<window>.geojson; and the site raw dir: nisar/manifest.json
(granules), products/*.json and *.failed.json (what could be processed). Writes data/backtest.json.

Nothing is ranked or tuned here. The metrics are the ones written in pipeline/backtest.json
before any NISAR value in the backtest boxes was read:
  positive_pass     a patch within the hit radius of the truth that drains to the same channel ranks
                    top_n in BOTH of the last two ascending windows before the event (rank.py's hit)
  rank_series       per ascending window from the first fetched pair to the event, the rank of the best hit
  false_alarm       a valley other than the truth valley in the top top_n in two consecutive ascending windows
  false_alarm_rate  false alarms per box per pass over the summer windows

How the false-alarm definition is applied (written down here, applied identically to every site):
  * Patches: the top_n ranked patches of every counted window (rank <= top_n in blobs_<window>.geojson).
  * Valley: single-linkage clusters of those patches across the counted windows. Two patches link when their
    outlines are within hit_radius_km of each other AND they drain to the same channel, i.e. their D8 flow
    paths first reach the same named OSM river (rank.py's channel_name; two patches with no named river
    downstream count as the same channel).
  * Truth valley (positive sites): every cluster that contains a hit patch (within hit_radius_km of the truth
    and same_channel, exactly rank.py's hit test). It is never a false alarm.
  * False alarm: a non-truth valley with top_n patches in two consecutive counted windows (adjacent in the
    pair list; a window that could not be processed breaks the run). Each valley counts once; its windows
    are listed.
  * Rate: false alarms / number of counted passes (windows). Also given per 12 days of the period covered.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape

from grid import BACKTEST, CFG, DEFAULT_SITE, ROOT, site_settings

TOP_N = CFG["pass_condition"]["top_n"]
SITES = ["budhigandaki", "manang", "langtang"]
NOTES = {
    "langtang": (
        "Uses the committed data/ outputs unchanged. They reproduce byte-for-byte from the current code only when "
        "clean.py runs on an earlier Copernicus DEM mosaic that sat half a pixel (about 15 m) off the tile grid and "
        "rank.py on the current, corrected DEM: data/ was not regenerated after the DEM fix. Re-running clean.py and "
        "rank.py on the current DEM keeps the pre-event pass ranks (not ranked, #2) and the 4 false alarms, but changes "
        "n_ranked in most windows and the hit rank in A098 0819-0831 (21 to 10), A098 0924-1006 (8 to 7) and "
        "D048 0723-0816 (13 to 14)."),
    "budhigandaki": (
        "The rank series starts at the first fetched pair, A098 2026-05-15, the same start as Langtang; the A098 "
        "stack also holds winter pairs back to Nov 2025 that were not fetched. The truth is an approximate scar "
        "point (no mapped outline), so the hit zone is a 2 km circle around a point."),
}


def git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return ""


def wdates(w: str) -> tuple[date, date]:
    _, a, b = w.split("_")
    return date(int(a[:4]), int(a[4:6]), int(a[6:])), date(int(b[:4]), int(b[4:6]), int(b[6:]))


def control_span(spec: str) -> tuple[date, date]:
    """'all A098 ascending pairs from 2026-06-20 to 2026-10-06' -> the two dates."""
    d = re.findall(r"(\d{4})-(\d{2})-(\d{2})", spec)
    if len(d) != 2:
        raise SystemExit(f"cannot read the control window span from {spec!r}")
    return tuple(date(*map(int, x)) for x in d)


# ---------------------------------------------------------------- per-site inputs
def load_site(site: str, data_dir: Path | None = None) -> dict:
    s = site_settings(site)
    data = data_dir or s["data"]
    raw = s["raw"]
    card = json.loads((data / "scorecard.json").read_text())
    wins = {w["id"]: w for w in json.loads((data / "windows.json").read_text())}
    manifest = json.loads((raw / "nisar" / "manifest.json").read_text())
    prod = raw / "products"
    pairs = sorted({k.split("_", 1)[1] for k, v in manifest.items() if v["level"] in ("GUNW", "GOFF")})
    status = {}
    for w in pairs:
        have_gunw = (raw / "nisar" / f"GUNW_{w}.npz").exists()
        have_goff = (raw / "nisar" / f"GOFF_{w}.npz").exists()
        failed = prod / f"{w}.failed.json"
        info = json.loads((prod / f"{w}.json").read_text()) if (prod / f"{w}.json").exists() else None
        if not have_gunw:
            st = "not processed: GUNW subset missing"
        elif not have_goff:
            st = "not processed: GOFF subset missing"
        elif failed.exists():
            st = "not processed: " + json.loads(failed.read_text())["error"]
        elif w not in card["windows"]:
            st = "not processed: not ranked (run clean.py and rank.py)"
        elif info is not None and not info.get("offsets_layer"):
            st = "not processed: no usable offsets"
        else:
            st = "ok"
        status[w] = st
    return dict(site=site, s=s, data=data, raw=raw, card=card, wins=wins, manifest=manifest, pairs=pairs, status=status)


def blobs(S: dict, w: str) -> list[dict]:
    fc = json.loads((S["data"] / f"blobs_{w}.geojson").read_text())
    to_utm = Transformer.from_crs(4326, int(S["s"]["grid"]["epsg"]), always_xy=True)
    out = []
    for f in fc["features"]:
        p = f["properties"]
        if p.get("rank") is None:
            continue
        p = dict(p)
        p["_geom"] = shapely.transform(shape(f["geometry"]), lambda c: np.column_stack(to_utm.transform(c[:, 0], c[:, 1])))
        out.append(p)
    return sorted(out, key=lambda p: p["rank"])


def is_hit(S: dict, p: dict) -> bool:
    t = S["s"]["truth"]
    return bool(t) and p.get("dist_truth_km") is not None and p["dist_truth_km"] <= t["hit_radius_km"] and bool(p.get("same_channel"))


def top_entry(S: dict, p: dict) -> dict:
    e = dict(rank=p["rank"], id=p["id"], channel_kind=p["channel"], lon=round(p["lon"], 5), lat=round(p["lat"], 5),
             score=round(p["score"], 3), channel_name=p.get("channel_name"))
    if S["s"]["truth"]:
        e.update(dist_truth_km=round(p["dist_truth_km"], 2), hit=is_hit(S, p))
    return e


# ---------------------------------------------------------------- metrics
def rank_of_hit(S: dict, w: str) -> dict:
    st = S["status"].get(w, "not processed: not fetched")
    if st != "ok":
        return dict(window=w, status=st, rank=None, n_ranked=None)
    c = S["card"]["windows"][w]
    hits = [p for p in blobs(S, w) if is_hit(S, p)]
    best = hits[0]["rank"] if hits else None
    if best != c["source_rank"]:  # backtest reads the same files rank.py wrote: these must agree
        raise SystemExit(f"{S['site']} {w}: best hit rank {best} in blobs but {c['source_rank']} in scorecard")
    return dict(window=w, status=st, rank=c["source_rank"], n_ranked=c["n_ranked"], hit_id=c["hit_id"],
                top_n=bool(c["source_rank"] is not None and c["source_rank"] <= TOP_N))


def false_alarms(S: dict, windows: list[str]) -> dict:
    radius_km = (S["s"]["truth"] or CFG["truth"])["hit_radius_km"]
    counted = [w for w in windows if S["status"].get(w) == "ok"]
    pats = []
    for w in counted:
        for p in blobs(S, w)[:TOP_N]:
            pats.append(dict(window=w, p=p, hit=is_hit(S, p)))
    parent = list(range(len(pats)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(pats)):
        for j in range(i + 1, len(pats)):
            a, b = pats[i]["p"], pats[j]["p"]
            if a.get("channel_name") == b.get("channel_name") and a["_geom"].distance(b["_geom"]) <= radius_km * 1000:
                parent[root(i)] = root(j)
    clusters: dict[int, list] = {}
    for i, q in enumerate(pats):
        clusters.setdefault(root(i), []).append(q)
    pos = {w: i for i, w in enumerate(windows)}
    valleys, truth_valley = [], []
    for members in clusters.values():
        ws = sorted({q["window"] for q in members}, key=pos.get)
        runs = [[a, b] for a, b in zip(ws, ws[1:]) if pos[b] == pos[a] + 1]
        lon = sum(q["p"]["lon"] for q in members) / len(members)
        lat = sum(q["p"]["lat"] for q in members) / len(members)
        v = dict(centroid_lonlat=[round(lon, 4), round(lat, 4)],
                 channel_name=members[0]["p"].get("channel_name"),
                 windows=ws, consecutive_pairs=runs,
                 best_rank_per_window={w: min(q["p"]["rank"] for q in members if q["window"] == w) for w in ws},
                 patches={w: [q["p"]["id"] for q in members if q["window"] == w] for w in ws})
        if any(q["hit"] for q in members):
            truth_valley.append(v)
        elif runs:
            valleys.append(v)
    valleys.sort(key=lambda v: (pos[v["consecutive_pairs"][0][0]], v["centroid_lonlat"]))
    d0, d1 = wdates(windows[0])[0], wdates(windows[-1])[1]
    days = (d1 - d0).days
    n = len(counted)
    return dict(
        windows=windows, windows_counted=counted,
        windows_not_counted={w: S["status"].get(w, "not fetched") for w in windows if w not in counted},
        n_passes=n, period=[str(d0), str(d1)], period_days=days,
        n_false_alarms=len(valleys),
        n_consecutive_pairs=sum(len(v["consecutive_pairs"]) for v in valleys),
        false_alarm_rate_per_pass=round(len(valleys) / n, 3) if n else None,
        false_alarm_rate_per_12_days=round(len(valleys) / (days / 12), 3) if days else None,
        false_alarms=valleys,
        truth_valley_clusters=sorted(truth_valley, key=lambda v: pos[v["windows"][0]]),
        top_patches_without_channel_name=sum(1 for q in pats if q["p"].get("channel_name") is None),
        top_patches=len(pats), link_radius_km=radius_km,
    )


def provenance(S: dict, windows: list[str]) -> dict:
    out = {}
    for w in windows:
        e = {}
        for lv in ("GUNW", "GOFF"):
            m = S["manifest"].get(f"{lv}_{w}")
            if m:
                meta_p = S["raw"] / "nisar" / f"{lv}_{w}.json"
                meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
                e[lv] = dict(scene=m["scene"], crid=m["crid"], asf_processing_date=m.get("asf_processingDate"),
                             product_processing_time=meta.get("processingDateTime"),
                             box_coverage=meta.get("box_coverage", 1.0 if meta else None),
                             read_together_with=[x for x in meta.get("boxes_read_from_this_open", []) if x != S["site"]] or None)
        out[w] = e
    return out


def window_table(S: dict, windows: list[str]) -> list[dict]:
    rows = []
    for w in windows:
        r = dict(window=w, status=S["status"].get(w, "not fetched"))
        if w in S["wins"]:
            x = S["wins"][w]
            r.update(n_ranked=x["n_ranked"], offsets_layer=x.get("offsets_layer"),
                     offsets_noise_m=round(x["offsets_noise_m"], 4) if x.get("offsets_noise_m") is not None else None,
                     phase_noise_mm=round(x["phase_noise_mm"], 2), asf_processed=x.get("asf_processed"),
                     season_factor=x["season"]["value"])
        if r["status"] == "ok":
            r["top5"] = [top_entry(S, p) for p in blobs(S, w)[:TOP_N]]
        rows.append(r)
    return rows


def site_result(site: str, data_dir: Path | None = None) -> dict:
    S = load_site(site, data_dir)
    s, card = S["s"], S["card"]
    asc = [w for w in S["pairs"] if w.startswith("A")]
    res = dict(role=s["role"], box_epsg=s["grid"]["epsg"], box_x_edges=s["grid"]["x_edges"], box_y_edges=s["grid"]["y_edges"],
               truth=card.get("truth"), event_utc=s["event_utc"])
    if s["truth"]:
        ev = datetime.fromisoformat(s["event_utc"].replace("Z", "+00:00")).date()
        pre_asc = [w for w in asc if wdates(w)[1] <= ev]  # same pre/co/post rule as rank.py
        res.update(hit_definition=f"a ranked patch within {s['truth']['hit_radius_km']} km of the truth whose D8 path joins the "
                                  "truth's D8 path before the main stem (rank.py same_channel)",
                   source_path_first_river=card.get("source_path_first_river"),
                   main_stem_first_river=card.get("main_stem_first_river"),
                   tributary_length_km=card.get("tributary_length_km"))
        res["rank_series"] = [rank_of_hit(S, w) for w in pre_asc]
        last2 = s["last_two_ascending"]
        r2 = [rank_of_hit(S, w) for w in last2]
        evaluable = all(x["status"] == "ok" for x in r2)
        res["positive_pass"] = dict(rule=BACKTEST["metrics"]["positive_pass"], windows=last2, ranks=[x["rank"] for x in r2],
                                    n_ranked=[x["n_ranked"] for x in r2],
                                    result=("pass" if evaluable and all(x["top_n"] for x in r2) else "fail" if evaluable else "not evaluable"))
        other = [w for w in S["pairs"] if w not in pre_asc]
        res["other_windows"] = [rank_of_hit(S, w) | {"note": "spans the event" if wdates(w)[0] <= ev < wdates(w)[1] else
                                                     ("before the event" if wdates(w)[1] <= ev else "after the event")}
                                for w in other]
        fa_windows = pre_asc
        res["windows_used"] = dict(rank_series=pre_asc, false_alarms=pre_asc, other=other)
    else:
        a, b = control_span(BACKTEST["sites"][site]["windows"])
        fa_windows = [w for w in asc if wdates(w)[0] >= a and wdates(w)[1] <= b]
        res["windows_used"] = dict(false_alarms=fa_windows, other=[w for w in S["pairs"] if w not in fa_windows])
        res["positive_pass"] = None
    res["false_alarm"] = false_alarms(S, fa_windows)
    res["windows"] = window_table(S, S["pairs"])
    if site in NOTES:
        res["note"] = NOTES[site]
    res["season"] = (card.get("season_note") or "ERA5 season factor applied (identical for every patch in a pass)")
    res["provenance"] = dict(granules=provenance(S, S["pairs"]), config_sha256=card.get("config_sha256"),
                             backtest_sha256=card.get("backtest_sha256"))
    return res


def main(argv: list[str]) -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--langtang-data", type=Path, help="read the Langtang outputs from this dir and print, do not write")
    a = ap.parse_args(argv)
    if a.langtang_data:
        r = site_result(DEFAULT_SITE, a.langtang_data)
        print(json.dumps({k: r[k] for k in ("positive_pass", "rank_series")}, indent=1))
        print(json.dumps({k: v for k, v in r["false_alarm"].items() if k != "truth_valley_clusters"}, indent=1))
        return
    dirty = git("status", "--porcelain", "--", "pipeline")
    out = dict(
        generated_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        plan="pipeline/backtest.json",
        plan_commit=git("log", "-1", "--format=%h %s", "--", "pipeline/backtest.json"),
        rules="pipeline/config.json",
        rules_sha256=hashlib.sha256((ROOT / "pipeline" / "config.json").read_bytes()).hexdigest(),
        code_commit=git("rev-parse", "HEAD") + (" (pipeline/ has uncommitted changes)" if dirty else ""),
        top_n=TOP_N,
        method=__doc__.split("How the false-alarm definition is applied", 1)[1].strip(" (written down here, applied identically to every site):\n"),
        season_note=("Backtest sites: ERA5 was not fetched and the season factor is set to 1. It multiplies every patch in "
                     "a pass by the same number, so it cannot change a rank. Langtang keeps its committed ERA5 factor."),
        sites={},
    )
    for site in SITES:
        out["sites"][site] = site_result(site)
        r = out["sites"][site]
        fa = r["false_alarm"]
        line = f"{site:13s} FA {fa['n_false_alarms']} over {fa['n_passes']} passes ({fa['false_alarm_rate_per_pass']}/pass)"
        if r.get("positive_pass"):
            line += f"  pass test: {r['positive_pass']['result']} ranks {r['positive_pass']['ranks']}"
        print(line, flush=True)
    (ROOT / "data" / "backtest.json").write_text(json.dumps(out, indent=1))
    print("wrote data/backtest.json")


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
