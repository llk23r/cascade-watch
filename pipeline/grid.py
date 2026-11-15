"""Common study grid, file lookup and DEM helpers shared by every stage."""
from __future__ import annotations

import glob
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
CFG = json.loads((ROOT / "pipeline" / "config.json").read_text())

CRS = "EPSG:32645"
CELL = 80.0
X_EDGE0, X_EDGE1 = 336400.0, 373440.0
Y_EDGE0, Y_EDGE1 = 3113680.0, 3146640.0
NX = int(round((X_EDGE1 - X_EDGE0) / CELL))  # 463
NY = int(round((Y_EDGE1 - Y_EDGE0) / CELL))  # 412
TRANSFORM = from_origin(X_EDGE0, Y_EDGE1, CELL, CELL)
XC = X_EDGE0 + CELL / 2 + CELL * np.arange(NX)
YC = Y_EDGE1 - CELL / 2 - CELL * np.arange(NY)

TO_UTM = Transformer.from_crs(4326, 32645, always_xy=True)
TO_LL = Transformer.from_crs(32645, 4326, always_xy=True)


def find(pattern: str) -> str | None:
    hits = sorted(glob.glob(str(RAW / "**" / pattern), recursive=True))
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
        raise FileNotFoundError("Copernicus DEM (dem_corridor_4326.tif) not found under raw/")
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
