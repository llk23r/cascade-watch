"""Common study grid, site selection, file lookup and DEM helpers shared by every stage.

Site selection: the environment variable CW_SITE (default "langtang") picks the study box.
  langtang  grid, truth and event from pipeline/config.json; files in raw/ and data/ (unchanged).
  others    box, truth and event time from pipeline/backtest.json; files in raw/sites/<site>/ and
            data/sites/<site>/, so the Langtang files are never touched.
The screening rules (pipeline/config.json) are the same for every site.
"""
from __future__ import annotations

import glob
import json
import math
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "pipeline" / "config.json").read_text())
BACKTEST = json.loads((ROOT / "pipeline" / "backtest.json").read_text())
DEFAULT_SITE = "langtang"
HCELL = 90.0  # flow-routing cell (hydro.py)


def site_settings(site: str) -> dict:
    """Everything that differs between study boxes. Rules never differ."""
    if site == DEFAULT_SITE:
        t = CFG["truth"]
        return dict(
            site=site, role=BACKTEST["sites"][site]["role"], grid=CFG["grid"],
            raw=ROOT / "raw", data=ROOT / "data",
            truth=dict(kind="USGS event location us7000tbwb", lonlat=t["source_lonlat"], hit_radius_km=t["hit_radius_km"],
                       same_catchment_required=t["same_catchment_required"], outline_file="detachment.geojson"),
            event_utc=CFG["windows"]["event_utc"],
            last_two_ascending=[f"A098_{w}" for w in CFG["windows"]["pre_event_last_two_ascending"]],
            # The corridor fixed for the Langtang replay (lon 85.10-85.75, lat 27.85-28.45): it carries the
            # flood path down the Trishuli to Betrawati. UTM bounds are the ones every committed result used.
            corridor_lonlat=(85.10, 27.85, 85.75, 28.45),
            corridor_utm=(313200.0, 377100.0, 3081600.0, 3148200.0),
            # the source tributary ends where its D8 path meets one of these OSM rivers (rank.py)
            main_stem_names={"Bhote Koshi", "Trishuli River", "Trishuli Ganga River"},
            era5=True,
        )
    if site not in BACKTEST["sites"]:
        raise SystemExit(f"CW_SITE={site!r} is not a site in pipeline/backtest.json ({', '.join(BACKTEST['sites'])})")
    s = BACKTEST["sites"][site]
    g = s["grid"]
    t = s.get("truth")
    corridor_utm = _corridor_around_box(g)
    return dict(
        site=site, role=s["role"], grid=g,
        raw=ROOT / "raw" / "sites" / site, data=ROOT / "data" / "sites" / site,
        truth=(dict(kind=t["kind"], lonlat=t["lonlat"], hit_radius_km=t["hit_radius_km"],
                    same_catchment_required=t["same_catchment_required"], outline_file=None) if t else None),
        event_utc=s.get("event_utc"),
        last_two_ascending=s.get("last_two_ascending"),
        corridor_utm=corridor_utm,
        corridor_lonlat=_lonlat_bbox(corridor_utm, g["epsg"]),
        main_stem_names=None,  # no site-specific river names: the tributary ends at 500 km2 upstream area
        era5=False,  # season factor is identical for every patch in a pass, so it cannot change ranks
    )


def _corridor_around_box(g: dict) -> tuple:
    """Box plus room for the first people_path_km of every flow path (a path of length L stays within L of its
    start) and the people buffer, plus 2 km so routing edge effects stay outside; snapped outward to HCELL."""
    m = (CFG["blobs"]["people_path_km"] + CFG["blobs"]["people_buffer_km"] + 2.0) * 1000.0
    x0, x1 = g["x_edges"]
    y0, y1 = g["y_edges"]
    return (math.floor((x0 - m) / HCELL) * HCELL, math.ceil((x1 + m) / HCELL) * HCELL,
            math.floor((y0 - m) / HCELL) * HCELL, math.ceil((y1 + m) / HCELL) * HCELL)


def _lonlat_bbox(utm: tuple, epsg: int, pad_deg: float = 0.01) -> tuple:
    """W, S, E, N in degrees that contains the UTM rectangle (edges densified), padded and rounded outward."""
    x0, x1, y0, y1 = utm
    t = Transformer.from_crs(epsg, 4326, always_xy=True)
    xs = np.r_[np.linspace(x0, x1, 60), np.full(60, x1), np.linspace(x1, x0, 60), np.full(60, x0)]
    ys = np.r_[np.full(60, y0), np.linspace(y0, y1, 60), np.full(60, y1), np.linspace(y1, y0, 60)]
    lon, lat = t.transform(xs, ys)
    r = lambda v, f: f(v * 100) / 100  # noqa: E731
    return (r(lon.min() - pad_deg, math.floor), r(lat.min() - pad_deg, math.floor),
            r(lon.max() + pad_deg, math.ceil), r(lat.max() + pad_deg, math.ceil))


SITE = (os.environ.get("CW_SITE") or DEFAULT_SITE).strip().lower()
SITE_CFG = site_settings(SITE)
RAW = SITE_CFG["raw"]
DATA = SITE_CFG["data"]
RAW.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)
TRUTH = SITE_CFG["truth"]  # None for a negative control
EVENT_UTC = SITE_CFG["event_utc"]  # None for a negative control

EPSG = int(SITE_CFG["grid"]["epsg"])
CRS = f"EPSG:{EPSG}"
CELL = float(SITE_CFG["grid"]["cell_m"])
X_EDGE0, X_EDGE1 = map(float, SITE_CFG["grid"]["x_edges"])
Y_EDGE0, Y_EDGE1 = map(float, SITE_CFG["grid"]["y_edges"])
NX = int(round((X_EDGE1 - X_EDGE0) / CELL))  # 463
NY = int(round((Y_EDGE1 - Y_EDGE0) / CELL))  # 412
TRANSFORM = from_origin(X_EDGE0, Y_EDGE1, CELL, CELL)
XC = X_EDGE0 + CELL / 2 + CELL * np.arange(NX)
YC = Y_EDGE1 - CELL / 2 - CELL * np.arange(NY)
CORRIDOR_UTM = SITE_CFG["corridor_utm"]  # x0, x1, y0, y1 (flow routing, people below)
CORRIDOR_LONLAT = SITE_CFG["corridor_lonlat"]  # W, S, E, N (ancillary downloads)

TO_UTM = Transformer.from_crs(4326, EPSG, always_xy=True)
TO_LL = Transformer.from_crs(EPSG, 4326, always_xy=True)


def find(pattern: str) -> str | None:
    hits = sorted(glob.glob(str(RAW / "**" / pattern), recursive=True))
    if SITE == DEFAULT_SITE:  # raw/ holds the other sites and shared caches below it: never pick those up
        hits = [h for h in hits if not Path(h).is_relative_to(RAW / "sites") and not Path(h).is_relative_to(RAW / "cache")]
    return hits[0] if hits else None


def warp_to(src_path: str, transform, shape, crs=CRS, resampling=Resampling.bilinear, band=1, dtype="float32"):
    out = np.full(shape, np.nan, dtype=dtype)
    with rasterio.open(src_path) as src:
        reproject(
            source=rasterio.band(src, band),
            destination=out,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=crs,
            resampling=resampling,
            src_nodata=src.nodata,
            dst_nodata=np.nan,
        )
    return out


@lru_cache(maxsize=4)
def dem(cell: float = CELL) -> np.ndarray:
    src = find("dem_corridor_4326.tif")
    if src is None:
        raise FileNotFoundError(f"Copernicus DEM (dem_corridor_4326.tif) not found under {RAW}")
    nx = int(round((X_EDGE1 - X_EDGE0) / cell))
    ny = int(round((Y_EDGE1 - Y_EDGE0) / cell))
    return warp_to(src, from_origin(X_EDGE0, Y_EDGE1, cell, cell), (ny, nx))


def slope_fall_line(z: np.ndarray, cell: float = CELL, smooth: float = 1.0):
    """Slope (deg) and the 3-D unit vector pointing straight downhill (east, north, up)."""
    from scipy.ndimage import gaussian_filter

    zs = gaussian_filter(np.nan_to_num(z, nan=np.nanmean(z)), smooth) if smooth else z
    gy, gx = np.gradient(zs, cell)  # gy: per row (southward), gx: per column (eastward)
    dzde, dzdn = gx, -gy
    g = np.hypot(dzde, dzdn)
    slope = np.degrees(np.arctan(g))
    with np.errstate(invalid="ignore", divide="ignore"):
        he, hn = -dzde / g, -dzdn / g
    th = np.radians(slope)
    f = np.stack([np.cos(th) * he, np.cos(th) * hn, -np.sin(th)])
    return slope, f


def box_lonlat_polygon():
    xs = [X_EDGE0, X_EDGE1, X_EDGE1, X_EDGE0, X_EDGE0]
    ys = [Y_EDGE0, Y_EDGE0, Y_EDGE1, Y_EDGE1, Y_EDGE0]
    lon, lat = TO_LL.transform(xs, ys)
    return [[round(a, 6), round(b, 6)] for a, b in zip(lon, lat)]


def cell_of_lonlat(lon: float, lat: float):
    x, y = TO_UTM.transform(lon, lat)
    return int((Y_EDGE1 - y) // CELL), int((x - X_EDGE0) // CELL)
