"""Sensitivity: rank the target's patches against ALL patches from the three study boxes on the same track and pass
(Langtang, Budhi Gandaki, Manang), i.e. a haystack about 3x larger than the box drawn around each event. Post hoc.

The season factor is divided out first: it differs between boxes (Langtang uses ERA5, backtest boxes use 1) but is
the same for every patch inside one box. Scores are otherwise comparable because each is motion over its own box's noise.

Usage: python pipeline/sensitivity_pooled.py  ->  data/sensitivity_pooled.json
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CFG = json.loads((ROOT / "pipeline" / "config.json").read_text())
V2 = json.loads((ROOT / "pipeline" / "prereg_v2.json").read_text())
BT = json.loads((DATA / "backtest.json").read_text())
WS, HIT, ZONE = CFG["score"]["w_season"], CFG["truth"]["hit_radius_km"], V2["zone"]["radius_km"]
PATHS = {"langtang": DATA / "blobs_{w}.geojson", "budhigandaki": DATA / "sites" / "budhigandaki" / "blobs_{w}.geojson",
         "manang": DATA / "sites" / "manang" / "blobs_{w}.geojson"}


def patches(site, w):
    p = Path(str(PATHS[site]).format(w=w))
    if not p.exists():
        return []
    out = []
    for f in json.loads(p.read_text())["features"]:
        q = f["properties"]
        if not q.get("rank"):
            continue
        truth = site != "manang"
        out.append(dict(site=site, s=q["score"] / (1 + WS * (q["season_anomaly"] - 1)), own=q["rank"],
                        hit=truth and q["dist_truth_km"] <= HIT and q["same_channel"], zone=truth and q["dist_truth_km"] <= ZONE and q["same_channel"]))
    return out


def main():
    rows = []
    targets = [("langtang", BT["sites"]["langtang"]["false_alarm"]["windows_counted"]), ("budhigandaki", BT["sites"]["budhigandaki"]["positive_pass"]["windows"])]
    for target, wins in targets:
        for w in wins:
            pool = [p for s in PATHS for p in patches(s, w)]
            pool.sort(key=lambda p: -p["s"])
            for i, p in enumerate(pool):
                p["pooled"] = i + 1
            mine = [p for p in pool if p["site"] == target]
            best = lambda k: min((p["pooled"] for p in mine if p[k]), default=None)
            rows.append(dict(target=target, window=w, n_pooled=len(pool), boxes=sorted({p["site"] for p in pool}),
                             own_rank=min((p["own"] for p in mine if p["hit"]), default=None), pooled_rank=best("hit"), pooled_zone_rank=best("zone")))
    out = dict(note="Post hoc sensitivity: each study box was drawn around a known event; this ranks against all three boxes on the same track and pass.",
               rows=rows)
    (DATA / "sensitivity_pooled.json").write_text(json.dumps(out, indent=1))
    for r in rows:
        print(r["target"], r["window"], "own", r["own_rank"], "pooled", r["pooled_rank"], "zone", r["pooled_zone_rank"], "of", r["n_pooled"])


if __name__ == "__main__":
    main()
