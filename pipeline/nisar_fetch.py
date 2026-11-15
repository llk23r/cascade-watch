"""Stage 1 — Fetch NISAR L2 subsets over the Cascade Watch study box.

Reads only the study box out of each remote HDF5 granule on ASF (HTTP range
requests through an authenticated Earthdata session), so nothing close to the
2.3 GB granules lands on disk.

Products
  GUNW  unwrapped phase, coherence, connected components, ionosphere screen,
        pixel offsets, LOS unit vectors and incidence (80 m)
  GOFF  dense pixel offsets, 3 layers, with variances and SNR (80 m)
  GCOV  RTC gamma0 HH and HV backscatter (10 m) for the imagery views

Release choice: for every pair/date we prefer composite release P05023 (the
processing that existed at the time, e.g. the 26 Jul–19 Aug pair was processed
24 Aug 2026, two days before the collapse) and fall back to X05026.

Credentials come from ~/.netrc via earthaccess. Nothing is written to the repo.
Usage:  python pipeline/nisar_fetch.py [GUNW GOFF GCOV]
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "raw" / "nisar"
OUT.mkdir(parents=True, exist_ok=True)

# Study box: cell EDGES in UTM 45N (EPSG:32645). NISAR coordinates are pixel
# centres on an 80 m lattice whose edges are multiples of 80 m, so these edges
# align exactly: 463 x 412 cells at 80 m, 3704 x 3296 at 10 m.
X0, X1, Y0, Y1 = 336400.0, 373440.0, 3113680.0, 3146640.0
SOURCE_LONLAT = (85.515, 28.271)
ASF_API = "https://api.daac.asf.alaska.edu/services/search/param"
START, END = "2026-05-20T00:00:00Z", "2026-10-10T00:00:00Z"


def search(level: str) -> list[dict]:
    r = requests.get(
        ASF_API,
        params=dict(
            platform="NISAR",
            processingLevel=level,
            intersectsWith=f"POINT({SOURCE_LONLAT[0]} {SOURCE_LONLAT[1]})",
            start=START,
            end=END,
            output="geojson",
            maxResults=1000,
        ),
        timeout=120,
    )
    r.raise_for_status()
    return [f["properties"] for f in r.json()["features"]]


def key_of(p: dict) -> tuple:
    dates = re.findall(r"_(\d{8})T\d{6}", p["sceneName"])
    d = p["flightDirection"][0]
    if p["processingLevel"] in ("GUNW", "GOFF"):
        return (p["processingLevel"], d, int(p["pathNumber"]), dates[0], dates[2])
    return (p["processingLevel"], d, int(p["pathNumber"]), dates[0])


def pick(props: list[dict]) -> dict[tuple, dict]:
    best: dict[tuple, dict] = {}
    for p in props:
        k = key_of(p)
        crid = re.search(r"_([PX]\d{5})_", p["sceneName"]).group(1)
        rank = 0 if crid.startswith("P") else 1
        if k not in best or rank < best[k]["_rank"]:
            best[k] = {**p, "_rank": rank, "_crid": crid}
    return best


def name_of(k: tuple) -> str:
    if len(k) == 5:
        return f"{k[0]}_{k[1]}{k[2]:03d}_{k[3]}_{k[4]}"
    return f"{k[0]}_{k[1]}{k[2]:03d}_{k[3]}"


def box_slices(xc: np.ndarray, yc: np.ndarray):
    cols = np.where((xc > X0) & (xc < X1))[0]
    rows = np.where((yc > Y0) & (yc < Y1))[0]
    return slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1)


def _s(v):
    v = v[()]
    if isinstance(v, bytes):
        return v.decode()
    if isinstance(v, np.ndarray) and v.dtype.kind == "S":
        return [x.decode() for x in v.tolist()]
    if isinstance(v, np.generic):
        return v.item()
    return v


def fetch_one(level: str, k: tuple, url: str) -> dict:
    import earthaccess
    import h5py

    out = OUT / f"{name_of(k)}.npz"
    if out.exists():
        return {"name": out.name, "skipped": True}
    earthaccess.login(strategy="netrc")
    fs = earthaccess.get_fsspec_https_session()
    t0 = time.time()
    with fs.open(url, block_size=2**22, cache_type="blockcache") as fh, h5py.File(fh, "r") as h:
        ident = h["science/LSAR/identification"]
        meta = {
            "granule": _s(ident["granuleId"]),
            "processingDateTime": _s(ident["processingDateTime"]),
            "orbitPassDirection": _s(ident["orbitPassDirection"]),
            "lookDirection": _s(ident["lookDirection"]),
            "trackNumber": _s(ident["trackNumber"]),
            "frameNumber": _s(ident["frameNumber"]),
        }
        arrays: dict[str, np.ndarray] = {}
        if level == "GUNW":
            g = h["science/LSAR/GUNW"]
            meta["referenceStart"] = _s(ident["referenceZeroDopplerStartTime"])
            meta["secondaryStart"] = _s(ident["secondaryZeroDopplerStartTime"])
            meta["centerFrequency"] = _s(g["grids/frequencyA/centerFrequency"])
            meta["temporalBaseline_days"] = _s(g["metadata/orbit/temporalBaseline"])
            par = g["metadata/processingInformation/parameters/geocoding"]
            for n in par:
                meta[n] = _s(par[n])
            ui = g["grids/frequencyA/unwrappedInterferogram"]
            rs, cs = box_slices(ui["xCoordinates"][:], ui["yCoordinates"][:])
            for n in ("unwrappedPhase", "coherenceMagnitude", "connectedComponents", "ionospherePhaseScreen"):
                arrays[n] = ui["HH/" + n][rs, cs]
            arrays["unw_mask"] = ui["mask"][rs, cs]
            arrays["x"] = ui["xCoordinates"][cs]
            arrays["y"] = ui["yCoordinates"][rs]
            po = g["grids/frequencyA/pixelOffsets"]
            prs, pcs = box_slices(po["xCoordinates"][:], po["yCoordinates"][:])
            for n in ("alongTrackOffset", "slantRangeOffset", "correlationSurfacePeak"):
                arrays["po_" + n] = po["HH/" + n][prs, pcs]
            rg = g["metadata/radarGrid"]
            rx, ry = rg["xCoordinates"][:], rg["yCoordinates"][:]
            cc = np.where((rx > X0 - 2000) & (rx < X1 + 2000))[0]
            rr = np.where((ry > Y0 - 2000) & (ry < Y1 + 2000))[0]
            sl = (slice(None), slice(rr[0], rr[-1] + 1), slice(cc[0], cc[-1] + 1))
            for n in ("losUnitVectorX", "losUnitVectorY", "incidenceAngle", "alongTrackUnitVectorX", "alongTrackUnitVectorY"):
                arrays["rg_" + n] = rg[n][sl]
            arrays["rg_x"] = rx[sl[2]]
            arrays["rg_y"] = ry[sl[1]]
            arrays["rg_h"] = rg["heightAboveEllipsoid"][:]
        elif level == "GOFF":
            g = h["science/LSAR/GOFF"]
            meta["referenceStart"] = _s(ident["referenceZeroDopplerStartTime"])
            meta["secondaryStart"] = _s(ident["secondaryZeroDopplerStartTime"])
            po = g["grids/frequencyA/pixelOffsets"]
            rs, cs = box_slices(po["xCoordinates"][:], po["yCoordinates"][:])
            arrays["mask"] = po["mask"][rs, cs]
            arrays["x"] = po["xCoordinates"][cs]
            arrays["y"] = po["yCoordinates"][rs]
            for L in ("layer1", "layer2", "layer3"):
                for n in po["HH/" + L]:
                    d = po[f"HH/{L}/{n}"]
                    if d.ndim == 2:
                        arrays[f"{L}_{n}"] = d[rs, cs]
            par = g["metadata/processingInformation/parameters/pixelOffsets/frequencyA"]
            for L in par:
                if hasattr(par[L], "keys"):
                    for n in par[L]:
                        meta[f"{L}_{n}"] = _s(par[f"{L}/{n}"])
                else:
                    meta[L] = _s(par[L])
        elif level == "GCOV":
            g = h["science/LSAR/GCOV/grids/frequencyA"]
            meta["zeroDopplerStartTime"] = _s(ident["zeroDopplerStartTime"])
            rs, cs = box_slices(g["xCoordinates"][:], g["yCoordinates"][:])
            for n in ("HHHH", "HVHV"):
                v = g[n][rs, cs]
                with np.errstate(divide="ignore", invalid="ignore"):
                    arrays[n + "_db"] = (10 * np.log10(v)).astype(np.float16)
            arrays["mask"] = g["mask"][rs, cs]
            arrays["x"] = g["xCoordinates"][cs]
            arrays["y"] = g["yCoordinates"][rs]
    np.savez_compressed(out, **arrays)
    meta["seconds"] = round(time.time() - t0, 1)
    (OUT / f"{name_of(k)}.json").write_text(json.dumps(meta, indent=1, default=str))
    return {"name": out.name, **meta}


def main(levels: list[str]) -> None:
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    jobs = []
    for spec in levels:
        level, _, only = spec.partition(":")  # e.g. GCOV:20260819,20260831 limits to those dates
        only_dates = set(only.split(",")) if only else None
        chosen = pick(search(level))
        for k, p in sorted(chosen.items()):
            if only_dates and not (set(k[3:]) & only_dates):
                continue
            manifest[name_of(k)] = {
                "level": level,
                "scene": p["sceneName"],
                "crid": p["_crid"],
                "url": p["url"],
                "asf_processingDate": p.get("processingDate"),
                "direction": k[1],
                "track": k[2],
                "dates": list(k[3:]),
            }
            jobs.append((level, k, p["url"]))
    manifest_path.write_text(json.dumps(manifest, indent=1))
    print(f"{len(jobs)} granules to subset", flush=True)
    with cf.ProcessPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_one, *j): j for j in jobs}
        for f in cf.as_completed(futs):
            j = futs[f]
            try:
                r = f.result()
                print("ok", name_of(j[1]), r.get("seconds", "cached"), flush=True)
            except Exception as e:  # report and keep going; a missing granule is reported, never filled
                print("FAIL", name_of(j[1]), repr(e)[:300], flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or ["GUNW", "GOFF"])
