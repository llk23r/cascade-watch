"""Stage 1 — Fetch NISAR L2 subsets over one or more study boxes.

Reads only the study box out of each remote HDF5 granule on ASF (HTTP range
requests through an authenticated Earthdata session), so nothing close to the
2.3 GB granules lands on disk. When several sites want the same granule (the
backtest boxes share track 98 frame 16 with Langtang), it is opened once and
every box is read from the same open file.

Products
  GUNW  unwrapped phase, coherence, connected components, ionosphere screen,
        pixel offsets, LOS unit vectors and incidence (80 m)
  GOFF  dense pixel offsets, 3 layers, with variances and SNR (80 m)
  GCOV  RTC gamma0 HH and HV backscatter (10 m) for the imagery views

Release choice: for every pair/date we prefer composite release P05023 (the
processing that existed at the time, e.g. the 26 Jul–19 Aug pair was processed
24 Aug 2026, two days before the collapse) and fall back to X05026.

Boxes come from grid.site_settings (CW_SITE, default langtang); files go to
<site raw>/nisar/ with a manifest.json per site. Credentials come from ~/.netrc
via earthaccess.

Usage
  python pipeline/nisar_fetch.py [GUNW GOFF GCOV[:YYYYMMDD,...]]
      the CW_SITE box, every pair over the site point from 2026-05-20 to 2026-10-10
  python pipeline/nisar_fetch.py GUNW GOFF --want SITE=SPEC[,SPEC...] [--want ...] [--workers N]
      SPEC = TRACK:YYYYMMDD-YYYYMMDD  pairs on that track (e.g. A098) with reference >= the first date
                                      and secondary <= the second
           | TRACK_YYYYMMDD_YYYYMMDD  one pair (e.g. D149_20260928_20261010)
Stalled HTTP reads can hang a worker: kill and re-run, finished boxes are skipped.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import requests

from grid import SITE, site_settings

ASF_API = "https://api.daac.asf.alaska.edu/services/search/param"
START, END = "2026-05-20T00:00:00Z", "2026-10-10T00:00:00Z"  # default search span (the Langtang replay)


# ---------------------------------------------------------------- sites and boxes
def box_of(site: str) -> dict:
    s = site_settings(site)
    g = s["grid"]
    if s["truth"]:
        lon, lat = s["truth"]["lonlat"]
    else:
        from pyproj import Transformer

        lon, lat = Transformer.from_crs(g["epsg"], 4326, always_xy=True).transform(sum(g["x_edges"]) / 2, sum(g["y_edges"]) / 2)
    out = s["raw"] / "nisar"
    out.mkdir(parents=True, exist_ok=True)
    return dict(site=site, epsg=int(g["epsg"]), x0=float(g["x_edges"][0]), x1=float(g["x_edges"][1]),
                y0=float(g["y_edges"][0]), y1=float(g["y_edges"][1]), point=(float(lon), float(lat)), out=str(out))


def parse_spec(spec: str):
    m = re.fullmatch(r"([AD]\d{3}):(\d{8})-(\d{8})", spec)
    if m:
        return ("range", m.group(1), m.group(2), m.group(3))
    m = re.fullmatch(r"([AD]\d{3})_(\d{8})_(\d{8})", spec)
    if m:
        return ("pair", m.group(1), m.group(2), m.group(3))
    raise SystemExit(f"bad window spec {spec!r}")


def spec_matches(spec, k: tuple) -> bool:
    if len(k) != 5:  # GCOV dates are selected with LEVEL:dates
        return False
    trk = f"{k[1]}{k[2]:03d}"
    kind, t, a, b = spec
    if trk != t:
        return False
    return (k[3] >= a and k[4] <= b) if kind == "range" else (k[3] == a and k[4] == b)


# ---------------------------------------------------------------- ASF search
def search(level: str, point: tuple, start: str, end: str) -> list[dict]:
    r = requests.get(
        ASF_API,
        params=dict(platform="NISAR", processingLevel=level, intersectsWith=f"POINT({point[0]} {point[1]})",
                    start=start, end=end, output="geojson", maxResults=1000),
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


# ---------------------------------------------------------------- reading
def _s(v):
    v = v[()]
    if isinstance(v, bytes):
        return v.decode()
    if isinstance(v, np.ndarray) and v.dtype.kind == "S":
        return [x.decode() for x in v.tolist()]
    if isinstance(v, np.generic):
        return v.item()
    return v


def epsg_of(grp) -> int | None:
    """EPSG code of a geocoded NISAR grid (the `projection` dataset), None if it cannot be read."""
    if "projection" not in grp:
        return None
    p = grp["projection"]
    for key in ("epsg_code", "epsg"):
        if key in p.attrs:
            return int(np.asarray(p.attrs[key]).ravel()[0])
    try:
        v = int(np.asarray(p[()]).ravel()[0])
        return v if 1024 <= v <= 32767 else None
    except Exception:
        return None


class Box:
    """Where a granule grid (pixel-centre coordinates) falls inside a study box with 80 m-aligned edges."""

    def __init__(self, b: dict, xc: np.ndarray, yc: np.ndarray):
        cell = float(abs(xc[1] - xc[0]))
        self.nx = int(round((b["x1"] - b["x0"]) / cell))
        self.ny = int(round((b["y1"] - b["y0"]) / cell))
        cols = np.where((xc > b["x0"]) & (xc < b["x1"]))[0]
        rows = np.where((yc > b["y0"]) & (yc < b["y1"]))[0]
        if cols.size == 0 or rows.size == 0:
            raise RuntimeError(f"{b['site']}: box outside this granule")
        self.rs, self.cs = slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1)
        fc = (xc[self.cs][0] - b["x0"] - cell / 2) / cell
        fr = (b["y1"] - cell / 2 - yc[self.rs][0]) / cell
        if abs(fc - round(fc)) > 1e-6 or abs(fr - round(fr)) > 1e-6:
            raise RuntimeError(f"{b['site']}: granule pixel centres are not on the box lattice")
        self.c0, self.r0 = int(round(fc)), int(round(fr))
        self.x = b["x0"] + cell / 2 + cell * np.arange(self.nx)
        self.y = b["y1"] - cell / 2 - cell * np.arange(self.ny)
        self.coverage = float((rows.size * cols.size) / (self.nx * self.ny))

    def read(self, ds, fill=None) -> np.ndarray:
        """The box out of a 2-D dataset; cells outside the granule get `fill` (NaN, or 0 for integers)."""
        a = ds[self.rs, self.cs]
        if a.shape == (self.ny, self.nx):
            return a
        if fill is None:
            fill = np.nan if a.dtype.kind == "f" else 0
        out = np.full((self.ny, self.nx), fill, dtype=a.dtype)
        out[self.r0 : self.r0 + a.shape[0], self.c0 : self.c0 + a.shape[1]] = a
        return out


class WarpBox(Box):
    """A granule geocoded in another UTM zone than the box (e.g. a frame in 44N for a 45N box): every box cell
    takes the granule pixel nearest to its centre, so values, mask bits and component labels stay as they are."""

    def __init__(self, b: dict, xc: np.ndarray, yc: np.ndarray, src_epsg: int):
        from pyproj import Transformer

        cell = float(abs(xc[1] - xc[0]))
        self.nx = int(round((b["x1"] - b["x0"]) / cell))
        self.ny = int(round((b["y1"] - b["y0"]) / cell))
        self.x = b["x0"] + cell / 2 + cell * np.arange(self.nx)
        self.y = b["y1"] - cell / 2 - cell * np.arange(self.ny)
        xx, yy = np.meshgrid(self.x, self.y)
        sx, sy = Transformer.from_crs(b["epsg"], src_epsg, always_xy=True).transform(xx, yy)
        ci = np.rint((sx - xc[0]) / (xc[1] - xc[0])).astype(np.int64)
        ri = np.rint((sy - yc[0]) / (yc[1] - yc[0])).astype(np.int64)
        self.inside = (ci >= 0) & (ci < xc.size) & (ri >= 0) & (ri < yc.size)
        if not self.inside.any():
            raise RuntimeError(f"{b['site']}: box outside this granule")
        r0, r1, c0, c1 = ri[self.inside].min(), ri[self.inside].max(), ci[self.inside].min(), ci[self.inside].max()
        self.rs, self.cs = slice(r0, r1 + 1), slice(c0, c1 + 1)
        self.ri, self.ci = ri[self.inside] - r0, ci[self.inside] - c0
        self.coverage = float(self.inside.mean())

    def read(self, ds, fill=None) -> np.ndarray:
        a = ds[self.rs, self.cs]
        if fill is None:
            fill = np.nan if a.dtype.kind == "f" else 0
        out = np.full((self.ny, self.nx), fill, dtype=a.dtype)
        out[self.inside] = a[self.ri, self.ci]
        return out


def make_box(b: dict, xc: np.ndarray, yc: np.ndarray, src_epsg: int | None) -> Box:
    return Box(b, xc, yc) if src_epsg in (None, b["epsg"]) else WarpBox(b, xc, yc, src_epsg)


def radar_cube(rg, b: dict, src_epsg: int | None, margin: float = 2000.0) -> dict:
    """The radar-geometry cube (LOS and along-track unit vectors, incidence) around the box, on box-CRS axes.
    Same zone: the product's own nodes. Other zone: bilinear onto a regular box-CRS lattice with the product's
    node spacing. The vector components are used as the product gives them (east, north)."""
    rx, ry = rg["xCoordinates"][:], rg["yCoordinates"][:]
    names = ("losUnitVectorX", "losUnitVectorY", "incidenceAngle", "alongTrackUnitVectorX", "alongTrackUnitVectorY")
    out = {"rg_h": rg["heightAboveEllipsoid"][:]}
    if src_epsg in (None, b["epsg"]):
        cc = np.where((rx > b["x0"] - margin) & (rx < b["x1"] + margin))[0]
        rr = np.where((ry > b["y0"] - margin) & (ry < b["y1"] + margin))[0]
        sl = (slice(None), slice(rr[0], rr[-1] + 1), slice(cc[0], cc[-1] + 1))
        for n in names:
            out["rg_" + n] = rg[n][sl]
        out["rg_x"], out["rg_y"] = rx[sl[2]], ry[sl[1]]
        return out
    from pyproj import Transformer
    from scipy.interpolate import RegularGridInterpolator

    step = float(abs(rx[1] - rx[0]))
    tx = np.arange(b["x0"] - margin, b["x1"] + margin + step, step)
    ty = np.arange(b["y1"] + margin, b["y0"] - margin - step, -step)
    xx, yy = np.meshgrid(tx, ty)
    sx, sy = Transformer.from_crs(b["epsg"], src_epsg, always_xy=True).transform(xx, yy)
    cc = np.where((rx > sx.min() - 2 * step) & (rx < sx.max() + 2 * step))[0]
    rr = np.where((ry > sy.min() - 2 * step) & (ry < sy.max() + 2 * step))[0]
    sl = (slice(None), slice(rr[0], rr[-1] + 1), slice(cc[0], cc[-1] + 1))
    sub_x, sub_y = rx[sl[2]], ry[sl[1]]
    order = np.argsort(sub_y)
    pts = np.stack([sy.ravel(), sx.ravel()], -1)
    for n in names:
        cube = rg[n][sl][:, order, :]
        out["rg_" + n] = np.stack([RegularGridInterpolator((sub_y[order], sub_x), cube[i], bounds_error=False, fill_value=None)(pts)
                                   .reshape(xx.shape) for i in range(cube.shape[0])]).astype(cube.dtype)
    out["rg_x"], out["rg_y"] = tx, ty
    return out


def read_box(level: str, h, b: dict) -> tuple[dict, dict]:
    meta: dict = {}
    arrays: dict[str, np.ndarray] = {}
    MASK_FILL = 255  # the products' "outside the swath" mask code (clean.decode_mask)
    if level == "GUNW":
        g = h["science/LSAR/GUNW"]
        ui = g["grids/frequencyA/unwrappedInterferogram"]
        meta["epsg"] = epsg_of(ui)
        bx = make_box(b, ui["xCoordinates"][:], ui["yCoordinates"][:], meta["epsg"])
        for n in ("unwrappedPhase", "coherenceMagnitude", "connectedComponents", "ionospherePhaseScreen"):
            arrays[n] = bx.read(ui["HH/" + n])
        arrays["unw_mask"] = bx.read(ui["mask"], MASK_FILL)
        arrays["x"], arrays["y"] = bx.x, bx.y
        meta["box_coverage"] = bx.coverage
        po = g["grids/frequencyA/pixelOffsets"]
        pb = make_box(b, po["xCoordinates"][:], po["yCoordinates"][:], epsg_of(po) or meta["epsg"])
        for n in ("alongTrackOffset", "slantRangeOffset", "correlationSurfacePeak"):
            arrays["po_" + n] = pb.read(po["HH/" + n])
        arrays.update(radar_cube(g["metadata/radarGrid"], b, meta["epsg"]))
    elif level == "GOFF":
        po = h["science/LSAR/GOFF/grids/frequencyA/pixelOffsets"]
        meta["epsg"] = epsg_of(po)
        bx = make_box(b, po["xCoordinates"][:], po["yCoordinates"][:], meta["epsg"])
        arrays["mask"] = bx.read(po["mask"], MASK_FILL)
        arrays["x"], arrays["y"] = bx.x, bx.y
        meta["box_coverage"] = bx.coverage
        for L in ("layer1", "layer2", "layer3"):
            for n in po["HH/" + L]:
                d = po[f"HH/{L}/{n}"]
                if d.ndim == 2:
                    arrays[f"{L}_{n}"] = bx.read(d)
    elif level == "GCOV":
        g = h["science/LSAR/GCOV/grids/frequencyA"]
        meta["epsg"] = epsg_of(g)
        bx = make_box(b, g["xCoordinates"][:], g["yCoordinates"][:], meta["epsg"])
        for n in ("HHHH", "HVHV"):
            v = bx.read(g[n])
            with np.errstate(divide="ignore", invalid="ignore"):
                arrays[n + "_db"] = (10 * np.log10(v)).astype(np.float16)
        arrays["mask"] = bx.read(g["mask"], MASK_FILL)
        arrays["x"], arrays["y"] = bx.x, bx.y
        meta["box_coverage"] = bx.coverage
    if meta["epsg"] not in (None, b["epsg"]):
        meta["resampled"] = (f"granule grid EPSG:{meta['epsg']} -> box EPSG:{b['epsg']}: nearest granule pixel per box cell; "
                             "radar-geometry cube bilinear")
    return meta, arrays


def granule_meta(level: str, h) -> dict:
    ident = h["science/LSAR/identification"]
    meta = {
        "granule": _s(ident["granuleId"]),
        "processingDateTime": _s(ident["processingDateTime"]),
        "orbitPassDirection": _s(ident["orbitPassDirection"]),
        "lookDirection": _s(ident["lookDirection"]),
        "trackNumber": _s(ident["trackNumber"]),
        "frameNumber": _s(ident["frameNumber"]),
    }
    if level == "GUNW":
        g = h["science/LSAR/GUNW"]
        meta["referenceStart"] = _s(ident["referenceZeroDopplerStartTime"])
        meta["secondaryStart"] = _s(ident["secondaryZeroDopplerStartTime"])
        meta["centerFrequency"] = _s(g["grids/frequencyA/centerFrequency"])
        meta["temporalBaseline_days"] = _s(g["metadata/orbit/temporalBaseline"])
        par = g["metadata/processingInformation/parameters/geocoding"]
        for n in par:
            meta[n] = _s(par[n])
    elif level == "GOFF":
        meta["referenceStart"] = _s(ident["referenceZeroDopplerStartTime"])
        meta["secondaryStart"] = _s(ident["secondaryZeroDopplerStartTime"])
        par = h["science/LSAR/GOFF/metadata/processingInformation/parameters/pixelOffsets/frequencyA"]
        for L in par:
            if hasattr(par[L], "keys"):
                for n in par[L]:
                    meta[f"{L}_{n}"] = _s(par[f"{L}/{n}"])
            else:
                meta[L] = _s(par[L])
    elif level == "GCOV":
        meta["zeroDopplerStartTime"] = _s(ident["zeroDopplerStartTime"])
    return meta


def fetch_one(level: str, k: tuple, url: str, boxes: list[dict]) -> list[dict]:
    """Open one granule and write the subset of every box that does not have it yet."""
    import earthaccess
    import h5py

    todo = [b for b in boxes if not (Path(b["out"]) / f"{name_of(k)}.npz").exists()]
    if not todo:
        return [{"site": b["site"], "skipped": True} for b in boxes]
    earthaccess.login(strategy="netrc")
    fs = earthaccess.get_fsspec_https_session()
    res = []
    t0 = time.time()
    with fs.open(url, block_size=2**22, cache_type="blockcache") as fh, h5py.File(fh, "r") as h:
        gmeta = granule_meta(level, h)
        for b in todo:
            bmeta, arrays = read_box(level, h, b)
            out = Path(b["out"])
            np.savez_compressed(out / f"{name_of(k)}.npz", **arrays)
            meta = {**gmeta, **bmeta, "boxes_read_from_this_open": [x["site"] for x in todo],
                    "seconds": round(time.time() - t0, 1)}  # since the granule was opened
            (out / f"{name_of(k)}.json").write_text(json.dumps(meta, indent=1, default=str))
            res.append({"site": b["site"], **meta})
    return res


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("levels", nargs="*", default=["GUNW", "GOFF"], help="GUNW GOFF GCOV[:YYYYMMDD,...]")
    ap.add_argument("--want", action="append", default=[], help="SITE=SPEC[,SPEC...] (repeatable)")
    ap.add_argument("--workers", type=int, default=3, help="parallel granules (keep small: stalled reads hang)")
    a = ap.parse_args()

    wants: dict[str, list | None] = {}
    for w in a.want:
        site, _, specs = w.partition("=")
        wants[site] = [parse_spec(s) for s in specs.split(",") if s]
    if not wants:
        wants = {SITE: None}  # every pair in START..END over the site point
    boxes = {s: box_of(s) for s in wants}

    jobs: dict[str, dict] = {}  # url -> job (one open per granule, every box that wants it)
    for site, specs in wants.items():
        b = boxes[site]
        man_path = Path(b["out"]) / "manifest.json"
        manifest = json.loads(man_path.read_text()) if man_path.exists() else {}
        if specs:
            d0 = min(s[2] for s in specs)
            d1 = max(s[3] for s in specs)
            start = datetime.strptime(d0, "%Y%m%d").strftime("%Y-%m-%dT00:00:00Z")
            end = (datetime.strptime(d1, "%Y%m%d") + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")
        else:
            start, end = START, END
        for spec in a.levels:
            level, _, only = spec.partition(":")  # e.g. GCOV:20260819,20260831 limits to those dates
            only_dates = set(only.split(",")) if only else None
            chosen = pick(search(level, b["point"], start, end))
            for k, p in sorted(chosen.items()):
                if only_dates and not (set(k[3:]) & only_dates):
                    continue
                if specs and not any(spec_matches(s, k) for s in specs):
                    continue
                manifest[name_of(k)] = {
                    "level": level, "scene": p["sceneName"], "crid": p["_crid"], "url": p["url"],
                    "asf_processingDate": p.get("processingDate"), "direction": k[1], "track": k[2], "dates": list(k[3:]),
                }
                j = jobs.setdefault(p["url"], {"level": level, "key": k, "boxes": []})
                j["boxes"].append(b)
        man_path.write_text(json.dumps(manifest, indent=1))
        print(site, "manifest", len(manifest), "entries", flush=True)

    print(f"{len(jobs)} granules to open for {sum(len(j['boxes']) for j in jobs.values())} box subsets", flush=True)
    with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(fetch_one, j["level"], j["key"], url, j["boxes"]): j for url, j in jobs.items()}
        for f in cf.as_completed(futs):
            j = futs[f]
            try:
                for r in f.result():
                    print("ok", r["site"], name_of(j["key"]), r.get("seconds", "cached"), "coverage", r.get("box_coverage"), flush=True)
            except Exception as e:  # report and keep going; a missing granule is reported, never filled
                print("FAIL", name_of(j["key"]), [b["site"] for b in j["boxes"]], repr(e)[:300], flush=True)


if __name__ == "__main__":
    main()
