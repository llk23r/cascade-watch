"""Stage 5a — Render map assets for the page (all from real rasters).

  data/terrain/{z}/{x}/{y}.png   Terrarium-encoded Copernicus DEM tiles (3-D terrain)
  data/img/relief.jpg            shaded relief of the corridor (Web Mercator)
  data/img/motion_<win>.png      NISAR fall-line motion (GOFF), transparent PNG
  data/img/phase_<win>.png       NISAR relative LOS motion (GUNW), transparent PNG
  data/img/coh_<win>.png         NISAR interferometric coherence
  data/img/gcov_<date>.jpg       NISAR GCOV gamma0 HH/HV composite (10 m)
  data/img/bounds.json           corner coordinates for every image
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds

from grid import CRS, DATA, RAW, TRANSFORM, X_EDGE0, X_EDGE1, Y_EDGE0, Y_EDGE1, find

IMG = DATA / "img"
IMG.mkdir(parents=True, exist_ok=True)
TER = DATA / "terrain"
LON0, LON1, LAT0, LAT1 = 85.10, 85.75, 27.85, 28.45
R = 6378137.0


def merc(lon, lat):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def tile_bounds(z, x, y):
    n = 2**z
    lon0 = x / n * 360 - 180
    lon1 = (x + 1) / n * 360 - 180
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon0, lat0, lon1, lat1


def tiles_for(z):
    n = 2**z
    x0 = int((LON0 + 180) / 360 * n)
    x1 = int((LON1 + 180) / 360 * n)
    def ty(lat):
        return int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    y0, y1 = ty(LAT1), ty(LAT0)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def terrain_tiles(zmin=8, zmax=13):
    src = rasterio.open(find("dem_corridor_4326.tif"))
    dem = src.read(1).astype(np.float32)
    count = 0
    for z in range(zmin, zmax + 1):
        for x, y in tiles_for(z):
            out = TER / str(z) / str(x) / f"{y}.png"
            if out.exists():
                continue
            lo0, la0, lo1, la1 = tile_bounds(z, x, y)
            mx0, my0 = merc(lo0, la0)
            mx1, my1 = merc(lo1, la1)
            dst = np.full((256, 256), np.nan, np.float32)
            reproject(dem, dst, src_transform=src.transform, src_crs=src.crs, dst_transform=from_bounds(mx0, my0, mx1, my1, 256, 256),
                      dst_crs="EPSG:3857", resampling=Resampling.bilinear, dst_nodata=np.nan)
            h = np.nan_to_num(dst, nan=0.0) + 32768.0
            r = np.floor(h / 256)
            g = np.floor(h - r * 256)
            b = np.floor((h - np.floor(h)) * 256)
            out.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.stack([r, g, b], -1).astype(np.uint8)).save(out, optimize=True)
            count += 1
    return count


def to_merc_image(arr, src_transform, src_crs, bounds_ll, width, resampling=Resampling.bilinear):
    mx0, my0 = merc(bounds_ll[0], bounds_ll[1])
    mx1, my1 = merc(bounds_ll[2], bounds_ll[3])
    height = int(round(width * (my1 - my0) / (mx1 - mx0)))
    dst = np.full((height, width), np.nan, np.float32)
    reproject(arr.astype(np.float32), dst, src_transform=src_transform, src_crs=src_crs,
              dst_transform=from_bounds(mx0, my0, mx1, my1, width, height), dst_crs="EPSG:3857",
              resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
    return dst


def corners(b):
    lo0, la0, lo1, la1 = b
    return [[lo0, la1], [lo1, la1], [lo1, la0], [lo0, la0]]


def lerp_colors(t, stops):
    t = np.clip(t, 0, 1)
    xs = np.array([s[0] for s in stops])
    out = np.zeros(t.shape + (3,))
    for ch in range(3):
        out[..., ch] = np.interp(t, xs, [s[1][ch] for s in stops])
    return out


def relief():
    src = rasterio.open(find("dem_corridor_4326.tif"))
    dem = src.read(1).astype(np.float32)
    b = (LON0, LAT0, LON1, LAT1)
    z = to_merc_image(dem, src.transform, src.crs, b, 2600)
    cell = (merc(LON1, LAT0)[0] - merc(LON0, LAT0)[0]) / z.shape[1] * math.cos(math.radians(28.15))
    gy, gx = np.gradient(np.nan_to_num(z, nan=np.nanmin(z)), cell)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    shade = np.zeros_like(z)
    for az, w in ((315, 0.55), (270, 0.2), (0, 0.15), (225, 0.1)):
        a, alt = math.radians(az), math.radians(42)
        shade += w * (math.sin(alt) * np.cos(slope) + math.cos(alt) * np.sin(slope) * np.cos(a - aspect))
    shade = np.clip(shade, 0, 1)
    t = (z - 400) / (7200 - 400)
    base = lerp_colors(t, [(0.0, (22, 34, 48)), (0.18, (30, 48, 60)), (0.42, (48, 62, 78)), (0.62, (84, 98, 118)), (0.78, (150, 166, 186)), (1.0, (226, 234, 242))])
    k = (0.38 + 0.9 * shade)[..., None]
    rgb = np.clip(base * k, 0, 255).astype(np.uint8)
    Image.fromarray(rgb).save(IMG / "relief.jpg", quality=86, optimize=True, progressive=True)
    return {"relief": corners(b)}


def box_bounds_ll():
    return transform_bounds(CRS, "EPSG:4326", X_EDGE0, Y_EDGE0, X_EDGE1, Y_EDGE1)


def rgba_png(rgb, alpha, path):
    a = np.clip(alpha, 0, 255).astype(np.uint8)
    Image.fromarray(np.dstack([np.clip(rgb, 0, 255).astype(np.uint8), a])).save(path, optimize=True)


def window_rasters(width=1400):
    out = {}
    b = box_bounds_ll()
    for p in sorted((RAW / "products").glob("*.npz")):
        name = p.stem
        P = np.load(p)
        if "s" in P:
            s = to_merc_image(P["s"], TRANSFORM, CRS, b, width, Resampling.nearest)
            t = np.clip(s / 2.0, 0, 1) ** 0.6  # 0 .. 2 m downhill per window
            rgb = lerp_colors(t, [(0, (53, 208, 230)), (0.45, (199, 107, 242)), (0.8, (243, 207, 85)), (1, (255, 250, 230))])
            alpha = np.where(np.isfinite(s) & (s > 0.15), 60 + 195 * np.clip(s / 1.0, 0, 1), 0)
            rgba_png(rgb, alpha, IMG / f"motion_{name}.png")
        rel = to_merc_image(P["los_rel_mm"], TRANSFORM, CRS, b, width, Resampling.nearest)
        t = np.clip((rel + 60) / 120, 0, 1)
        rgb = lerp_colors(t, [(0, (64, 132, 255)), (0.5, (235, 240, 245)), (1, (255, 97, 82))])
        alpha = np.where(np.isfinite(rel), 40 + 200 * np.clip(np.abs(rel) / 60, 0, 1), 0)
        rgba_png(rgb, alpha, IMG / f"phase_{name}.png")
        coh = to_merc_image(P["coh"], TRANSFORM, CRS, b, width, Resampling.nearest)
        g = np.nan_to_num(coh) * 255
        rgba_png(np.dstack([g * 0.9, g * 0.95, g]), np.where(np.isfinite(coh), 230, 0), IMG / f"coh_{name}.png")
        out[name] = corners(b)
    return out


def gcov_images(width=2400, pairs=(("A098_20260819", "A098_20260831"), ("D048_20260816", "D048_20260828"))):
    """Greyscale gamma0 HH per date, plus a log-ratio change map for each before/after pair."""
    from scipy import ndimage

    out, hh_by = {}, {}
    b = box_bounds_ll()
    for p in sorted((RAW / "nisar").glob("GCOV_*.npz")):
        G = np.load(p)
        xs, ys = G["x"], G["y"]
        tr = from_bounds(xs[0] - 5, ys[-1] - 5, xs[-1] + 5, ys[0] + 5, xs.size, ys.size)
        hh = to_merc_image(G["HHHH_db"].astype(np.float32), tr, CRS, b, width)
        hh_by[p.stem[5:]] = hh
        t = np.clip((hh + 21) / 23, 0, 1) ** 1.6
        rgb = np.dstack([18 + t * 214, 26 + t * 214, 40 + t * 210])
        rgb = np.where(np.isfinite(hh)[..., None], rgb, 8)
        Image.fromarray(rgb.astype(np.uint8)).save(IMG / f"gcov_{p.stem[5:]}.jpg", quality=84, optimize=True, progressive=True)
        out["gcov_" + p.stem[5:]] = corners(b)
    for a, c in pairs:
        if a in hh_by and c in hh_by:
            d = np.nan_to_num(hh_by[c] - hh_by[a], nan=0.0)
            d = ndimage.median_filter(d, size=5)
            d = ndimage.gaussian_filter(d, 1.2)
            mag = np.clip((np.abs(d) - 2.5) / 5.0, 0, 1)
            rgb = np.where((d > 0)[..., None], np.array([255, 120, 90]), np.array([90, 160, 255]))
            rgba_png(rgb * np.ones_like(d)[..., None], 235 * mag, IMG / f"gcovchange_{a}_{c[-8:]}.png")
            out[f"gcovchange_{a}_{c[-8:]}"] = corners(b)
    return out


def main(parts):
    bpath = IMG / "bounds.json"
    bounds = json.loads(bpath.read_text()) if bpath.exists() else {}
    if "terrain" in parts:
        print("terrain tiles", terrain_tiles())
    if "relief" in parts:
        bounds.update(relief())
    if "windows" in parts:
        bounds.update(window_rasters())
    if "gcov" in parts:
        bounds.update(gcov_images())
    bounds["box"] = corners(box_bounds_ll())
    bpath.write_text(json.dumps(bounds))


if __name__ == "__main__":
    main(sys.argv[1:] or ["terrain", "relief", "windows", "gcov"])
