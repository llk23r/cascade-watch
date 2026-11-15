#!/usr/bin/env python
"""Fetch real ancillary geodata for Cascade Watch.

The study box and corridor come from grid.py (CW_SITE, default langtang). Everything
lands in <site raw>/ancillary/<name>/ (raw/ancillary/ for Langtang) and is described
in its manifest.json. Re-runnable: each step skips outputs that already exist (use
--force to redo a step). Real data only: if a source fails, the failure is written
to the manifest "missing" array and the step moves on.

Backtest sites get only what the ranking reads: DEM, RGI glacier outlines, WorldPop,
OSM rivers. ERA5 is not fetched for them: the season factor is the same for every
patch in a pass, so it cannot change ranks (rank.py sets it to 1 and says so).

Usage:
    .venv/bin/python -I pipeline/fetch_ancillary.py [--only dem,glaciers,population,osm,era5,meena] [--force]
                                                    [--keep-downloads]

Earthdata credentials are read from ~/.netrc by earthaccess (never printed).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import shutil
import sys
import time
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # grid.py, also under python -I
from grid import (CELL, CORRIDOR_LONLAT, CRS, DEFAULT_SITE, NX, NY, RAW, SITE, TRUTH,  # noqa: E402
                  X_EDGE0, X_EDGE1, Y_EDGE0, Y_EDGE1)

OUT = RAW / "ancillary"
MANIFEST = OUT / "manifest.json"
CACHE = ROOT / "raw" / "cache"   # national downloads shared between sites (git-ignored)
UA = {"User-Agent": "cascade-watch-ancillary-fetch/1.0 (research; python-requests)"}
FULL = SITE == DEFAULT_SITE      # the Langtang replay also wants debris, GLIMS, roads, places, ERA5, Meena

# ---------------------------------------------------------------- regions (all from grid.py)
UTM = CRS
BOX_X0, BOX_X1 = X_EDGE0, X_EDGE1
BOX_Y0, BOX_Y1 = Y_EDGE0, Y_EDGE1
GRID80 = dict(res=CELL, cols=NX, rows=NY)            # exactly the study grid
GRID30 = dict(res=30.0, cols=math.ceil((BOX_X1 - BOX_X0) / 30.0),
              rows=math.ceil((BOX_Y1 - BOX_Y0) / 30.0))  # 1235 x 1099 for Langtang, same origin
CORRIDOR = CORRIDOR_LONLAT                           # W, S, E, N (lon/lat)
SOURCE_PT = (TRUTH["lonlat"][1], TRUTH["lonlat"][0]) if TRUTH else None  # lat, lon
SOURCE_ELEV = 5190   # unsourced earlier guess, kept only as a sensitivity run; primary uses the API DEM elevation
EVENT_YEAR = 2026

DEM_URL = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{t}_DEM/"
           "Copernicus_DSM_COG_10_{t}_DEM.tif")
RGI_URL = ("https://daacdata.apps.nsidc.org/pub/DATASETS/nsidc0770_rgi_v7/regional_files/"
           "RGI2000-v7.0-G/RGI2000-v7.0-G-15_south_asia_east.zip")
GLIMS_SVC = "https://www.glims.org/services"
WORLDPOP_API = "https://hub.worldpop.org/rest/data/pop/G2_CN_POP_R25A_100m?iso3=NPL"
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
MEENA_URL = "https://assets-eu.researchsquare.com/files/rs-11022365/v1/b0dbe1d825f6eb03362df3d2.csv"

GDAL_ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="2",
                VSI_CACHE="TRUE")


# ---------------------------------------------------------------- helpers
def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(p: Path) -> str:
    return str(Path(p).resolve().relative_to(ROOT))


def size_mb(p: Path) -> float:
    return round(Path(p).stat().st_size / 1e6, 3)


class Manifest:
    def __init__(self):
        self.data = {"files": [], "missing": []}
        if MANIFEST.exists():
            try:
                self.data = json.loads(MANIFEST.read_text())
            except Exception:
                pass
        self.files = {f["path"]: f for f in self.data.get("files", [])}
        self.missing = {}  # step -> list of entries logged this run
        self.old_missing = self.data.get("missing", [])
        self.step = None     # step currently running (set by main)
        self.wrote = set()   # steps that (re)wrote at least one file this run

    def add(self, path: Path, **kw):
        entry = {"path": rel(path), "size_mb": size_mb(path), **kw}
        entry.setdefault("retrieved_utc", utcnow())
        self.files[entry["path"]] = entry
        self.wrote.add(self.step)

    def touch(self, path: Path):
        """Keep the existing entry (file skipped) but refresh its size."""
        k = rel(path)
        if k in self.files:
            self.files[k]["size_mb"] = size_mb(path)

    def miss(self, step: str, item: str, tried: str, error: str = ""):
        self.missing.setdefault(step, []).append(
            {"step": step, "item": item, "tried": tried, "error": error[:500],
             "logged_utc": utcnow()})

    def save(self, steps_run):
        # drop entries for files that no longer exist
        files = [f for p, f in sorted(self.files.items()) if (ROOT / p).exists()]
        # a step's old "missing" entries are replaced only if that step did work
        # this run (wrote a file or logged a gap); skipped steps keep theirs.
        redone = {s for s in steps_run if s in self.wrote or s in self.missing}
        missing = [m for m in self.old_missing if m.get("step") not in redone]
        for step in steps_run:
            missing += self.missing.get(step, [])
        self.data = {
            "generated_utc": utcnow(),
            "generator": "pipeline/fetch_ancillary.py",
            "project": "Cascade Watch (NASA Space Apps 2026), site " + SITE,
            "regions": {
                "study_box_utm": {"crs": UTM, "x": [BOX_X0, BOX_X1], "y": [BOX_Y0, BOX_Y1],
                                  "grid": f"{NX} cols x {NY} rows of {CELL:.0f} m, origin ({BOX_X0:.0f}, {BOX_Y1:.0f})"},
                "corridor_lonlat": {"w": CORRIDOR[0], "s": CORRIDOR[1],
                                    "e": CORRIDOR[2], "n": CORRIDOR[3]},
                "source_point": ({"lat": SOURCE_PT[0], "lon": SOURCE_PT[1]} | ({"elev_m": SOURCE_ELEV} if FULL else {})
                                 if SOURCE_PT else None),
            },
            "files": files,
            "missing": missing,
        }
        MANIFEST.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))


def box_polygon_lonlat(densify: int = 50):
    """Study box (UTM) as a lon/lat shapely polygon, edges densified."""
    from pyproj import Transformer
    from shapely.geometry import Polygon
    tr = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    xs = np.linspace(BOX_X0, BOX_X1, densify)
    ys = np.linspace(BOX_Y0, BOX_Y1, densify)
    ring = ([(x, BOX_Y0) for x in xs] + [(BOX_X1, y) for y in ys] +
            [(x, BOX_Y1) for x in xs[::-1]] + [(BOX_X0, y) for y in ys[::-1]])
    lon, lat = tr.transform(*zip(*ring))
    return Polygon(zip(lon, lat))


def write_geojson(path: Path, features, meta: dict | None = None):
    fc = {"type": "FeatureCollection", "features": features}
    if meta:
        fc["metadata"] = meta
    path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))


def http_get(url, session=None, retries=4, **kw):
    s = session or requests
    kw.setdefault("timeout", 120)
    headers = {**UA, **kw.pop("headers", {})}
    last = None
    for i in range(retries):
        try:
            r = s.get(url, headers=headers, **kw)
            if r.status_code in (429, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code}")
                time.sleep(10 * (i + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"GET failed {url}: {last}")


def earthdata_session():
    import earthaccess
    earthaccess.login(strategy="netrc")
    return earthaccess.get_requests_https_session()


# ---------------------------------------------------------------- 1. DEM
def step_dem(m: Manifest, force: bool):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.merge import merge
    from rasterio.transform import Affine
    from rasterio.warp import reproject

    d = OUT / "dem"
    d.mkdir(parents=True, exist_ok=True)
    f_corr = d / "dem_corridor_4326.tif"
    f80 = d / "dem_box_utm45_80m.tif"
    f30 = d / "dem_box_utm45_30m.tif"
    tiles = [f"N{lat:02d}_00_E{lon:03d}_00"
             for lat in range(math.floor(CORRIDOR[1]), math.floor(CORRIDOR[3]) + 1)
             for lon in range(math.floor(CORRIDOR[0]), math.floor(CORRIDOR[2]) + 1)]
    urls = [DEM_URL.format(t=t) for t in tiles]
    lic = ("Copernicus DEM GLO-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH "
           "2014-2018, provided under COPERNICUS by the European Union and ESA; free licence "
           "(Copernicus DEM licence). Accessed via the AWS Open Data registry.")
    common = dict(source_url=urls, license=lic,
                  version="Copernicus DEM GLO-30 public release (COG, 2021/1 tiles on AWS)")

    if all(p.exists() for p in (f_corr, f80, f30)) and not force:
        for p in (f_corr, f80, f30):
            m.touch(p)
        print("dem: present, skipping")
        return

    with rasterio.Env(**GDAL_ENV):
        srcs = [rasterio.open("/vsicurl/" + u) for u in urls]
        # The tiles are PixelIsPoint (centres on whole arcseconds), so GDAL's area grid
        # starts half a pixel off whole degrees. Snap the corridor outward to that grid
        # so merge copies native pixels with no half-pixel resampling shift.
        t0 = srcs[0].transform
        rx, ry = t0.a, -t0.e
        snapped = (t0.c + math.floor(round((CORRIDOR[0] - t0.c) / rx, 6)) * rx,
                   t0.f - math.ceil(round((t0.f - CORRIDOR[1]) / ry, 6)) * ry,
                   t0.c + math.ceil(round((CORRIDOR[2] - t0.c) / rx, 6)) * rx,
                   t0.f - math.floor(round((t0.f - CORRIDOR[3]) / ry, 6)) * ry)
        mosaic, mtr = merge(srcs, bounds=snapped, nodata=-32767.0, dtype="float32")
        src_crs = srcs[0].crs
        for s in srcs:
            s.close()
    mosaic = mosaic[0]
    retrieved = utcnow()
    prof = dict(driver="GTiff", dtype="float32", count=1, nodata=-32767.0,
                compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(f_corr, "w", width=mosaic.shape[1], height=mosaic.shape[0],
                       crs=src_crs, transform=mtr, **prof) as dst:
        dst.write(mosaic, 1)
        dst.update_tags(SOURCE="Copernicus DEM GLO-30", VERTICAL_DATUM="EGM2008 geoid (m)",
                        TILES=",".join(tiles))
    m.add(f_corr, retrieved_utc=retrieved, **common,
          description=(f"Copernicus GLO-30 mosaic of tiles {', '.join(tiles)} clipped to the corridor "
                       f"lon {CORRIDOR[0]}-{CORRIDOR[2]} E, lat {CORRIDOR[1]}-{CORRIDOR[3]} N; "
                       "EPSG:4326, 1 arcsec, heights in m above EGM2008 geoid."))

    for f, g in ((f80, GRID80), (f30, GRID30)):
        dtr = Affine(g["res"], 0, BOX_X0, 0, -g["res"], BOX_Y1)
        out = np.full((g["rows"], g["cols"]), -32767.0, dtype="float32")
        reproject(mosaic, out, src_transform=mtr, src_crs=src_crs, src_nodata=-32767.0,
                  dst_transform=dtr, dst_crs=UTM, dst_nodata=-32767.0,
                  resampling=Resampling.bilinear)
        with rasterio.open(f, "w", width=g["cols"], height=g["rows"], crs=UTM,
                           transform=dtr, **prof) as dst:
            dst.write(out, 1)
            dst.update_tags(SOURCE="Copernicus DEM GLO-30, bilinear from dem_corridor_4326.tif",
                            VERTICAL_DATUM="EGM2008 geoid (m)")
        nodata_n = int((out == -32767.0).sum())
        extent = (BOX_X0, BOX_Y1 - g["rows"] * g["res"], BOX_X0 + g["cols"] * g["res"], BOX_Y1)
        m.add(f, retrieved_utc=retrieved, **common,
              description=(f"DEM bilinearly resampled onto the study-box grid: EPSG:32645, "
                           f"{g['res']:.0f} m, {g['cols']} cols x {g['rows']} rows, origin "
                           f"(x={BOX_X0:.0f}, y={BOX_Y1:.0f}), extent {extent}; nodata cells: {nodata_n}."))
    print("dem: done")


# ---------------------------------------------------------------- 2. glaciers
def _shape_geom(shp):
    """pyshp shape -> shapely geometry. Polygons are rebuilt from rings so that
    degenerate (zero-area) rings in the source are dropped instead of failing:
    shapefile outer rings are clockwise, holes counter-clockwise."""
    from shapely.geometry import Polygon, shape
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    if shp.shapeType not in (5, 15, 25):
        return shape(shp.__geo_interface__) if shp.points else None
    idx = list(shp.parts) + [len(shp.points)]
    outers, holes = [], []
    for a, b in zip(idx[:-1], idx[1:]):
        pts = [tuple(p[:2]) for p in shp.points[a:b]]
        if len(pts) < 4:
            continue
        x = np.array([p[0] for p in pts]); y = np.array([p[1] for p in pts])
        signed = 0.5 * float(np.sum(x[:-1] * y[1:] - x[1:] * y[:-1]))
        if signed == 0:
            continue
        ring = make_valid(Polygon(pts))
        if ring.geom_type == "GeometryCollection":
            ring = unary_union([p for p in ring.geoms if p.geom_type in ("Polygon", "MultiPolygon")])
        if ring.is_empty or ring.area == 0:
            continue
        (outers if signed < 0 else holes).append(ring)
    if not outers:
        return None
    g = unary_union(outers)
    if holes:
        g = g.difference(unary_union(holes))
    return None if g.is_empty else g


def _shp_records(zpath: Path, member_suffix: str, clip_poly):
    """Yield (geometry, props) from a zipped shapefile, clipped by bbox intersect."""
    import shapefile  # pyshp
    from shapely.geometry import shape
    from shapely.prepared import prep
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        base = [n for n in names if n.lower().endswith(member_suffix)]
        if not base:
            raise RuntimeError(f"{member_suffix} not in {zpath.name}: {names[:20]}")
        stem = base[0][:-4]
        get = lambda ext: io.BytesIO(z.read(stem + ext)) if (stem + ext) in names else None
        r = shapefile.Reader(shp=get(".shp"), shx=get(".shx"), dbf=get(".dbf"),
                             encoding="utf-8", encodingErrors="replace")
        prj = z.read(stem + ".prj").decode("utf-8", "replace") if (stem + ".prj") in names else ""
        fields = [f[0] for f in r.fields[1:]]
        pp = prep(clip_poly)
        bx = clip_poly.bounds
        out = []
        for sr in r.iterShapeRecords():
            sb = sr.shape.bbox
            if sb[2] < bx[0] or sb[0] > bx[2] or sb[3] < bx[1] or sb[1] > bx[3]:
                continue
            geom = _shape_geom(sr.shape)
            if geom is None or not pp.intersects(geom):
                continue
            props = {}
            for k, v in zip(fields, sr.record):
                if isinstance(v, bytes):
                    v = v.decode("utf-8", "replace")
                if hasattr(v, "isoformat"):
                    v = v.isoformat()
                props[k] = v
            out.append((geom, props))
        return out, fields, prj, stem


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP byte ranges, so zipfile can pull single
    members out of a large remote archive without downloading all of it."""

    def __init__(self, url):
        self.url, self.pos = url, 0
        h = requests.head(url, headers=UA, allow_redirects=True, timeout=60)
        h.raise_for_status()
        self.size = int(h.headers["Content-Length"])

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        r = http_get(self.url, headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}"}, timeout=300)
        data = r.content
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)


DEBRIS_URL = "https://datapub.gfz-potsdam.de/download/10.5880.GFZ.3.3.2018.005/S2_2015-2017_NDSI.zip"


def _polyarea_km2(geod, g):
    from shapely.geometry import MultiPolygon, Polygon
    if g.is_empty:
        return 0.0
    if g.geom_type == "GeometryCollection":
        polys = [p for p in g.geoms if isinstance(p, (Polygon, MultiPolygon))]
        return sum(_polyarea_km2(geod, p) for p in polys)
    if not isinstance(g, (Polygon, MultiPolygon)):
        return 0.0
    return abs(geod.geometry_area_perimeter(g)[0]) / 1e6


def _debris(m: Manifest, d: Path, box, force: bool):
    """Scherler, Wulf & Gorelick (2018) supraglacial debris (Sentinel-2 NDSI),
    region 15 members only; clip to box; annotate RGI 7.0 outlines."""
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    f_deb = d / "glacier_debris_scherler2018.geojson"
    f_rgi = d / "glaciers.geojson"
    if not f_deb.exists() or force:
        tmp = d / "_tmp_debris"
        tmp.mkdir(exist_ok=True)
        try:
            z = zipfile.ZipFile(io.BufferedReader(HttpRangeFile(DEBRIS_URL), buffer_size=1 << 20))
            members = [i for i in z.namelist() if "/15_rgi60_" in i]
            if not members:
                raise RuntimeError("no region-15 members in archive")
            local = tmp / "deb15.zip"
            with zipfile.ZipFile(local, "w") as zo:
                for mem in members:
                    zo.writestr(Path(mem).name, z.read(mem))
            retrieved = utcnow()
            recs, fields, prj, stem = _shp_records(local, ".shp", box)
            feats = []
            for g, p in recs:
                gi = g.intersection(box)
                if gi.is_empty:
                    continue
                p["area_in_box_km2"] = round(_polyarea_km2(geod, gi), 5)
                feats.append({"type": "Feature", "geometry": mapping(gi), "properties": p})
            write_geojson(f_deb, feats, meta={
                "source": "Scherler, Wulf & Gorelick (2018) supraglacial debris cover, Sentinel-2 2015-2017 NDSI",
                "url": DEBRIS_URL, "members": [Path(x).name for x in members], "fields": fields,
                "source_prj": prj.strip()[:200], "selection": "polygons clipped to the study box",
                "note": "Mapped within RGI 6.0 outlines; overlay on RGI 7.0 is approximate."})
            m.add(f_deb, retrieved_utc=retrieved, source_url=DEBRIS_URL,
                  license=("Scherler, D., Wulf, H., Gorelick, N. (2018): Supraglacial debris cover. GFZ Data "
                           "Services, doi:10.5880/GFZ.3.3.2018.005 (CC BY 4.0); paper: GRL 45, "
                           "doi:10.1029/2018GL080158."),
                  version="v1, Sentinel-2 2015-2017 NDSI classification, region 15 member",
                  description=(f"{len(feats)} supraglacial debris-cover polygons clipped to the study box "
                               "(only the region-15 shapefile members were range-read from the 1 GB archive)."))
            print(f"glaciers: debris {len(feats)} polygons")
        except Exception as e:
            traceback.print_exc()
            m.miss("glaciers", "debris-cover polygons (Scherler et al. 2018, S2 NDSI, region 15)", DEBRIS_URL, repr(e))
            return
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        m.touch(f_deb)
    # annotate RGI outlines with debris area / fraction (derived, real overlay)
    if not f_rgi.exists():
        return
    rgi = json.loads(f_rgi.read_text())
    if rgi["features"] and "debris_area_km2_s2018" in rgi["features"][0]["properties"] and not force:
        return
    deb = [shape(f["geometry"]) for f in json.loads(f_deb.read_text())["features"]]
    tree = STRtree(deb) if deb else None
    for f in rgi["features"]:
        g = shape(f["geometry"])
        hit = [deb[i] for i in tree.query(g)] if tree is not None else []
        inter = unary_union([g.intersection(h) for h in hit]) if hit else None
        g_in = g.intersection(box)
        a_in = _polyarea_km2(geod, g_in)
        a_deb = _polyarea_km2(geod, inter) if inter is not None else 0.0
        f["properties"]["debris_area_km2_s2018"] = round(a_deb, 4)
        f["properties"]["debris_frac_in_box_s2018"] = round(a_deb / a_in, 4) if a_in > 0 else None
    rgi.setdefault("metadata", {})["debris_fields"] = (
        "debris_area_km2_s2018 = geodesic area of Scherler et al. (2018) S2 NDSI debris polygons (clipped to the "
        "box) inside the outline; debris_frac_in_box_s2018 = that / outline area inside the box. No threshold applied.")
    write_geojson(f_rgi, rgi["features"], meta=rgi["metadata"])
    if rel(f_rgi) in m.files:
        e = m.files[rel(f_rgi)]
        e["size_mb"] = size_mb(f_rgi)
        if "debris" not in e["description"]:
            e["description"] += (" Annotated with debris_area_km2_s2018 / debris_frac_in_box_s2018 from "
                                 "glacier_debris_scherler2018.geojson.")


def step_glaciers(m: Manifest, force: bool):
    from shapely.geometry import mapping
    from pyproj import Geod
    d = OUT / "glaciers"
    d.mkdir(parents=True, exist_ok=True)
    f_rgi = d / "glaciers.geojson"
    f_glims = d / "glims_box.geojson"
    box = box_polygon_lonlat()
    geod = Geod(ellps="WGS84")
    sess = None

    # --- RGI 7.0 (primary)
    if f_rgi.exists() and not force:
        m.touch(f_rgi)
        print("glaciers: RGI present, skipping")
    else:
        tmp = d / "_tmp"
        tmp.mkdir(exist_ok=True)
        zpath = tmp / Path(RGI_URL).name
        try:
            sess = sess or earthdata_session()
            if not zpath.exists():
                with sess.get(RGI_URL, stream=True, timeout=600) as r:
                    r.raise_for_status()
                    with open(zpath, "wb") as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
            retrieved = utcnow()
            recs, fields, prj, stem = _shp_records(zpath, ".shp", box)
            feats = []
            for geom, props in recs:
                props["geod_area_km2_full"] = round(abs(geod.geometry_area_perimeter(geom)[0]) / 1e6, 4)
                inbox = geom.intersection(box)
                props["area_in_box_km2"] = round(abs(geod.geometry_area_perimeter(inbox)[0]) / 1e6, 4)
                props["source"] = "RGI 7.0 (RGI2000-v7.0-G, region 15 South Asia East)"
                feats.append({"type": "Feature", "geometry": mapping(geom), "properties": props})
            write_geojson(f_rgi, feats, meta={
                "source": "Randolph Glacier Inventory v7.0, glacier product (RGI2000-v7.0-G), region 15",
                "url": RGI_URL, "selection": "outlines intersecting the study box (full outlines kept)",
                "source_prj": prj.strip()[:200], "fields": fields,
                "note": ("RGI 7.0 has no debris-cover attribute; debris fields are added from "
                         "glacier_debris_scherler2018.geojson (GLIMS extent download has no debris_cov here).")})
            m.add(f_rgi, retrieved_utc=retrieved, source_url=RGI_URL,
                  license=("RGI 7.0 Consortium (2023), Randolph Glacier Inventory - A Dataset of Global Glacier "
                           "Outlines, Version 7.0, NSIDC DAAC, doi:10.5067/f6jmovy5navz. CC BY 4.0."),
                  version="RGI 7.0 (NSIDC-0770 v7), file RGI2000-v7.0-G-15_south_asia_east",
                  description=(f"{len(feats)} RGI 7.0 glacier outlines intersecting the study box, EPSG:4326, "
                               "all RGI attributes (rgi_id, glims_id, glac_name, area_km2, zmin/zmed/zmax, "
                               "slope, aspect, term_type, surge_type, ...) plus geodesic area_in_box_km2."))
            print(f"glaciers: RGI {len(feats)} outlines")
        except Exception as e:
            traceback.print_exc()
            m.miss("glaciers", "RGI 7.0 region 15 outlines", RGI_URL + " via earthaccess netrc session", repr(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if not FULL:
        print("glaciers: debris and GLIMS layers skipped for a backtest site (the ranking reads RGI outlines only)")
        return

    # --- supraglacial debris (RGI 7.0 has no debris attribute)
    _debris(m, d, box, force)

    # --- GLIMS extent download (secondary: carries line_type incl. debris_cov)
    if f_glims.exists() and not force:
        m.touch(f_glims)
        print("glaciers: GLIMS present, skipping")
        return
    tmp = d / "_tmp_glims"
    tmp.mkdir(exist_ok=True)
    try:
        sess = sess or earthdata_session()
        w, s, e, n = box.bounds
        clause = f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}"
        params = dict(download_type="extent", clause=clause, file_format="shapefile", archive_type="zip")
        fname = None
        for _ in range(4):  # first call can just complete the Earthdata auth handshake
            r = sess.get(f"{GLIMS_SVC}/downloadprepare", params=params, timeout=300)
            r.raise_for_status()
            j = r.json()
            if "filename" in j:
                fname = j["filename"]
                break
            time.sleep(2)
        if not fname:
            raise RuntimeError(f"downloadprepare returned no filename: {j}")
        for _ in range(90):
            r = sess.get(f"{GLIMS_SVC}/downloadfileready", params={"filename": fname}, timeout=120)
            txt = r.text.strip().lower()
            if r.ok and ("true" in txt or "ready" in txt) and "false" not in txt:
                break
            time.sleep(5)
        zpath = tmp / fname
        with sess.get(f"{GLIMS_SVC}/downloadfile", params={"filename": fname}, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(zpath, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        retrieved = utcnow()
        with zipfile.ZipFile(zpath) as z:
            shp_names = [x for x in z.namelist() if x.lower().endswith(".shp")]
        poly_shp = [x for x in shp_names if "polygon" in x.lower()] or shp_names
        recs, fields, prj, stem = _shp_records(zpath, Path(poly_shp[0]).name.lower(), box)
        feats = [{"type": "Feature", "geometry": mapping(g), "properties": p} for g, p in recs]
        write_geojson(f_glims, feats, meta={
            "source": "GLIMS Glacier Database (NSIDC), extent download", "bbox": clause,
            "member": stem, "fields": fields, "source_prj": prj.strip()[:200],
            "note": "All GLIMS polygons intersecting the box, all analyses/dates; line_type distinguishes "
                    "glac_bound, debris_cov, intrnl_rock, etc."})
        lt = {}
        for _, p in recs:
            lt[p.get("line_type")] = lt.get(p.get("line_type"), 0) + 1
        m.add(f_glims, retrieved_utc=retrieved,
              source_url=f"{GLIMS_SVC}/downloadprepare?download_type=extent&clause={clause}&file_format=shapefile&archive_type=zip",
              license=("GLIMS Consortium (2005, updated), GLIMS Glacier Database, Version 1, NSIDC DAAC, "
                       "doi:10.7265/N5V98602. Free use with citation."),
              version=f"GLIMS database snapshot at retrieval time ({stem})",
              description=f"{len(feats)} GLIMS polygons intersecting the study box, EPSG:4326; line_type counts: {lt}.")
        print(f"glaciers: GLIMS {len(feats)} polygons {lt}")
    except Exception as e:
        traceback.print_exc()
        m.miss("glaciers", "GLIMS polygons (debris_cov) for the box",
               f"{GLIMS_SVC}/downloadprepare extent download with Earthdata session; "
               "the documented GLIMS WFS (www.glims.org/geoserver/...) returned HTTP 404", repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 3. population
# Both countries for every site: the Langtang box's northern third is in Tibet (Gyirong county) and the
# backtest corridors reach into Tibet too; a country with no cells in the corridor adds nothing.
POP_COUNTRIES = ("NPL", "CHN")


def _worldpop_clip(m: Manifest, d: Path, iso3: str, keep: bool = False):
    """Download one WorldPop R2025A constrained 100 m national file (the server does
    not serve byte ranges, so no remote windowed read), clip to the corridor on the
    WorldPop 3-arcsec grid, delete the national file unless `keep` (it is cached in
    raw/cache/worldpop/ and reused by the next site). Returns (arr, transform, nodata, crs, meta)."""
    import rasterio
    from rasterio.windows import from_bounds
    api = http_get(WORLDPOP_API.replace("NPL", iso3), timeout=60).json()["data"]
    # R2025A runs 2015-2030 (modelled projections); take the event year 2026,
    # i.e. the latest year that is not in the future relative to the flood.
    rec = max((r for r in api if int(r["popyear"]) <= EVENT_YEAR), key=lambda r: int(r["popyear"]))
    url, year = rec["files"][0], int(rec["popyear"])
    cache = CACHE / "worldpop"
    cache.mkdir(parents=True, exist_ok=True)
    full = cache / Path(url).name
    part = full.with_suffix(full.suffix + ".part")
    try:
        if not full.exists():
            with requests.get(url, headers=UA, stream=True, timeout=900) as r:
                r.raise_for_status()
                with open(part, "wb") as fh:
                    for chunk in r.iter_content(1 << 22):
                        fh.write(chunk)
            part.rename(full)
        nbytes = full.stat().st_size
        with rasterio.open(full) as src:
            win = from_bounds(*CORRIDOR, transform=src.transform).round_offsets().round_lengths()
            arr = src.read(1, window=win, boundless=True, fill_value=src.nodata)
            tr = src.window_transform(win)
            nod, crs = src.nodata, src.crs
    finally:
        part.unlink(missing_ok=True)
        if not keep:
            full.unlink(missing_ok=True)
    return arr, tr, nod, crs, dict(iso3=iso3, url=url, year=year, doi=rec.get("doi"),
                                   downloaded_mb=round(nbytes / 1e6, 1), retrieved_utc=utcnow())


def _sum_in_box(arr, tr, nod):
    """Sum of cells whose centres fall inside the UTM study box."""
    from pyproj import Transformer
    rows, cols = np.nonzero((arr != nod) & np.isfinite(arr) & (arr > 0))
    if rows.size == 0:
        return 0.0
    lon = tr.c + (cols + 0.5) * tr.a
    lat = tr.f + (rows + 0.5) * tr.e
    x, y = Transformer.from_crs("EPSG:4326", UTM, always_xy=True).transform(lon, lat)
    inside = (x >= BOX_X0) & (x <= BOX_X1) & (y >= BOX_Y0) & (y <= BOX_Y1)
    return float(arr[rows[inside], cols[inside]].astype("float64").sum())


def step_population(m: Manifest, force: bool, keep: bool = False):
    import rasterio
    d = OUT / "population"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "population_corridor.tif"
    if f.exists() and not force:
        m.touch(f)
        print("population: present, skipping")
        return
    parts = []
    for iso3 in POP_COUNTRIES:
        try:
            parts.append(_worldpop_clip(m, d, iso3, keep))
        except Exception as e:
            traceback.print_exc()
            m.miss("population", f"WorldPop {iso3} 100 m constrained", WORLDPOP_API.replace("NPL", iso3), repr(e))
    if not parts:
        return
    arr0, tr0, nod0, crs0, _ = parts[0]
    for arr, tr, nod, crs, meta in parts[1:]:
        if arr.shape != arr0.shape or not tr.almost_equals(tr0, precision=1e-9):
            raise RuntimeError(f"WorldPop grids differ between countries ({meta['iso3']})")
    out = np.zeros(arr0.shape, dtype="float32")
    anyvalid = np.zeros(arr0.shape, dtype=bool)
    stats = []
    for arr, tr, nod, crs, meta in parts:
        ok = (arr != nod) & np.isfinite(arr)
        out[ok] += arr[ok]   # constrained products are country-masked; border cells add
        anyvalid |= ok
        stats.append({**meta, "corridor_total": round(float(arr[ok].astype("float64").sum())),
                      "box_total": round(_sum_in_box(arr, tr, nod))})
    nodata = -99999.0
    out[~anyvalid] = nodata
    with rasterio.open(f, "w", driver="GTiff", width=out.shape[1], height=out.shape[0], count=1,
                       dtype="float32", crs=crs0, transform=tr0, nodata=nodata, compress="deflate",
                       predictor=3, tiled=True, blockxsize=256, blockysize=256) as dst:
        dst.write(out, 1)
        dst.update_tags(SOURCES=" ; ".join(s["url"] for s in stats),
                        YEAR=",".join(str(s["year"]) for s in stats), UNITS="people per 3 arcsec (~100 m) cell")
    corr_total = round(float(out[anyvalid].astype("float64").sum()))
    box_total = round(_sum_in_box(out, tr0, nodata))
    per = "; ".join(f"{s['iso3']} {s['year']}: corridor {s['corridor_total']}, box {s['box_total']}" for s in stats)
    (d / "population_stats.json").write_text(json.dumps(
        {"corridor_bbox": CORRIDOR, "box_utm": [BOX_X0, BOX_Y0, BOX_X1, BOX_Y1],
         "corridor_total": corr_total, "box_total": box_total, "by_country": stats,
         "method": "sum of WorldPop cells (box: cell centres inside the UTM box)"}, indent=1))
    m.add(d / "population_stats.json", source_url=[s["url"] for s in stats],
          license="Derived from WorldPop (CC BY 4.0)", version="computed from population_corridor.tif inputs",
          description="Population totals for the corridor and the study box, per country.")
    m.add(f, retrieved_utc=stats[0]["retrieved_utc"], source_url=[s["url"] for s in stats],
          license=("WorldPop (www.worldpop.org), University of Southampton. CC BY 4.0. "
                   f"doi:{stats[0].get('doi') or '10.5258/SOTON/WP00839'}"),
          version=("WorldPop Global2 R2025A v1, constrained, 100 m, year "
                   + "/".join(sorted({str(s['year']) for s in stats})) + " (modelled estimate)"),
          description=(f"WorldPop constrained population counts per 3 arcsec cell, {'+'.join(s['iso3'] for s in stats)} "
                       f"merged and clipped to the corridor; EPSG:4326. Totals: corridor {corr_total}, study box "
                       f"{box_total} ({per})."))
    print(f"population: corridor {corr_total}, box {box_total}; {per}")


# ---------------------------------------------------------------- 4. OSM
OSM_CLAUSES = {  # Overpass clauses per output layer (order kept from the original single query)
    "places": ['node["place"~"^(city|town|village|hamlet|suburb|quarter|neighbourhood|locality|isolated_dwelling)$"]["name"];'],
    "waterways": ['way["waterway"~"^(river|stream)$"];'],
    "roads": ['way["highway"~"^(trunk|primary|secondary|tertiary|track)$"];'],
    "bridges": ['way["bridge"]["bridge"!="no"];', 'nwr["man_made"="bridge"];'],
    "hydro": ['nwr["power"="plant"]["plant:source"="hydro"];', 'nwr["power"="generator"]["generator:source"="hydro"];',
              'nwr["power"~"^(plant|generator)$"]["source"="hydro"];', 'nwr["power"="plant"]["name"~"hydro",i];',
              'nwr["waterway"~"^(dam|weir)$"];'],
}
# backtest sites need only the rivers (channel names and the stream network in hydro.py)
OSM_LAYERS = list(OSM_CLAUSES) if FULL else ["waterways"]


def osm_query(layers) -> str:
    body = "\n".join("  " + c for k in layers for c in OSM_CLAUSES[k])
    return "[out:json][timeout:240][bbox:{s},{w},{n},{e}];\n(\n" + body + "\n);\nout geom;"

# city/town/village/hamlet as specified, plus the finer types because several flood-path
# settlements (Timure, Langtang, Dhunche, Chilime) are mapped as place=suburb and
# Ghoda Tabela / Lama Hotel / Bamboo as isolated_dwelling. Filter on the "place" tag.
PLACE_TYPES = ("city", "town", "village", "hamlet", "suburb", "quarter", "neighbourhood",
               "locality", "isolated_dwelling")


def _osm_geom(el):
    from shapely.geometry import LineString, MultiLineString, Polygon, mapping
    from shapely.ops import polygonize, unary_union
    t = el["type"]
    if t == "node":
        return {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
    if t == "way":
        pts = [(p["lon"], p["lat"]) for p in el.get("geometry", []) if p]
        if len(pts) < 2:
            return None
        tags = el.get("tags", {})
        closed = len(pts) >= 4 and pts[0] == pts[-1]
        areaish = (tags.get("area") == "yes" or tags.get("power") in ("plant", "generator")
                   or tags.get("man_made") == "bridge" or "building" in tags)
        if closed and areaish and "highway" not in tags and tags.get("waterway") not in ("river", "stream"):
            return mapping(Polygon(pts))
        return mapping(LineString(pts))
    if t == "relation":
        lines = []
        for mem in el.get("members", []):
            g = mem.get("geometry")
            if mem.get("type") == "way" and g:
                pts = [(p["lon"], p["lat"]) for p in g if p]
                if len(pts) >= 2:
                    lines.append((mem.get("role", ""), LineString(pts)))
        if not lines:
            return None
        if el.get("tags", {}).get("type") == "multipolygon":
            outer = [ln for r, ln in lines if r in ("outer", "")]
            polys = list(polygonize(unary_union(outer)))
            if polys:
                return mapping(unary_union(polys))
        return mapping(MultiLineString([ln for _, ln in lines]))
    return None


def step_osm(m: Manifest, force: bool):
    d = OUT / "osm"
    d.mkdir(parents=True, exist_ok=True)
    names = OSM_LAYERS
    files = {k: d / f"osm_{k}.geojson" for k in names}
    if all(p.exists() for p in files.values()) and not force:
        for p in files.values():
            m.touch(p)
        print("osm: present, skipping")
        return
    w, s, e, n = CORRIDOR
    q = osm_query(names).format(s=s, w=w, n=n, e=e)
    data, used, err = None, None, ""
    for ep in OVERPASS:
        for attempt in range(3):
            try:
                r = requests.post(ep, data={"data": q}, headers=UA, timeout=300)
                if r.status_code in (429, 502, 503, 504):
                    err = f"{ep} HTTP {r.status_code}"
                    time.sleep(30 * (attempt + 1))
                    continue
                r.raise_for_status()
                data, used = r.json(), ep
                break
            except Exception as ex:
                err = f"{ep}: {ex!r}"
                time.sleep(15)
        if data:
            break
    if not data:
        for k in names:
            m.miss("osm", f"osm_{k}.geojson", f"Overpass {OVERPASS}", err)
        return
    retrieved = utcnow()
    osm_base = data.get("osm3s", {}).get("timestamp_osm_base", "")
    buckets = {k: [] for k in names}
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        cats = []
        if el["type"] == "node" and tags.get("place") in PLACE_TYPES and "name" in tags and "places" in buckets:
            cats.append("places")
        if el["type"] == "way" and tags.get("waterway") in ("river", "stream"):
            cats.append("waterways")
        if el["type"] == "way" and tags.get("highway") in ("trunk", "primary", "secondary", "tertiary", "track") and "roads" in buckets:
            cats.append("roads")
        if ((el["type"] == "way" and tags.get("bridge") not in (None, "no")) or tags.get("man_made") == "bridge") and "bridges" in buckets:
            cats.append("bridges")
        hydro = ((tags.get("power") == "plant" and tags.get("plant:source") == "hydro")
                 or (tags.get("power") == "generator" and tags.get("generator:source") == "hydro")
                 or (tags.get("power") in ("plant", "generator") and tags.get("source") == "hydro")
                 or (tags.get("power") == "plant" and "hydro" in tags.get("name", "").lower())
                 or tags.get("waterway") in ("dam", "weir"))
        if hydro and "hydro" in buckets:
            cats.append("hydro")
        if not cats:
            continue
        geom = _osm_geom(el)
        if geom is None:
            continue
        props = {"osm_type": el["type"], "osm_id": el["id"], **tags}
        feat = {"type": "Feature", "id": f"{el['type']}/{el['id']}", "geometry": geom, "properties": props}
        for c in cats:
            buckets[c].append(feat)
    lic = "(c) OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"
    desc = {
        "places": "Named OSM place nodes: city/town/village/hamlet plus suburb/quarter/neighbourhood/locality/isolated_dwelling (several flood-path villages, e.g. Timure, Dhunche, Langtang, are tagged suburb); filter on the place property",
        "waterways": "OSM waterway=river|stream ways (named and unnamed; name tag kept when present)",
        "roads": "OSM highway=trunk|primary|secondary|tertiary|track ways",
        "bridges": "OSM ways with bridge=* (not 'no'; includes footway/suspension bridges) and man_made=bridge features",
        "hydro": "OSM hydropower: power=plant|generator with plant:/generator:source=hydro or source=hydro, power=plant named *hydro*, and waterway=dam|weir",
    }
    for k in names:
        write_geojson(files[k], buckets[k], meta={"source": used, "osm_base": osm_base, "bbox_swne": [s, w, n, e]})
        m.add(files[k], retrieved_utc=retrieved, source_url=used, license=lic,
              version=f"OSM data as of {osm_base}",
              description=f"{desc[k]} in the corridor bbox; {len(buckets[k])} features; EPSG:4326; all OSM tags as properties.")
    if FULL:
        m.miss("osm", "building count per 500 m grid (optional)", "not attempted",
               "skipped deliberately to keep Overpass load low; WorldPop 100 m covers exposure")
    print("osm:", {k: len(v) for k, v in buckets.items()})


# ---------------------------------------------------------------- 5. ERA5 / Open-Meteo
WIN = ("07-01", "08-25")


def _om_fetch(path: Path, model: str, daily: list[str], elevation=None):
    params = dict(latitude=SOURCE_PT[0], longitude=SOURCE_PT[1], start_date="1991-01-01",
                  end_date="2026-09-30", daily=",".join(daily), models=model, timezone="Asia/Kathmandu")
    if elevation is not None:
        params["elevation"] = elevation
    url = requests.Request("GET", OPEN_METEO, params=params).prepare().url
    if path.exists():
        return json.loads(path.read_text()), url, None
    r = http_get(OPEN_METEO, params=params, timeout=300)
    j = r.json()
    if "daily" not in j:
        raise RuntimeError(f"Open-Meteo error: {str(j)[:300]}")
    path.write_text(json.dumps(j))
    return j, url, utcnow()


def _summarise(j: dict, vars_: list[str]):
    dl = j["daily"]
    t = np.array(dl["time"])
    def col(v):
        return np.array([np.nan if x is None else float(x) for x in dl[v]])
    cols = {v: col(v) for v in vars_}
    agg = {"temperature_2m_mean": "mean", "snowfall_sum": "sum", "precipitation_sum": "sum",
           "snow_depth_mean": "mean"}
    out_name = {"temperature_2m_mean": "mean_temp_c", "snowfall_sum": "total_snowfall_cm",
                "precipitation_sum": "total_precip_mm", "snow_depth_mean": "mean_snow_depth_m"}
    per_year = []
    for y in range(1991, 2027):
        mask = (t >= f"{y}-{WIN[0]}") & (t <= f"{y}-{WIN[1]}")
        rec = {"year": y, "n_days": int(mask.sum())}
        for v in vars_:
            x = cols[v][mask]
            nn = int(np.isnan(x).sum())
            if nn == len(x):
                val = None
            else:
                val = float(np.nanmean(x) if agg[v] == "mean" else np.nansum(x))
                val = round(val, 3)
            rec[out_name[v]] = val
            if nn:
                rec[out_name[v] + "_missing_days"] = nn
        per_year.append(rec)
    clim, y26, anom, pct = {}, {}, {}, {}
    for v in vars_:
        k = out_name[v]
        base = [r[k] for r in per_year if 1991 <= r["year"] <= 2020 and r[k] is not None]
        allv = [r[k] for r in per_year if r[k] is not None]
        v26 = next(r[k] for r in per_year if r["year"] == 2026)
        if not base or v26 is None:
            continue
        cm = float(np.mean(base))
        clim[k] = round(cm, 3)
        clim[k + "_sd"] = round(float(np.std(base, ddof=1)), 3)
        y26[k] = v26
        anom[k] = round(v26 - cm, 3)
        if cm != 0 and agg[v] == "sum":
            anom[k.replace("total_", "") + "_pct_of_normal"] = round(100 * v26 / cm, 1)
        if clim[k + "_sd"]:
            anom[k + "_z"] = round((v26 - cm) / clim[k + "_sd"], 2)
        n = len(allv)
        pct[k] = {"n_years": n,
                  "percent_of_years_le_2026": round(100 * sum(a <= v26 for a in allv) / n, 1),
                  "rank_highest_first": 1 + sum(a > v26 for a in allv),
                  "rank_lowest_first": 1 + sum(a < v26 for a in allv)}
    jun_sep = (t >= "2026-06-01") & (t <= "2026-09-30")
    daily = {"time": t[jun_sep].tolist()}
    for v in vars_:
        daily[v] = [None if np.isnan(x) else round(float(x), 3) for x in cols[v][jun_sep]]
    return dict(grid_cell={"latitude": j.get("latitude"), "longitude": j.get("longitude")},
                elevation_m_used=j.get("elevation"), units=j.get("daily_units"),
                climatology_1991_2020=clim, y2026=y26, anomaly_2026_minus_clim=anom,
                percentile_rank_2026_among_1991_2026=pct, per_year=per_year,
                daily_2026_06_01_to_09_30=daily)


def step_era5(m: Manifest, force: bool):
    if not FULL:
        m.miss("era5", "ERA5 season anomaly", "not attempted",
               "not needed for the backtest: the season factor is identical for every patch in a pass and cannot "
               "change ranks; rank.py sets it to 1")
        print("era5: skipped for a backtest site (season factor set to 1 in rank.py)")
        return
    d = OUT / "era5"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "era5_source_point.json"
    if f.exists() and not force:
        m.touch(f)
        for p in sorted(d.glob("openmeteo_raw_*.json")):
            m.touch(p)
        print("era5: present, skipping")
        return
    runs_cfg = [
        ("era5_elev5190", "era5", ["temperature_2m_mean", "snowfall_sum", "precipitation_sum", "snow_depth_mean"], SOURCE_ELEV),
        ("era5_default_elev", "era5", ["temperature_2m_mean", "snowfall_sum", "precipitation_sum", "snow_depth_mean"], None),
        # ERA5 snow depth is null over this (glacier-masked) cell; ERA5-Land carries snow depth.
        ("era5_land_elev5190", "era5_land", ["temperature_2m_mean", "snow_depth_mean"], SOURCE_ELEV),
        ("era5_land_default_elev", "era5_land", ["temperature_2m_mean", "snow_depth_mean"], None),
    ]
    lic = ("Open-Meteo Historical Weather API (CC BY 4.0), based on Copernicus Climate Change Service "
           "ERA5 / ERA5-Land reanalysis (Hersbach et al. 2020; Munoz Sabater 2019), contains modified "
           "Copernicus Climate Change Service information.")
    runs, urls = {}, {}
    for name, model, vars_, elev in runs_cfg:
        raw = d / f"openmeteo_raw_{name}.json"
        try:
            if force and raw.exists():
                raw.unlink()
            j, url, retrieved = _om_fetch(raw, model, vars_, elev)
            urls[name] = url
            if retrieved:
                m.add(raw, retrieved_utc=retrieved, source_url=url, license=lic,
                      version=f"Open-Meteo archive API, models={model}",
                      description=(f"Raw Open-Meteo daily response 1991-01-01..2026-09-30 at {SOURCE_PT}, "
                                   f"models={model}, elevation={'API default' if elev is None else elev}, "
                                   f"variables {vars_}, timezone Asia/Kathmandu."))
            else:
                m.touch(raw)
            # drop all-null variables (e.g. ERA5 snow depth) but say so
            dropped = [v for v in vars_ if all(x is None for x in j["daily"][v])]
            s = _summarise(j, [v for v in vars_ if v not in dropped])
            if dropped:
                s["variables_all_null_in_source"] = dropped
                m.miss("era5", f"{', '.join(dropped)} from models={model} ({name})", url,
                       "all values null at this grid cell in the source; snow depth taken from the "
                       "ERA5-Land runs instead" if model == "era5" else "all values null in source")
            s.update(model=model, elevation_param=("API default (90 m DEM at point)" if elev is None else elev),
                     request_url=url)
            runs[name] = s
            time.sleep(2)
        except Exception as e:
            traceback.print_exc()
            m.miss("era5", f"Open-Meteo run {name}", OPEN_METEO + f" models={model} elevation={elev}", repr(e))
    if not runs:
        return
    out = {
        "description": ("ERA5-based daily climate at the Langtang Lirung collapse source via the Open-Meteo "
                        "archive API. Window statistics are for 1 Jul - 25 Aug inclusive (56 days) of each year; "
                        "climatology = 1991-2020 mean (sd = sample sd); percentile = percent of years 1991-2026 "
                        "(2026 included) with a value <= the 2026 value; ranks are 1-based."),
        "point": {"lat": SOURCE_PT[0], "lon": SOURCE_PT[1], "elevation_m_source": SOURCE_ELEV},
        "window": {"start": WIN[0], "end": WIN[1], "days": 56},
        "timezone": "Asia/Kathmandu (daily aggregation in local days)",
        "primary_run": "era5_default_elev",
        "notes": [
            "Open-Meteo 'elevation' applies its own lapse-rate downscaling to temperature and recomputes "
            "snowfall from the downscaled temperature; precipitation is unchanged between elevation runs.",
            "Snowfall is in cm of snow (Open-Meteo converts ERA5 water-equivalent snowfall).",
            "ERA5 snow_depth is null at this grid cell; snow depth comes from the ERA5-Land runs.",
            "ERA5 (0.25 deg) and ERA5-Land (0.1 deg) grid cells differ; see grid_cell per run.",
        ],
        "runs": runs,
    }
    f.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    m.add(f, source_url=list(urls.values()), license=lic,
          version="ERA5 and ERA5-Land via Open-Meteo archive API",
          description=("Summary at the source point: daily series 2026-06-01..09-30, 1991-2020 climatology "
                       "for 1 Jul-25 Aug, 2026 values, anomalies, percentile rank, and per-year 1991-2026 "
                       "values; runs at elevation 5190 m and API default, ERA5 and ERA5-Land."))
    print("era5: done", list(runs))


# ---------------------------------------------------------------- 6. Meena zones
def step_meena(m: Manifest, force: bool):
    d = OUT / "meena"
    d.mkdir(parents=True, exist_ok=True)
    fcsv, fgj = d / "meena_zones.csv", d / "meena_zones.geojson"
    lic = ("Meena, S. R. (2026), NISAR offsets measure pre-failure motion but do not single out the "
           "Bhote Koshi avalanche source, Research Square preprint rs-11022365 v1, supplementary CSV. CC BY 4.0.")
    if fcsv.exists() and fgj.exists() and not force:
        m.touch(fcsv); m.touch(fgj)
        print("meena: present, skipping")
        return
    try:
        if not fcsv.exists() or force:
            r = http_get(MEENA_URL, timeout=60)
            fcsv.write_bytes(r.content)
            retrieved = utcnow()
            m.add(fcsv, retrieved_utc=retrieved, source_url=MEENA_URL, license=lic, version="v1 (preprint asset)",
                  description="Meena 2026 moving-zone table as published (one row per zone).")
        else:
            m.touch(fcsv)
            retrieved = utcnow()
        rows = list(csv.DictReader(io.StringIO(fcsv.read_text(encoding="utf-8-sig"))))
        cols = list(rows[0].keys()) if rows else []
        lon_k = next(c for c in cols if c.lower() in ("lon", "longitude", "x"))
        lat_k = next(c for c in cols if c.lower() in ("lat", "latitude", "y"))

        def conv(v):
            if v is None or v == "":
                return None
            if v in ("True", "False"):
                return v == "True"
            try:
                return int(v)
            except ValueError:
                try:
                    return float(v)
                except ValueError:
                    return v
        feats = [{"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [float(rw[lon_k]), float(rw[lat_k])]},
                  "properties": {k: conv(v) for k, v in rw.items()}} for rw in rows]
        write_geojson(fgj, feats, meta={"source": MEENA_URL, "columns": cols, "rows": len(rows)})
        m.add(fgj, retrieved_utc=retrieved, source_url=MEENA_URL, license=lic, version="v1 (preprint asset)",
              description=f"Meena zones as points (lon/lat columns), {len(rows)} features, all {len(cols)} CSV columns as properties.")
        print(f"meena: {len(rows)} rows, columns {cols}")
    except Exception as e:
        traceback.print_exc()
        m.miss("meena", "Meena 2026 zones CSV", MEENA_URL, repr(e))


# ---------------------------------------------------------------- main
STEPS = {"dem": step_dem, "glaciers": step_glaciers, "population": step_population,
         "osm": step_osm, "era5": step_era5, "meena": step_meena}
SITE_STEPS = ["dem", "glaciers", "population", "osm"]  # what a backtest site's ranking reads


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default=",".join(STEPS if FULL else SITE_STEPS), help="comma list of steps")
    ap.add_argument("--force", action="store_true", help="redo selected steps even if outputs exist")
    ap.add_argument("--keep-downloads", action="store_true",
                    help="keep national WorldPop files in raw/cache/worldpop/ for the next site")
    a = ap.parse_args()
    steps = [s.strip() for s in a.only.split(",") if s.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    man = Manifest()
    for s in steps:
        print(f"== {s}")
        man.step = s
        try:
            if s == "population":
                step_population(man, a.force, a.keep_downloads)
            else:
                STEPS[s](man, a.force)
        except Exception as e:
            traceback.print_exc()
            man.miss(s, f"step {s}", "see script", repr(e))
        man.save(steps[: steps.index(s) + 1])
    man.save(steps)
    print("manifest:", rel(MANIFEST))


if __name__ == "__main__":
    sys.exit(main())
