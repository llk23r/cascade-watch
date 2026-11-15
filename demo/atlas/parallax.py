"""A slow camera move through the Atlas-painted view, with true parallax from the real DEM depth.

The Atlas image was generated at a camera whose depth we rendered from the Copernicus DEM, so every pixel has a
known 3D position. Re-projecting those points to nearby cameras gives a physically consistent dolly. The real
UNOSAT outline and D8 flood route are projected into every frame with the same camera, so they stay locked to
the terrain. No Atlas credits are used here.

Usage: python demo/atlas/parallax.py  ->  demo/film/atlas/{before,after}_NNN.jpg and overlays.json
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

import render_depth as rd

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[0] / "film" / "atlas"
OUT.mkdir(parents=True, exist_ok=True)
FPS, SECONDS = 30, 5.0
W, H = rd.W, rd.H
UP_W, UP_H = 1920, 1080


def decode_depth(png, zmin, zmax):
    p = np.asarray(Image.open(png)).astype(np.float64)
    z = zmin * (zmax / zmin) ** (1 - p / 65535.0)
    z[p == 0] = np.inf
    return z


def basis(C, T):
    f = T - C; f /= np.linalg.norm(f)
    r = np.cross(f, [0, 1, 0]); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return np.stack([r, u, -f], 1)


def main():
    meta = json.loads((HERE / "out" / "close_nw_meta.json").read_text())
    K = meta["camera"]["intrinsics"]; fx, fy, cx, cy = K["fx"], K["fy"], K["cx"], K["cy"]
    C0 = np.array(meta["camera"]["extrinsics"]["position"])
    z = decode_depth(HERE / "out" / "close_nw_depth16.png", meta["zMin"], meta["zMax"])
    # sky comes from the painted image (flood-filled from the top edge), so silhouettes match what is visible;
    # painted terrain where the DEM ray found nothing gets the far depth
    ref = np.asarray(Image.open(HERE / "out" / "close_before_clean.png").convert("RGB")).astype(float)
    top = np.median(ref[:6].reshape(-1, 3), 0)
    skylike = (np.linalg.norm(ref - top, axis=-1) < 38) & (ref.mean(-1) > 150)
    lab, _ = ndimage.label(skylike)
    sky = np.isin(lab, np.unique(lab[0][lab[0] > 0]))
    sky = ndimage.binary_dilation(sky, iterations=2) & skylike | sky
    z = np.where(~sky & ~np.isfinite(z), meta["zMax"] * 1.3, z)
    sh = rd.SHOTS["close_nw"]
    G, e0, n0 = rd.metric_grid(sh["zoom"])

    def elev_at(lo, la):
        e, n = rd.enu(lo, la, *rd.ORIGIN)
        i = int(round((float(n) - n0) / rd.STEP_M)); j = int(round((float(e) - e0) / rd.STEP_M))
        return float(G[min(max(i, 0), G.shape[0] - 1), min(max(j, 0), G.shape[1] - 1)])

    tl = sh["look"]; te, tn = rd.enu(tl[0], tl[1], *rd.ORIGIN)
    T = np.array([float(te), elev_at(*tl) + sh["look_up_m"], -float(tn)])
    R0 = basis(C0, T)
    vv, uu = np.mgrid[0:H, 0:W]
    zf = np.where(sky, 1.0, z)
    Pc = np.stack([(uu + 0.5 - cx) / fx * zf, -(vv + 0.5 - cy) / fy * zf, -zf], -1)
    Pw = Pc @ R0.T + C0                                   # world position of every pixel
    dirs_w = (np.stack([(uu + 0.5 - cx) / fx, -(vv + 0.5 - cy) / fy, -np.ones_like(uu, float)], -1)) @ R0.T

    # real features in world coordinates
    replay = json.loads((rd.ROOT / "data" / "replay.json").read_text())
    truth = json.loads((rd.ROOT / "data" / "truth.geojson").read_text())
    outline = next(f for f in truth["features"] if f["properties"].get("role") != "hit_zone")["geometry"]["coordinates"][0]

    def world(lonlat, lift):
        out = []
        for lo, la in lonlat:
            e, n = rd.enu(lo, la, *rd.ORIGIN)
            out.append([float(e), elev_at(lo, la) + lift, -float(n)])
        return np.array(out)

    path_w, det_w = world(replay["path"], 12.0), world(outline, 20.0)
    path_km = replay["path_km"]

    imgs = {k: np.asarray(Image.open(HERE / "out" / f).convert("RGB")) for k, f in
            (("before", "close_before_clean.png"), ("after", "close_after2/frames_0_image.jpg"))}
    fwd = (T - C0) / np.linalg.norm(T - C0)
    right = R0[:, 0]
    frames = []
    n = int(FPS * SECONDS)
    for i in range(n):
        s = i / (n - 1)
        e = 1 - (1 - s) ** 2.2                            # ease out: drift in, settle
        C = C0 + fwd * 520.0 * e + right * 110.0 * e - np.array([0, 60.0 * e, 0])
        R = basis(C, T)
        # terrain pixels
        Vc = (Pw - C) @ R
        zc = -Vc[..., 2]
        u2 = cx + fx * Vc[..., 0] / zc; v2 = cy - fy * Vc[..., 1] / zc
        # sky pixels: direction only (at infinity)
        Ds = dirs_w @ R
        su = cx + fx * Ds[..., 0] / -Ds[..., 2]; sv = cy - fy * Ds[..., 1] / -Ds[..., 2]
        u2 = np.where(sky, su, u2); v2 = np.where(sky, sv, v2); zc = np.where(sky, 1e9, zc)
        # backward warp: invert the forward flow with a fixed-point iteration, then sample the source smoothly
        Fu, Fv = u2 - (uu + 0.5), v2 - (vv + 0.5)
        qu, qv = uu.astype(float), vv.astype(float)
        for _ in range(6):
            fu = ndimage.map_coordinates(Fu, [qv, qu], order=1, mode="nearest")
            fv = ndimage.map_coordinates(Fv, [qv, qu], order=1, mode="nearest")
            qu, qv = uu - fu, vv - fv
        for k, src in imgs.items():
            buf = np.stack([ndimage.map_coordinates(src[..., c].astype(float), [qv, qu], order=1, mode="nearest") for c in range(3)], -1)
            Image.fromarray(np.clip(buf, 0, 255).astype(np.uint8)).resize((UP_W, UP_H), Image.LANCZOS).save(OUT / f"{k}_{i:03d}.jpg", quality=92)

        def proj(Pts):
            Vv = (Pts - C) @ R; zz = -Vv[:, 2]
            return [[round(float(cx + fx * a / c) * UP_W / W, 1), round(float(cy - fy * b / c) * UP_H / H, 1)] if c > 30 else None
                    for a, b, c in zip(Vv[:, 0], Vv[:, 1], zz)]
        frames.append(dict(path=proj(path_w), outline=proj(det_w)))
    # visibility of the path from the base camera (hidden stretches behind ridges are not drawn)
    base_vis = [q[3] if q else False for q in meta["overlays"]["flood_path"]]
    (OUT / "overlays.json").write_text(json.dumps(dict(fps=FPS, frames=frames, path_km=path_km, path_visible=base_vis,
                                                       source="Copernicus DEM depth, Atlas atlasChisel appearance; overlays: UNOSAT 4257 outline, D8 flood route"), separators=(",", ":")))
    print("frames", n, "->", OUT)


if __name__ == "__main__":
    main()
