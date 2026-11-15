"""Sensitivity check (not the headline): subtract the GUNW products' own wet + hydrostatic
tropospheric phase screens before ranking, and report where the source valley lands.

The headline result keeps the pre-registered processing (no troposphere correction).
Writes data/sensitivity.json. Usage: python pipeline/sensitivity_tropo.py
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np

from grid import DATA, RAW, X_EDGE0, X_EDGE1, Y_EDGE0, Y_EDGE1

TRO = RAW / "nisar"


def fetch(name_url):
    name, url = name_url
    out = TRO / f"TROPO_{name}.npz"
    if out.exists():
        return name
    import earthaccess
    import h5py

    earthaccess.login(strategy="netrc")
    fs = earthaccess.get_fsspec_https_session()
    with fs.open(url, block_size=2**22, cache_type="blockcache") as fh, h5py.File(fh, "r") as h:
        rg = h["science/LSAR/GUNW/metadata/radarGrid"]
        rx, ry = rg["xCoordinates"][:], rg["yCoordinates"][:]
        cc = np.where((rx > X_EDGE0 - 2000) & (rx < X_EDGE1 + 2000))[0]
        rr = np.where((ry > Y_EDGE0 - 2000) & (ry < Y_EDGE1 + 2000))[0]
        sl = (slice(None), slice(rr[0], rr[-1] + 1), slice(cc[0], cc[-1] + 1))
        np.savez_compressed(out, wet=rg["wetTroposphericPhaseScreen"][sl], hydro=rg["hydrostaticTroposphericPhaseScreen"][sl],
                            x=rx[sl[2]], y=ry[sl[1]], h=rg["heightAboveEllipsoid"][:])
    return name


def tropo_on_grid(name, z):
    from scipy.interpolate import RegularGridInterpolator

    from grid import NX, NY, XC, YC

    T = np.load(TRO / f"TROPO_{name}.npz")
    h, ry, rx = T["h"], T["y"], T["x"]
    order = np.argsort(ry)
    yy, xx = np.meshgrid(YC, XC, indexing="ij")
    pts = np.stack([yy.ravel(), xx.ravel()], -1)
    zc = np.clip(np.nan_to_num(z, nan=np.nanmedian(z)), h[0], h[-1])
    k = np.clip(np.searchsorted(h, zc) - 1, 0, len(h) - 2)
    t = (zc - h[k]) / (h[k + 1] - h[k])
    cube = (T["wet"] + T["hydro"])[:, order, :]
    layers = np.stack([RegularGridInterpolator((ry[order], rx), cube[i], bounds_error=False, fill_value=None)(pts).reshape(NY, NX) for i in range(len(h))])
    lo = np.take_along_axis(layers, k[None], 0)[0]
    hi = np.take_along_axis(layers, (k + 1)[None], 0)[0]
    return lo * (1 - t) + hi * t


def main():
    manifest = json.loads((RAW / "nisar" / "manifest.json").read_text())
    import sys
    jobs = [(k[5:], v["url"]) for k, v in manifest.items() if k.startswith("GUNW_")]
    if "--no-fetch" not in sys.argv:
        with cf.ProcessPoolExecutor(max_workers=3) as ex:
            for n in ex.map(fetch, jobs):
                pass
    import clean
    import rank
    from grid import dem

    z = dem()
    tmp = Path(tempfile.mkdtemp(prefix="cw_tropo_"))
    prod, data = tmp / "products", tmp / "data"
    prod.mkdir(); data.mkdir()
    # copy the headline products, then replace only the phase channel with a troposphere-corrected one
    for p in clean.PROD.glob("*"):
        shutil.copy(p, prod / p.name)
    orig_phase = clean.phase_channel
    corrected = []
    for p in sorted(prod.glob("*.npz")):
        name = p.stem
        if not (TRO / f"TROPO_{name}.npz").exists():
            continue  # screens not fetched: this window keeps the headline phase and is flagged below
        corrected.append(name)
        g = dict(np.load(RAW / "nisar" / f"GUNW_{name}.npz"))
        g["unwrappedPhase"] = g["unwrappedPhase"] - tropo_on_grid(name, z)
        ph = orig_phase(name, g, name[0])
        P = dict(np.load(p))
        P["los_rel_mm"] = ph["los_rel_mm"].astype(np.float32)
        np.savez_compressed(p, **P)
        info = json.loads((prod / f"{name}.json").read_text())
        info["phase_noise_mm"] = ph["noise_mm"]
        (prod / f"{name}.json").write_text(json.dumps(info))
    rank.PROD, rank.DATA = prod, data
    rank.run()
    card = json.loads((data / "scorecard.json").read_text())
    head = json.loads((DATA / "scorecard.json").read_text())
    out = {"note": "Same pipeline, but each GUNW's wet + hydrostatic tropospheric phase screens are subtracted before ranking. Headline keeps the pre-registered processing.",
           "windows": {w: {"headline_rank": head["windows"][w]["source_rank"], "tropo_rank": c["source_rank"],
                           "headline_n": head["windows"][w]["n_ranked"], "tropo_n": c["n_ranked"], "tropo_applied": w in corrected} for w, c in card["windows"].items()},
           "pass_with_tropo": card["pass"]}
    (DATA / "sensitivity.json").write_text(json.dumps(out, indent=1))
    shutil.rmtree(tmp, ignore_errors=True)
    for w, v in out["windows"].items():
        if w.startswith("A098_2026") and w < "A098_20260832" or w.startswith("D048_20260723"):
            print(w, v)
    print(out["pass_with_tropo"])


if __name__ == "__main__":
    main()
