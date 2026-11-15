"""Stage 2 — Clean the two NISAR channels for every window.

Offsets (GOFF): raw dense offsets -> cull -> remove bulk shift -> project the
slant-range and along-track offsets onto the local fall line (Copernicus DEM)
-> metres of downhill motion per pixel, with its propagated sigma, plus the
directional coherence of the offset field.

Phase (GUNW): unwrapped phase minus the ionosphere screen -> line-of-sight mm
-> coherence/connected-component mask -> robust plane removal -> subtract the
median of a 3-5 km ring -> relative LOS motion.

Writes <site raw>/products/<window>.npz and <window>.json (raw/products/ for Langtang;
see grid.py / CW_SITE). A window that cannot be processed gets <window>.failed.json
with the reason instead, and the run moves on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.interpolate import RegularGridInterpolator

from grid import CELL, CFG, NX, NY, RAW, XC, YC, dem, slope_fall_line

C = 299_792_458.0
PROD = RAW / "products"
PROD.mkdir(exist_ok=True)
OC, PC = CFG["offsets"], CFG["phase"]


def decode_mask(m: np.ndarray):
    m = m.astype(np.int64)
    low = m & 0xFF
    fill = m == 255
    water = (low // 100) == 1
    ref_sw = (low // 10) % 10
    sec_sw = low % 10
    valid = (~fill) & (ref_sw > 0) & (sec_sw > 0)
    anomaly = ((m >> 8) & 0xFFFF) > 0
    return valid & ~water, anomaly


def interp_radar_grid(g: np.lib.npyio.NpzFile, z: np.ndarray):
    """LOS (target->sensor) and along-track unit vectors on the study grid at DEM height."""
    h = g["rg_h"]
    ry, rx = g["rg_y"], g["rg_x"]
    order = np.argsort(ry)
    yy, xx = np.meshgrid(YC, XC, indexing="ij")
    pts = np.stack([yy.ravel(), xx.ravel()], -1)
    zc = np.clip(np.nan_to_num(z, nan=np.nanmedian(z)), h[0], h[-1])
    k = np.clip(np.searchsorted(h, zc) - 1, 0, len(h) - 2)
    t = (zc - h[k]) / (h[k + 1] - h[k])
    out = {}
    for n in ("losUnitVectorX", "losUnitVectorY", "alongTrackUnitVectorX", "alongTrackUnitVectorY", "incidenceAngle"):
        cube = g["rg_" + n][:, order, :]
        layers = np.stack(
            [RegularGridInterpolator((ry[order], rx), cube[i], bounds_error=False, fill_value=None)(pts).reshape(NY, NX) for i in range(len(h))]
        )
        lo = np.take_along_axis(layers, k[None], 0)[0]
        hi = np.take_along_axis(layers, (k + 1)[None], 0)[0]
        out[n] = lo * (1 - t) + hi * t
    le, ln = out["losUnitVectorX"], out["losUnitVectorY"]
    lu = np.sqrt(np.clip(1 - le**2 - ln**2, 0, 1))
    ae, an = out["alongTrackUnitVectorX"], out["alongTrackUnitVectorY"]
    nrm = np.hypot(ae, an)
    return np.stack([le, ln, lu]), np.stack([ae / nrm, an / nrm, 0 * ae]), out["incidenceAngle"]


def robust_mad(a: np.ndarray) -> float:
    a = a[np.isfinite(a)]
    return float(1.4826 * np.median(np.abs(a - np.median(a)))) if a.size else np.nan


def nanmedian_filter(a: np.ndarray, w: int) -> np.ndarray:
    r = w // 2
    p = np.pad(a, r, constant_values=np.nan)
    win = np.lib.stride_tricks.sliding_window_view(p, (w, w))
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return np.nanmedian(win.reshape(*a.shape, w * w), axis=-1)


def local_median_cull(a: np.ndarray, valid: np.ndarray, w: int, k: float) -> np.ndarray:
    filled = np.where(valid, a, np.nan)
    med = nanmedian_filter(filled, w)
    dev = np.abs(filled - med)
    mad = 1.4826 * nanmedian_filter(dev, w)
    return valid & (dev <= k * np.maximum(mad, 1e-3))


def dir_coherence(u: np.ndarray, v: np.ndarray, valid: np.ndarray, w: int) -> np.ndarray:
    mag = np.hypot(u, v)
    ok = valid & (mag > 0)
    ue = np.where(ok, u / np.where(ok, mag, 1), 0.0)
    ve = np.where(ok, v / np.where(ok, mag, 1), 0.0)
    n = ndimage.uniform_filter(ok.astype(float), w)
    mu = ndimage.uniform_filter(ue, w)
    mv = ndimage.uniform_filter(ve, w)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.hypot(mu, mv) / n
    return np.where(ok & (n * w * w >= 3), r, np.nan)


def offsets_channel(name: str, g, z, slope, f, los, at, stable):
    path = RAW / "nisar" / f"GOFF_{name}.npz"
    if not path.exists():
        return None
    o = np.load(path)
    valid0, _ = decode_mask(o["mask"])
    p_rg = -(f * los).sum(0)  # slant-range offset = -(d . LOS_target->sensor)
    p_az = (f * at).sum(0)
    sens = np.hypot(p_rg, p_az)
    geo_ok = (slope >= OC["min_slope_deg_for_fall_line"]) & (sens >= OC["min_projection_sensitivity"])
    best = None
    for L in ("layer1", "layer2", "layer3"):
        rg, az = o[f"{L}_slantRangeOffset"], o[f"{L}_alongTrackOffset"]
        vrg, vaz = o[f"{L}_slantRangeOffsetVariance"], o[f"{L}_alongTrackOffsetVariance"]
        snr = o[f"{L}_snr"]
        ok = valid0 & np.isfinite(rg) & np.isfinite(az) & np.isfinite(vrg) & np.isfinite(vaz)
        ok &= (np.sqrt(np.maximum(vrg, 0)) <= OC["cull_max_sigma_m"]) & (np.sqrt(np.maximum(vaz, 0)) <= OC["cull_max_sigma_m"])
        ok &= snr >= OC["cull_min_snr"]
        ok = local_median_cull(rg, ok, OC["cull_local_window_cells"], OC["cull_local_mad_k"])
        ok = local_median_cull(az, ok, OC["cull_local_window_cells"], OC["cull_local_mad_k"])
        rg = rg - np.nanmedian(rg[ok])
        az = az - np.nanmedian(az[ok])
        wrg, waz = 1 / np.maximum(vrg, 1e-6), 1 / np.maximum(vaz, 1e-6)
        den = wrg * p_rg**2 + waz * p_az**2
        s = (wrg * rg * p_rg + waz * az * p_az) / den
        sig = 1 / np.sqrt(den)
        okp = ok & geo_ok
        noise = robust_mad(s[okp & stable])
        cand = dict(layer=L, s=np.where(okp, s, np.nan), sig=np.where(okp, sig, np.nan), rg=np.where(ok, rg, np.nan),
                    az=np.where(ok, az, np.nan), ok=ok, okp=okp, noise=noise, corr=o[f"{L}_correlationSurfacePeak"],
                    snr=snr, valid_share=float(ok.mean()))
        if best is None or (np.isfinite(noise) and noise < best["noise"]):
            best = cand
    best["dircoh"] = dir_coherence(best["rg"], best["az"], best["ok"], OC["dir_coherence_window_cells"])
    return best


def ring_median(a: np.ndarray, valid: np.ndarray, r0_km: float, r1_km: float, factor: int = 4) -> np.ndarray:
    ny, nx = a.shape
    pad_y, pad_x = (-ny) % factor, (-nx) % factor
    b = np.pad(np.where(valid, a, np.nan), ((0, pad_y), (0, pad_x)), constant_values=np.nan)
    b = b.reshape(b.shape[0] // factor, factor, b.shape[1] // factor, factor)
    with np.errstate(all="ignore"):
        coarse = np.nanmedian(b, axis=(1, 3))
    cell = CELL * factor
    r1 = int(np.ceil(r1_km * 1000 / cell))
    yy, xx = np.mgrid[-r1 : r1 + 1, -r1 : r1 + 1]
    rr = np.hypot(yy, xx) * cell / 1000
    fp = (rr >= r0_km) & (rr <= r1_km)
    with np.errstate(all="ignore"):
        rm = ndimage.generic_filter(coarse, np.nanmedian, footprint=fp, mode="constant", cval=np.nan)
    rm = np.where(np.isfinite(rm), rm, np.nanmedian(coarse))
    up = ndimage.zoom(rm, factor, order=1)[:ny, :nx]
    return up


def phase_channel(name: str, g, direction: str):
    lam = C / float(json.loads((RAW / "nisar" / f"GUNW_{name}.json").read_text())["centerFrequency"])
    phi = g["unwrappedPhase"].astype(np.float64)
    if PC["subtract_ionosphere_screen"]:
        phi = phi - g["ionospherePhaseScreen"]
    los_mm = -lam / (4 * np.pi) * phi * 1000.0  # + = towards the sensor
    coh = g["coherenceMagnitude"].astype(float)
    cc = g["connectedComponents"]
    valid0, _ = decode_mask(g["unw_mask"])
    floor = PC["coherence_floor_ascending"] if direction == "A" else PC["coherence_floor_descending"]
    ok = valid0 & np.isfinite(los_mm) & (coh >= floor) & (cc > 0) & (cc != 65535)
    # robust plane
    yy, xx = np.mgrid[0:NY, 0:NX]
    A = np.stack([np.ones(ok.sum()), xx[ok], yy[ok]], 1)
    b = los_mm[ok]
    keep = np.ones(b.size, bool)
    for _ in range(4):
        coef, *_ = np.linalg.lstsq(A[keep], b[keep], rcond=None)
        res = b - A @ coef
        keep = np.abs(res - np.median(res)) < 3 * robust_mad(res)
    plane = coef[0] + coef[1] * xx + coef[2] * yy
    flat = los_mm - plane
    rm = ring_median(flat, ok, PC["ring_inner_km"], PC["ring_outer_km"])
    rel = np.where(ok, flat - rm, np.nan)
    return dict(los_rel_mm=rel, coh=coh, ok=ok, lam=lam, floor=floor, noise_mm=robust_mad(rel))


def glacier_mask() -> np.ndarray:
    import rasterio.features
    from shapely.geometry import shape
    from shapely.ops import transform as stransform

    from grid import TO_UTM, TRANSFORM, find

    p = find("glaciers.geojson")
    if p is None:
        return np.zeros((NY, NX), bool)
    gj = json.loads(Path(p).read_text())
    geoms = [stransform(lambda x, y, z=None: TO_UTM.transform(x, y), shape(ft["geometry"])) for ft in gj["features"]]
    return rasterio.features.rasterize([(gm, 1) for gm in geoms], out_shape=(NY, NX), transform=TRANSFORM, all_touched=False).astype(bool)


def windows_available() -> list[str]:
    return sorted(p.stem[5:] for p in (RAW / "nisar").glob("GUNW_*.npz"))


def run(names: list[str] | None = None) -> None:
    z = dem()
    slope, f = slope_fall_line(z)
    ice = glacier_mask()
    stable = (~ice) & (slope < 20)
    for name in names or windows_available():
        try:
            clean_window(name, z, slope, f, ice, stable)
            (PROD / f"{name}.failed.json").unlink(missing_ok=True)
        except Exception as e:  # recorded, never filled: rank.py only sees windows that were processed
            for ext in (".npz", ".json"):
                (PROD / f"{name}{ext}").unlink(missing_ok=True)
            (PROD / f"{name}.failed.json").write_text(json.dumps({"window": name, "error": repr(e)[:500]}, indent=1))
            print("FAILED", name, repr(e)[:300], flush=True)


def clean_window(name: str, z, slope, f, ice, stable) -> None:
    g = np.load(RAW / "nisar" / f"GUNW_{name}.npz")
    direction = name[0]
    los, at, inc = interp_radar_grid(g, z)
    off = offsets_channel(name, g, z, slope, f, los, at, stable)
    ph = phase_channel(name, g, direction)
    info = {"window": name, "direction": direction, "lambda_m": ph["lam"], "phase_noise_mm": ph["noise_mm"],
            "phase_valid_share": float(ph["ok"].mean()),
            "phase_ice_lost_share": float(((~ph["ok"]) & ice).sum() / max(ice.sum(), 1))}
    arrays = dict(los_rel_mm=ph["los_rel_mm"].astype(np.float32), coh=ph["coh"].astype(np.float32), inc=inc.astype(np.float32))
    if off is not None:
        info.update(offsets_layer=off["layer"], offsets_noise_m=off["noise"], offsets_valid_share=off["valid_share"],
                    offsets_ice_lost_share=float(((~off["okp"]) & ice).sum() / max(ice.sum(), 1)),
                    glacier_median_s_m=float(np.nanmedian(off["s"][ice])) if ice.any() else None,
                    stable_median_s_m=float(np.nanmedian(off["s"][stable])))
        arrays.update(s=off["s"].astype(np.float32), sig=off["sig"].astype(np.float32), dircoh=off["dircoh"].astype(np.float32),
                      corr=off["corr"].astype(np.float32), rg=off["rg"].astype(np.float32), az=off["az"].astype(np.float32))
    np.savez_compressed(PROD / f"{name}.npz", **arrays)
    (PROD / f"{name}.json").write_text(json.dumps(info, indent=1))
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in info.items()}), flush=True)


if __name__ == "__main__":
    run(sys.argv[1:] or None)
