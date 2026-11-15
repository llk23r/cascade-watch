#!/usr/bin/env python
"""Sentinel-1 C-band InSAR control dataset for Cascade Watch (Langtang, 26 Aug 2026).

Reproducible steps (each is idempotent; run them in order or use `all`):

    python -I pipeline/fetch_s1.py plan      # asf_search, pick track, build pairs -> raw/s1/plan.json
    python -I pipeline/fetch_s1.py submit    # check credits, submit-or-reuse HyP3 INSAR_GAMMA jobs by name
    python -I pipeline/fetch_s1.py wait --max-minutes 9   # poll (short, repeatable)
    python -I pipeline/fetch_s1.py process   # download finished products, clip to study grid, delete zips
    python -I pipeline/fetch_s1.py manifest  # (re)write raw/s1/manifest.json with coherence stats
    python -I pipeline/fetch_s1.py all       # plan + submit + one wait + process + manifest

Real data only: nothing is filled or invented. Failures are recorded in the manifest.
Earthdata credentials come from ~/.netrc via hyp3_sdk / asf_search; they are never read here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import time
import zipfile
from itertools import combinations
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "raw" / "s1"
TMP = OUT / "_tmp"
PLAN = OUT / "plan.json"
MANIFEST = OUT / "manifest.json"

JOB_NAME = "cascadewatch-s1"
SOURCE_LON, SOURCE_LAT = 85.515, 28.271
SEARCH_START, SEARCH_END = "2026-06-10", "2026-09-05T23:59:59"
EVENT_DATE = dt.date(2026, 8, 26)

# Study-box grid (must match the NISAR pipeline exactly).
GRID_CRS = "EPSG:32645"
GRID_X0, GRID_Y0, GRID_RES = 336440.0, 3146600.0, 80.0
GRID_W, GRID_H = 463, 412
NEAR_SOURCE_M = 2000.0

HYP3_PARAMS = dict(
    looks="20x4",
    include_look_vectors=True,
    include_displacement_maps=True,  # replaces deprecated include_los_displacement; yields los_disp
    include_inc_map=True,
    include_dem=True,
    apply_water_mask=False,
    # phase_filter_parameter left at the HyP3 default (0.6)
)

# Layers kept from each product: suffix in HyP3 zip -> (output name, resampling)
LAYERS = {
    "unw_phase": "bilinear",
    "corr": "bilinear",
    "los_disp": "bilinear",
    "lv_theta": "bilinear",
    "lv_phi": "bilinear",
    "inc_map": "bilinear",
    "dem": "bilinear",
}

# NISAR windows the control must match.
NISAR_ASC = [  # ascending track 98, pre-event + co-event
    ("W1", "2026-06-20", "2026-07-02"),
    ("W2", "2026-07-02", "2026-07-14"),
    ("W3", "2026-07-14", "2026-07-26"),
    ("W4", "2026-07-26", "2026-08-19"),
    ("CO", "2026-08-19", "2026-08-31"),
]
NISAR_DESC = [  # descending, reference only
    ("D1", "2026-06-29", "2026-07-11"),
    ("D2", "2026-07-11", "2026-07-23"),
    ("D3", "2026-07-23", "2026-08-16"),
    ("D4", "2026-08-16", "2026-08-28"),
]


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def save(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    tmp.replace(path)


# --------------------------------------------------------------------------- plan
def search() -> list[dict]:
    import asf_search as asf

    res = asf.search(
        platform=[asf.PLATFORM.SENTINEL1],
        processingLevel="SLC",
        beamMode="IW",
        intersectsWith=f"POINT({SOURCE_LON} {SOURCE_LAT})",
        start=SEARCH_START,
        end=SEARCH_END,
    )
    scenes = []
    for r in res:
        p = r.properties
        scenes.append(
            dict(
                granule=p["sceneName"],
                date=p["startTime"][:10],
                start_time=p["startTime"],
                platform=p["platform"],
                flight_direction=p["flightDirection"],
                relative_orbit=int(p["pathNumber"]),
                frame=int(p["frameNumber"]),
                polarization=p.get("polarization"),
            )
        )
    return sorted(scenes, key=lambda s: (s["flight_direction"], s["relative_orbit"], s["start_time"]))


def cost(pair, win) -> int:
    """Date mismatch in days between an S1 pair and a target window."""
    return abs((d(pair[0]["date"]) - d(win[1])).days) + abs((d(pair[1]["date"]) - d(win[2])).days)


def track_pairs(acqs):
    one_per_date = {}
    for a in acqs:  # one acquisition per date (frames along track can repeat)
        one_per_date.setdefault(a["date"], a)
    return [p for p in combinations(sorted(one_per_date.values(), key=lambda a: a["date"]), 2)]


def best(pairs, win, max_bl=None, min_bl=None, same_platform=False):
    cand = []
    for p in pairs:
        bl = (d(p[1]["date"]) - d(p[0]["date"])).days
        if max_bl is not None and bl > max_bl:
            continue
        if min_bl is not None and bl < min_bl:
            continue
        if same_platform and p[0]["platform"] != p[1]["platform"]:
            continue
        cand.append(p)
    if not cand:
        return []
    c0 = min(cost(p, win) for p in cand)
    return [p for p in cand if cost(p, win) == c0]


def pair_record(p, window, role, orbit_tag):
    ref, sec = p
    bl = (d(sec["date"]) - d(ref["date"])).days
    name = f"S1_{ref['date'].replace('-', '')}_{sec['date'].replace('-', '')}_{orbit_tag}"
    desc_map = min(NISAR_DESC, key=lambda w: cost(p, w))
    return dict(
        pairname=name,
        reference=ref,
        secondary=sec,
        temporal_baseline_days=bl,
        cross_platform=ref["platform"] != sec["platform"],
        nisar_window=window[0],
        nisar_window_dates=[window[1], window[2]],
        role=role,
        date_offset_days={
            "reference": (d(ref["date"]) - d(window[1])).days,
            "secondary": (d(sec["date"]) - d(window[2])).days,
        },
        nisar_desc_nearest=dict(
            id=desc_map[0],
            dates=[desc_map[1], desc_map[2]],
            offset_days=[
                (d(ref["date"]) - d(desc_map[1])).days,
                (d(sec["date"]) - d(desc_map[2])).days,
            ],
        ),
        spans_event=d(ref["date"]) <= EVENT_DATE <= d(sec["date"]),
    )


def cmd_plan(args) -> dict:
    scenes = search()
    tracks = {}
    for s in scenes:
        tracks.setdefault((s["flight_direction"], s["relative_orbit"]), []).append(s)

    summary, scores = [], {}
    for (fd, ro), acqs in sorted(tracks.items()):
        dates = sorted({a["date"] for a in acqs})
        gaps = [(d(b) - d(a)).days for a, b in zip(dates, dates[1:])]
        pairs = track_pairs(acqs)
        per_win = {w[0]: min(cost(p, w) for p in best(pairs, w)) for w in NISAR_ASC}
        scores[(fd, ro)] = sum(per_win.values())
        summary.append(
            dict(
                flight_direction=fd,
                relative_orbit=ro,
                n_acquisitions=len(dates),
                platforms=sorted({a["platform"] for a in acqs}),
                frames=sorted({a["frame"] for a in acqs}),
                dates=[f"{a['date']} {a['platform']}" for a in sorted(acqs, key=lambda a: a["date"])],
                revisit_gaps_days=gaps,
                nisar_asc_window_mismatch_days=per_win,
                total_mismatch_days=scores[(fd, ro)],
            )
        )

    # Best match to the NISAR ascending windows; ascending wins ties.
    chosen = min(scores, key=lambda k: (scores[k], 0 if k[0] == "ASCENDING" else 1, k[1]))
    fd, ro = chosen
    orbit_tag = f"{'A' if fd == 'ASCENDING' else 'D'}{ro:03d}"
    pairs = track_pairs(tracks[chosen])

    planned = []
    for w in NISAR_ASC:
        span = (d(w[2]) - d(w[1])).days
        # short-baseline pair(s) (<=12 d) closest to the window; ties all kept (e.g. both halves of W4)
        for p in best(pairs, w, max_bl=12):
            planned.append(pair_record(p, w, "primary_short", orbit_tag))
        # if the closest short pair is not a same-platform 12/6-day pair, add the best one that is
        short = best(pairs, w, max_bl=12)
        if short and any(
            p[0]["platform"] != p[1]["platform"] or (d(p[1]["date"]) - d(p[0]["date"])).days not in (6, 12)
            for p in short
        ):
            for p in best(pairs, w, max_bl=12, min_bl=6, same_platform=True):
                bl = (d(p[1]["date"]) - d(p[0]["date"])).days
                if bl in (6, 12):
                    planned.append(pair_record(p, w, "alternate_same_platform_12d", orbit_tag))
        # long window: a pair whose baseline matches the window length (W4: 24 d)
        if span > 12:
            for p in best(pairs, w, min_bl=span - 2, max_bl=span + 2):
                planned.append(pair_record(p, w, "primary_long", orbit_tag))

    # de-duplicate by pair name, merging window roles
    uniq = {}
    for r in planned:
        if r["pairname"] in uniq:
            uniq[r["pairname"]]["also_maps_to"] = uniq[r["pairname"]].get("also_maps_to", []) + [
                [r["nisar_window"], r["role"]]
            ]
        else:
            uniq[r["pairname"]] = r
    co = [r for r in uniq.values() if r["spans_event"]]

    plan = dict(
        generated_utc=now(),
        search=dict(
            dataset="Sentinel-1 IW SLC",
            point=[SOURCE_LON, SOURCE_LAT],
            start=SEARCH_START,
            end=SEARCH_END,
            n_scenes=len(scenes),
            tracks=summary,
        ),
        chosen_track=dict(
            flight_direction=fd,
            relative_orbit=ro,
            orbit_tag=orbit_tag,
            rule="minimum total date mismatch to NISAR ascending windows W1-W4+CO; ascending wins ties",
        ),
        co_event_pairs=[r["pairname"] for r in co],
        pairs=list(uniq.values()),
    )
    save(PLAN, plan)
    print(f"{len(scenes)} scenes; chosen {fd} relorb {ro}; {len(uniq)} pairs -> {PLAN.relative_to(ROOT)}")
    for r in uniq.values():
        print(f"  {r['pairname']:32s} {r['temporal_baseline_days']:2d}d {r['nisar_window']:3s} {r['role']}"
              f"{' x-platform' if r['cross_platform'] else ''}")
    return plan


# --------------------------------------------------------------------------- hyp3
def hyp3():
    from hyp3_sdk import HyP3
    from hyp3_sdk.exceptions import AuthenticationError

    try:
        return HyP3()
    except AuthenticationError as e:
        print(f"HyP3 authentication failed: {e}", file=sys.stderr)
        sys.exit(2)


def job_matches(job, rec) -> bool:
    p = job.job_parameters or {}
    if job.job_type != "INSAR_GAMMA":
        return False
    if sorted(p.get("granules", [])) != sorted([rec["reference"]["granule"], rec["secondary"]["granule"]]):
        return False
    return all(p.get(k, v) == v for k, v in HYP3_PARAMS.items())


def existing_jobs(h):
    return list(h.find_jobs(name=JOB_NAME))


def cmd_submit(args) -> None:
    plan = load(PLAN)
    h = hyp3()
    credits_before = h.check_credits()
    print(f"credits before: {credits_before}")
    jobs = existing_jobs(h)
    submitted = []
    for rec in plan["pairs"]:
        live = [j for j in jobs if job_matches(j, rec) and not j.failed()]
        if live:
            rec["hyp3_job_id"] = live[0].job_id
            rec["hyp3_reused"] = True
            print(f"  reuse  {rec['pairname']} {live[0].job_id} {live[0].status_code}")
            continue
        failed = [j for j in jobs if job_matches(j, rec) and j.failed()]
        if failed and not args.resubmit_failed:
            rec["hyp3_job_id"] = failed[0].job_id
            rec["hyp3_reused"] = True
            print(f"  FAILED {rec['pairname']} {failed[0].job_id} (use --resubmit-failed)")
            continue
        job = h.submit_insar_job(
            rec["reference"]["granule"], rec["secondary"]["granule"], name=JOB_NAME, **HYP3_PARAMS
        )
        rec["hyp3_job_id"] = job.job_id
        rec["hyp3_reused"] = False
        submitted.append(job.job_id)
        print(f"  submit {rec['pairname']} {job.job_id}")
    credits_after = h.check_credits()
    plan.setdefault("credits_log", []).append(
        dict(utc=now(), before=credits_before, after=credits_after, submitted=submitted)
    )
    save(PLAN, plan)
    print(f"credits after: {credits_after}")


def job_status(h, plan) -> dict:
    out = {}
    for rec in plan["pairs"]:
        jid = rec.get("hyp3_job_id")
        if jid:
            out[rec["pairname"]] = h.get_job_by_id(jid)
    return out


def cmd_wait(args) -> bool:
    plan = load(PLAN)
    h = hyp3()
    deadline = time.time() + 60 * args.max_minutes
    while True:
        st = job_status(h, plan)
        codes = {k: j.status_code for k, j in st.items()}
        print(now(), json.dumps(codes))
        if all(j.complete() for j in st.values()):
            return True
        if time.time() + args.interval > deadline:
            return False
        time.sleep(args.interval)


# --------------------------------------------------------------------------- clip
def grid_transform():
    from rasterio.transform import from_origin

    return from_origin(GRID_X0, GRID_Y0, GRID_RES, GRID_RES)


def clip_to_grid(src_path: Path, dst_path: Path, resampling: str) -> dict:
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(src_path) as src:
        src_nodata = src.nodata
        nodata_note = "from file"
        if src_nodata is None:
            src_nodata, nodata_note = 0.0, "file had none; HyP3 convention 0 used"
        dst = np.full((GRID_H, GRID_W), np.nan, dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src_nodata,
            dst_transform=grid_transform(),
            dst_crs=GRID_CRS,
            dst_nodata=np.nan,
            resampling=getattr(Resampling, resampling),
        )
        src_crs = str(src.crs)
        src_res = src.res
    profile = dict(
        driver="GTiff", width=GRID_W, height=GRID_H, count=1, dtype="float32",
        crs=GRID_CRS, transform=grid_transform(), nodata=np.nan,
        compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256,
    )
    with rasterio.open(dst_path, "w", **profile) as out:
        out.write(dst, 1)
    return dict(
        src_crs=src_crs, src_res=list(src_res), src_nodata=src_nodata, nodata_note=nodata_note,
        resampling=resampling, valid_fraction=float(np.isfinite(dst).mean()),
    )


def coherence_stats(corr_path: Path) -> dict:
    import rasterio
    from pyproj import Transformer

    with rasterio.open(corr_path) as ds:
        c = ds.read(1).astype("float64")
    sx, sy = Transformer.from_crs("EPSG:4326", GRID_CRS, always_xy=True).transform(SOURCE_LON, SOURCE_LAT)
    xs = GRID_X0 + GRID_RES * (np.arange(GRID_W) + 0.5)
    ys = GRID_Y0 - GRID_RES * (np.arange(GRID_H) + 0.5)
    X, Y = np.meshgrid(xs, ys)
    near = np.hypot(X - sx, Y - sy) <= NEAR_SOURCE_M
    valid = np.isfinite(c) & (c > 0)

    def med(m):
        v = c[m & valid]
        return None if v.size == 0 else round(float(np.median(v)), 4)

    return dict(
        corr_median_within_2km=med(near),
        corr_median_box=med(np.ones_like(valid)),
        n_valid_within_2km=int((near & valid).sum()),
        n_pixels_within_2km=int(near.sum()),
        valid_fraction_box=round(float(valid.mean()), 4),
    )


def process_product(h, rec) -> None:
    job = h.get_job_by_id(rec["hyp3_job_id"])
    rec["hyp3_status"] = job.status_code
    if not job.succeeded():
        return
    pdir = OUT / rec["pairname"]
    if all((pdir / f"{k}.tif").exists() for k in LAYERS) and (pdir / "clip_info.json").exists():
        print(f"  done   {rec['pairname']}")
        return
    TMP.mkdir(parents=True, exist_ok=True)
    work = TMP / rec["pairname"]
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    zips = job.download_files(work)
    pdir.mkdir(parents=True, exist_ok=True)
    info, missing = {}, []
    try:
        for zpath in zips:
            with zipfile.ZipFile(zpath) as z:
                names = z.namelist()
                stem = Path(zpath).stem
                for layer, resampling in LAYERS.items():
                    member = next((n for n in names if n.endswith(f"_{layer}.tif")), None)
                    if member is None:
                        missing.append(layer)
                        continue
                    extracted = work / f"{layer}.tif"
                    with z.open(member) as fsrc, open(extracted, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                    info[layer] = clip_to_grid(extracted, pdir / f"{layer}.tif", resampling)
                    info[layer]["hyp3_file"] = member
                    extracted.unlink()
                meta = next((n for n in names if n.endswith(f"{stem}.txt")), None)
                if meta:  # small HyP3 processing-parameter file (baseline, reference point, ...)
                    (pdir / "hyp3_metadata.txt").write_bytes(z.read(meta))
                rec["hyp3_product"] = stem
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rec["layers"] = info
    rec["missing_layers"] = missing
    save(pdir / "clip_info.json", dict(pairname=rec["pairname"], layers=info, missing=missing))
    print(f"  clipped {rec['pairname']} layers={sorted(info)} missing={missing}")


def cmd_process(args) -> None:
    plan = load(PLAN)
    h = hyp3()
    for rec in plan["pairs"]:
        if rec.get("hyp3_job_id"):
            try:
                process_product(h, rec)
            except Exception as e:  # record, don't hide
                rec["process_error"] = f"{type(e).__name__}: {e}"
                print(f"  ERROR {rec['pairname']}: {rec['process_error']}", file=sys.stderr)
    save(PLAN, plan)
    if TMP.exists() and not any(TMP.iterdir()):
        TMP.rmdir()


def cmd_manifest(args) -> None:
    plan = load(PLAN)
    pairs = []
    for rec in plan["pairs"]:
        pdir = OUT / rec["pairname"]
        corr = pdir / "corr.tif"
        stats = coherence_stats(corr) if corr.exists() else None
        clip = load(pdir / "clip_info.json") if (pdir / "clip_info.json").exists() else {}
        ref, sec = rec["reference"], rec["secondary"]
        pairs.append(dict(
            pairname=rec["pairname"],
            reference_granule=ref["granule"],
            secondary_granule=sec["granule"],
            reference_date=ref["date"],
            secondary_date=sec["date"],
            temporal_baseline_days=rec["temporal_baseline_days"],
            platform=[ref["platform"], sec["platform"]],
            cross_platform=rec["cross_platform"],
            relative_orbit=ref["relative_orbit"],
            flight_direction=ref["flight_direction"],
            frame=ref["frame"],
            hyp3_job_id=rec.get("hyp3_job_id"),
            hyp3_status=rec.get("hyp3_status"),
            hyp3_product=rec.get("hyp3_product"),
            nisar_window=rec["nisar_window"],
            nisar_window_dates=rec["nisar_window_dates"],
            role=rec["role"],
            also_maps_to=rec.get("also_maps_to", []),
            date_offset_days=rec["date_offset_days"],
            nisar_desc_nearest=rec["nisar_desc_nearest"],
            spans_event=rec["spans_event"],
            files=sorted(p.name for p in pdir.glob("*.tif")) if pdir.exists() else [],
            missing_layers=clip.get("missing", rec.get("missing_layers")),
            layers=clip.get("layers"),
            coherence=stats,
            process_error=rec.get("process_error"),
        ))
    manifest = dict(
        generated_utc=now(),
        description="Sentinel-1 C-band InSAR control for Cascade Watch; HyP3 INSAR_GAMMA products clipped to the study grid",
        source_point=[SOURCE_LON, SOURCE_LAT],
        grid=dict(crs=GRID_CRS, x0=GRID_X0, y0=GRID_Y0, res_m=GRID_RES, width=GRID_W, height=GRID_H),
        hyp3=dict(job_type="INSAR_GAMMA", name=JOB_NAME, parameters=HYP3_PARAMS,
                  credits_log=plan.get("credits_log", [])),
        search=plan["search"],
        chosen_track=plan["chosen_track"],
        coherence_note="median of HyP3 corr (>0, finite) after bilinear resampling to the grid; 2 km = pixel centres within 2000 m of the source point",
        pairs=pairs,
    )
    save(MANIFEST, manifest)
    print(f"wrote {MANIFEST.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["plan", "submit", "wait", "process", "manifest", "all"])
    ap.add_argument("--max-minutes", type=float, default=9.0)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--resubmit-failed", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.step in ("plan", "all"):
        cmd_plan(args)
    if args.step in ("submit", "all"):
        cmd_submit(args)
    if args.step == "wait":
        sys.exit(0 if cmd_wait(args) else 3)
    if args.step == "all":
        cmd_wait(args)
    if args.step in ("process", "all"):
        cmd_process(args)
    if args.step in ("manifest", "all"):
        cmd_manifest(args)


if __name__ == "__main__":
    main()
