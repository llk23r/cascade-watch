"""Drainage on the corridor DEM: depression filling, D8 flow, flow paths.

Used for (a) which channel a slope drains into, (b) people living along the
first 30 km below it, (c) the real flood path from the source to the Trishuli.
"""
from __future__ import annotations

import heapq
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from rasterio.transform import from_origin
from rasterio.warp import Resampling

from grid import CRS, TO_LL, TO_UTM, find, warp_to

HCELL = 90.0
# Corridor (lon 85.10–85.75, lat 27.85–28.45) in UTM 45N, snapped to 90 m.
HX0, HX1 = 313200.0, 377100.0
HY0, HY1 = 3081600.0, 3148200.0
HNX = int((HX1 - HX0) // HCELL)
HNY = int((HY1 - HY0) // HCELL)
HTRANSFORM = from_origin(HX0, HY1, HCELL, HCELL)
NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@lru_cache(maxsize=1)
def corridor_dem() -> np.ndarray:
    return warp_to(find("dem_corridor_4326.tif"), HTRANSFORM, (HNY, HNX))


@lru_cache(maxsize=1)
def flow():
    z = corridor_dem()
    z = np.where(np.isfinite(z), z, np.nanmin(z))
    ny, nx = z.shape
    filled = z.astype(np.float64).copy()
    closed = np.zeros((ny, nx), bool)
    heap: list = []
    for r in range(ny):
        for c in (0, nx - 1):
            heap.append((filled[r, c], r, c)); closed[r, c] = True
    for c in range(1, nx - 1):
        for r in (0, ny - 1):
            heap.append((filled[r, c], r, c)); closed[r, c] = True
    heapq.heapify(heap)
    eps = 1e-4
    while heap:
        e, r, c = heapq.heappop(heap)
        for dr, dc in NB:
            rr, cc = r + dr, c + dc
            if 0 <= rr < ny and 0 <= cc < nx and not closed[rr, cc]:
                closed[rr, cc] = True
                if filled[rr, cc] <= e:
                    filled[rr, cc] = e + eps
                heapq.heappush(heap, (filled[rr, cc], rr, cc))
    # D8 steepest descent on the filled surface
    best = np.full((ny, nx), -1, np.int64)
    bestdrop = np.zeros((ny, nx))
    pad = np.pad(filled, 1, constant_values=np.inf)
    idx = np.arange(ny * nx).reshape(ny, nx)
    for k, (dr, dc) in enumerate(NB):
        nbz = pad[1 + dr : 1 + dr + ny, 1 + dc : 1 + dc + nx]
        drop = (filled - nbz) / (HCELL * (1.4142 if dr and dc else 1.0))
        better = drop > bestdrop
        bestdrop = np.where(better, drop, bestdrop)
        best = np.where(better, k, best)
    to = np.full(ny * nx, -1, np.int64)
    ok = best >= 0
    drs = np.array([d[0] for d in NB]); dcs = np.array([d[1] for d in NB])
    rr = (idx // nx + np.where(ok, drs[best], 0))
    cc = (idx % nx + np.where(ok, dcs[best], 0))
    to[ok.ravel()] = (rr * nx + cc)[ok].ravel()
    # accumulation in descending filled order
    order = np.argsort(-filled.ravel(), kind="stable")
    acc = np.ones(ny * nx)
    for i in order:
        j = to[i]
        if j >= 0:
            acc[j] += acc[i]
    return filled, to, acc.reshape(ny, nx)


def rc_of_utm(x: float, y: float):
    return int((HY1 - y) // HCELL), int((x - HX0) // HCELL)


def utm_of_rc(r, c):
    return HX0 + (np.asarray(c) + 0.5) * HCELL, HY1 - (np.asarray(r) + 0.5) * HCELL


def path_from(x: float, y: float, max_km: float = 200.0, snap_acc_km2: float | None = None):
    """Cells (row, col) followed downstream from a UTM point until the corridor edge."""
    _, to, acc = flow()
    r, c = rc_of_utm(x, y)
    if snap_acc_km2:
        # move to the highest-accumulation cell within 300 m (start on the channel)
        k = int(300 // HCELL)
        win = acc[max(r - k, 0) : r + k + 1, max(c - k, 0) : c + k + 1]
        dr, dc = np.unravel_index(np.argmax(win), win.shape)
        r, c = max(r - k, 0) + dr, max(c - k, 0) + dc
    i = r * HNX + c
    cells = [i]
    dist = 0.0
    while to[i] >= 0 and dist < max_km * 1000:
        j = to[i]
        dist += HCELL * (1.4142 if (j // HNX != i // HNX and j % HNX != i % HNX) else 1.0)
        cells.append(j)
        i = j
    rows, cols = np.array(cells) // HNX, np.array(cells) % HNX
    xs, ys = utm_of_rc(rows, cols)
    seg = np.hypot(np.diff(xs), np.diff(ys))
    along = np.concatenate([[0], np.cumsum(seg)])
    return rows, cols, xs, ys, along


@lru_cache(maxsize=1)
def population_grid() -> np.ndarray | None:
    p = find("population_corridor.tif")
    if p is None:
        return None
    return warp_to(p, HTRANSFORM, (HNY, HNX), resampling=Resampling.sum)


def people_along(rows, cols, along, path_km: float, buffer_km: float) -> float | None:
    pop = population_grid()
    if pop is None:
        return None
    from scipy import ndimage

    m = np.zeros((HNY, HNX), bool)
    sel = along <= path_km * 1000
    m[rows[sel], cols[sel]] = True
    d = ndimage.distance_transform_edt(~m) * HCELL
    return float(np.nansum(np.where(d <= buffer_km * 1000, pop, 0)))


def english_name(p: dict) -> str | None:
    nm = p.get("name:en") or p.get("alt_name:en")
    if nm:
        return nm
    nm = p.get("name")
    return nm if nm and nm.isascii() else None


@lru_cache(maxsize=1)
def named_waterways():
    """(english name, waterway kind, UTM geometry) for named OSM waterways, plus an STRtree."""
    p = find("osm_waterways.geojson")
    if p is None:
        return [], None
    from shapely.geometry import shape
    from shapely.ops import transform as stransform
    from shapely.strtree import STRtree

    out = []
    for ft in json.loads(Path(p).read_text())["features"]:
        props = ft.get("properties") or {}
        nm = english_name(props)
        if not nm:
            continue
        try:
            g = stransform(lambda x, y, z=None: TO_UTM.transform(x, y), shape(ft["geometry"]))
        except Exception:
            continue
        out.append((nm, props.get("waterway", ""), g))
    return out, STRtree([g for _, _, g in out]) if out else None


def first_named_river(xs, ys, along, within_m: float = 150.0, names: set | None = None):
    """First named waterway the path comes within `within_m` of (optionally restricted to `names`)."""
    from shapely.geometry import Point

    ww, tree = named_waterways()
    if tree is None:
        return None, None
    for x, y, a in zip(xs, ys, along):
        p = Point(x, y)
        for i in tree.query(p.buffer(within_m)):
            nm, kind, g = ww[int(i)]
            if names and nm not in names:
                continue
            if g.distance(p) <= within_m:
                return nm, float(a)
    return None, None


def path_lonlat(xs, ys, step: int = 1):
    lon, lat = TO_LL.transform(xs[::step], ys[::step])
    return [[round(float(a), 5), round(float(b), 5)] for a, b in zip(lon, lat)]
