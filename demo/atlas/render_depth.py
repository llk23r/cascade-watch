"""Render a planar-depth map of the real terrain (Copernicus DEM, Terrarium tiles in data/terrain) for one camera,
in the format World Labs Atlas `atlasChisel` expects, plus a shaded preview and the screen positions of real
features (flood path, detachment outline, places) so they can be drawn over the AI-painted view.

The geometry is real; only the appearance Atlas paints on top of it is generated.
Usage: python demo/atlas/render_depth.py [shot_name]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
W, H = 1280, 720

SHOTS = {
    # high over the Bhote Koshi near Syabrubesi, looking north-east up the flood route to the detachment
    "valley": dict(cam=(85.300, 28.120, 7200.0), look=(85.5213, 28.2891), look_up_m=0.0, hfov=60.0, zoom=13),
    # from the north-west, looking down the massif onto the source and the route the flood took
    "massif": dict(cam=(85.395, 28.375, 7800.0), look=(85.545, 28.262), look_up_m=0.0, hfov=60.0, zoom=13),
    # close: over the flood valley west of the source, looking up-valley at the detachment
    "close_w": dict(cam=(85.470, 28.268, 5600.0), look=(85.5213, 28.2891), look_up_m=-150.0, hfov=58.0, zoom=13),
    # close: north-west of the source, higher
    "close_nw": dict(cam=(85.484, 28.326, 6900.0), look=(85.5213, 28.2891), look_up_m=700.0, hfov=64.0, zoom=13),
}
ORIGIN = (85.425, 28.15)   # world origin (lon, lat) for all shots
STEP_M = 25.0


def tiles(z):
    d = ROOT / "data" / "terrain" / str(z)
    xs = sorted(int(p.name) for p in d.iterdir() if p.is_dir())
    ys = sorted({int(f.stem) for x in xs for f in (d / str(x)).glob("*.png")})
    return d, xs, ys


def load_dem(z):
    d, xs, ys = tiles(z)
    A = np.full((len(ys) * 256, len(xs) * 256), np.nan, np.float32)
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            f = d / str(x) / f"{y}.png"
            if not f.exists():
                continue
            rgb = np.asarray(Image.open(f).convert("RGB"), np.float32)
            A[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256] = rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768
    n = 2 ** z
    px = (xs[0] * 256 + np.arange(A.shape[1]) + 0.5) / (256 * n)
    py = (ys[0] * 256 + np.arange(A.shape[0]) + 0.5) / (256 * n)
    lon = px * 360 - 180
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * py))))
    return A, lon, lat


def enu(lon, lat, lon0, lat0):
    return (np.asarray(lon) - lon0) * math.cos(math.radians(lat0)) * 111320.0, (np.asarray(lat) - lat0) * 110540.0


def quat_from_matrix(R):
    t = np.trace(R)
    if t > 0:
        s = math.sqrt(t + 1) * 2
        w, x, y, z = 0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w, x, y, z = (R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w, x, y, z = (R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w, x, y, z = (R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s
    return [float(x), float(y), float(z), float(w)]


def metric_grid(z):
    """Resample the Terrarium (Web Mercator) elevation to a regular metric grid around ORIGIN."""
    from scipy.interpolate import RegularGridInterpolator
    A, lon, lat = load_dem(z)
    e0, n0 = enu(lon[0], lat[-1], *ORIGIN); e1, n1 = enu(lon[-1], lat[0], *ORIGIN)
    xs = np.arange(math.ceil(e0 / STEP_M) * STEP_M, e1, STEP_M)
    ns = np.arange(math.ceil(n0 / STEP_M) * STEP_M, n1, STEP_M)
    lo = ORIGIN[0] + xs / (math.cos(math.radians(ORIGIN[1])) * 111320.0)
    la = ORIGIN[1] + ns / 110540.0
    f = RegularGridInterpolator((lat[::-1], lon), np.nan_to_num(A[::-1], nan=0.0), bounds_error=False, fill_value=0.0)
    LA, LO = np.meshgrid(la, lo, indexing="ij")
    G = f(np.stack([LA.ravel(), LO.ravel()], 1)).reshape(LA.shape).astype(np.float64)   # G[north_index, east_index]
    from scipy.ndimage import gaussian_filter
    G = gaussian_filter(G, 1.2)   # soften tile-quantisation steps; well below the DEM's own resolution
    return G, float(xs[0]), float(ns[0])


def raymarch(G, e0, n0, C, R, fx, fy, cx, cy):
    from numba import njit, prange

    @njit(parallel=True, fastmath=True)
    def run(G, e0, n0, step, C, R, fx, fy, cx, cy, W, H):
        D = np.zeros((H, W))
        ny, nx = G.shape
        for v in prange(H):
            for u in range(W):
                dc0 = (u + 0.5 - cx) / fx; dc1 = -(v + 0.5 - cy) / fy; dc2 = -1.0
                dx = R[0, 0] * dc0 + R[0, 1] * dc1 + R[0, 2] * dc2
                dy = R[1, 0] * dc0 + R[1, 1] * dc1 + R[1, 2] * dc2
                dz = R[2, 0] * dc0 + R[2, 1] * dc1 + R[2, 2] * dc2
                t = 30.0; prev_t = t; prev_above = 1.0; hit = -1.0
                while t < 90000.0:
                    x = C[0] + dx * t; y = C[1] + dy * t; z = C[2] + dz * t
                    gi = (-z - n0) / step; gj = (x - e0) / step
                    if gi < 0 or gj < 0 or gi >= ny - 1 or gj >= nx - 1:
                        if y < 0: break
                        t += max(25.0, t * 0.004); continue
                    i0 = int(gi); j0 = int(gj); fi = gi - i0; fj = gj - j0
                    h = (G[i0, j0] * (1 - fi) * (1 - fj) + G[i0 + 1, j0] * fi * (1 - fj) + G[i0, j0 + 1] * (1 - fi) * fj + G[i0 + 1, j0 + 1] * fi * fj)
                    above = y - h
                    if above <= 0:
                        lo_t = prev_t; hi_t = t           # refine the crossing by bisection
                        for _ in range(12):
                            mid = 0.5 * (lo_t + hi_t)
                            x = C[0] + dx * mid; y = C[1] + dy * mid; z = C[2] + dz * mid
                            gi = (-z - n0) / step; gj = (x - e0) / step
                            i0 = int(gi); j0 = int(gj); fi = gi - i0; fj = gj - j0
                            h = (G[i0, j0] * (1 - fi) * (1 - fj) + G[i0 + 1, j0] * fi * (1 - fj) + G[i0, j0 + 1] * (1 - fi) * fj + G[i0 + 1, j0 + 1] * fi * fj)
                            if y - h > 0: lo_t = mid
                            else: hi_t = mid
                        hit = hi_t; break
                    prev_t = t; prev_above = above
                    t += max(6.0, min(above * 0.5, t * 0.003 + 8.0))
                if hit > 0:
                    D[v, u] = hit   # ray parameter along the un-normalised direction (camera z = -1): planar depth
        return D

    return run(G, e0, n0, STEP_M, C, R, fx, fy, cx, cy, W, H)


def main(name="valley"):
    sh = SHOTS[name]
    G, e0, n0 = metric_grid(sh["zoom"])

    def elev_at(lo, la):
        e, n = enu(lo, la, *ORIGIN)
        i = int(round((float(n) - n0) / STEP_M)); j = int(round((float(e) - e0) / STEP_M))
        return float(G[min(max(i, 0), G.shape[0] - 1), min(max(j, 0), G.shape[1] - 1)])

    ce, cn = enu(sh["cam"][0], sh["cam"][1], *ORIGIN)
    C = np.array([float(ce), sh["cam"][2], -float(cn)])
    tl = sh["look"]
    te, tn = enu(tl[0], tl[1], *ORIGIN)
    T = np.array([float(te), elev_at(tl[0], tl[1]) + sh["look_up_m"], -float(tn)])
    f = T - C; f /= np.linalg.norm(f)
    r = np.cross(f, [0, 1, 0]); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    R = np.stack([r, u, -f], 1)                          # camera-to-world, columns = camera x, y, z (RUB)
    fx = W / (2 * math.tan(math.radians(sh["hfov"]) / 2)); fy = fx; cx, cy = W / 2, H / 2
    D = raymarch(G, e0, n0, C, R, fx, fy, cx, cy)
    sky = D <= 0
    zmin, zmax = float(D[~sky].min()), float(D[~sky].max())
    p = np.zeros((H, W), np.float64)
    p[~sky] = np.clip(np.round(65535 * (1 - np.log(D[~sky] / zmin) / np.log(zmax / zmin))), 1, 65535)
    Image.fromarray(p.astype(np.uint16)).save(OUT / f"{name}_depth16.png")
    # shaded preview from world normals (for checking the framing only; never sent to Atlas)
    Dz = np.where(sky, np.nan, D)
    vv, uu = np.mgrid[0:H, 0:W]
    Xc = (uu + 0.5 - cx) / fx * Dz; Yc = -(vv + 0.5 - cy) / fy * Dz; Zc = -Dz
    Pw = np.stack([Xc, Yc, Zc], -1) @ R.T + C
    dxu = np.gradient(Pw, axis=1); dxv = np.gradient(Pw, axis=0)
    nrm = np.cross(dxv, dxu); nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True) + 1e-9
    light = np.array([-0.5, 0.75, 0.45]); light /= np.linalg.norm(light)
    lam = np.clip(np.nan_to_num((nrm * light).sum(-1) * np.sign(nrm[..., 1:2].squeeze(-1) + 1e-9)), 0, 1)
    fog = np.clip(np.nan_to_num((Dz - zmin) / (zmax - zmin)), 0, 1) ** 0.7
    base = (0.25 + 0.75 * lam)[..., None] * np.array([238, 236, 232]) * (1 - 0.55 * fog[..., None]) + fog[..., None] * np.array([120, 138, 165]) * 0.55
    img = np.where(sky[..., None], np.array([168, 188, 214]), base)
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(OUT / f"{name}_preview.png")

    def project(lonlat, lift=15.0):
        out = []
        for lo, la in lonlat:
            e, n = enu(lo, la, *ORIGIN)
            Pw_ = np.array([float(e), elev_at(lo, la) + lift, -float(n)])
            v = (Pw_ - C) @ R
            zc = -v[2]
            if zc <= 50:
                out.append(None); continue
            su, sv = cx + fx * v[0] / zc, cy - fy * v[1] / zc
            iu, iv = int(round(su)), int(round(sv))
            vis = 0 <= iu < W and 0 <= iv < H and (sky[iv, iu] or zc <= D[iv, iu] * 1.02 + 80)
            out.append([round(su, 1), round(sv, 1), round(float(zc)), bool(vis)])
        return out

    replay = json.loads((ROOT / "data" / "replay.json").read_text())
    truth = json.loads((ROOT / "data" / "truth.geojson").read_text())
    outline = next(f for f in truth["features"] if f["properties"].get("role") != "hit_zone")["geometry"]["coordinates"][0]
    meta = dict(
        shot=name, source="Copernicus DEM GLO-30 via Terrarium tiles in data/terrain, zoom %d" % sh["zoom"],
        camera=dict(extrinsics=dict(position=C.tolist(), quaternion=quat_from_matrix(R), coordinateSystem="rub"),
                    intrinsics=dict(width=W, height=H, fx=fx, fy=fy, cx=cx, cy=cy)),
        camera_lonlat_alt=list(sh["cam"]), look_at_lonlat=[tl[0], tl[1]], zMin=zmin, zMax=zmax, sky_share=float(sky.mean()),
        overlays=dict(
            flood_path=project(replay["path"]),
            path_km=replay["path_km"],
            detachment=project(outline, 25.0),
            places=[dict(name=v["name"], xy=project([[v["lon"], v["lat"]]])[0]) for v in replay["villages"]],
            source=project([[85.5213, 28.2891]], 40.0)[0],
        ),
    )
    (OUT / f"{name}_meta.json").write_text(json.dumps(meta, indent=1))
    print(name, "zMin %.0f zMax %.0f sky %.0f%%" % (zmin, zmax, 100 * sky.mean()), "visible path pts", sum(1 for q in meta["overlays"]["flood_path"] if q and q[3]))


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["valley"]))
