"""Provenance for the page: where every number comes from, read from the files on disk.

Writes data/provenance.json with
- the pre-registration record: config SHA-256 and the git commits that show the rules were
  committed before the first NISAR result,
- every NISAR granule read (scene name, release, ASF processing time, data URL), from raw/nisar/manifest.json,
- the raw DHM ten-minute gauge rows around the collapse for the gauges the replay uses,
- a file index (path, bytes) of everything the page loads from /data.

Usage: python pipeline/build_provenance.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW, DATA = ROOT / "raw", ROOT / "data"


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def commits():
    out = []
    for line in git("log", "--reverse", "--format=%H|%cI|%s").splitlines():
        h, t, s = line.split("|", 2)
        files = git("show", "--name-only", "--format=", h).splitlines()
        out.append({"hash": h, "short": h[:7], "time": t, "subject": s,
                    "touches_config": "pipeline/config.json" in files,
                    "adds_results": any(f.startswith("data/scorecard") or f.startswith("data/blobs_") for f in files)})
    return out


def granules():
    man = json.loads((RAW / "nisar" / "manifest.json").read_text())
    rows = []
    for key, v in sorted(man.items()):
        rows.append({"key": key, "level": v.get("level"), "scene": v.get("scene"), "crid": v.get("crid"),
                     "asf_processed": v.get("asf_processingDate"), "direction": v.get("direction"),
                     "track": v.get("track"), "dates": v.get("dates"), "url": v.get("url")})
    return rows


def gauges():
    replay = json.loads((DATA / "replay.json").read_text())
    out = []
    for g in replay.get("gauges", []):
        hits = sorted((RAW / "event" / "dhm").glob(f"*_{g['station_id']}_20260826.csv"))
        if not hits:
            continue
        with hits[0].open() as fh:
            rows = list(csv.DictReader(fh))
        window = [{"npt": r["time_npt"][11:16], "utc": r["time_utc"], "stage_m": float(r["stage_m"])}
                  for r in rows if "07:30" <= r["time_npt"][11:16] <= "10:00"]
        out.append({"name": g["name"], "station_id": g["station_id"], "dhm_name": g.get("dhm_name"),
                    "warning_m": g.get("warning_m"), "file": str(hits[0].relative_to(ROOT)),
                    "n_rows_day": len(rows), "last_row": {"npt": rows[-1]["time_npt"][11:16], "stage_m": float(rows[-1]["stage_m"])},
                    "rows_0730_1000": window})
    return out


def files():
    return [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size}
            for p in sorted(DATA.glob("*.json*")) if p.name != "provenance.json"] + \
           [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size} for p in sorted(DATA.glob("*.geojson"))]


def main():
    cfg = ROOT / "pipeline" / "config.json"
    cm = commits()
    first_cfg = next((c for c in cm if c["touches_config"]), None)
    first_res = next((c for c in cm if c["adds_results"]), None)
    out = {
        "config": {"path": "pipeline/config.json", "sha256": hashlib.sha256(cfg.read_bytes()).hexdigest(),
                   "first_commit": first_cfg, "first_results_commit": first_res,
                   "committed_before_results": bool(first_cfg and first_res and first_cfg["time"] < first_res["time"])},
        "commits": cm,
        "granules": granules(),
        "gauges": gauges(),
        "files": files(),
    }
    (DATA / "provenance.json").write_text(json.dumps(out, indent=1))
    (DATA / "config.json").write_text(cfg.read_text())   # the page reads the committed rules, never its own copy
    bt = ROOT / "pipeline" / "backtest.json"
    if bt.exists():   # the pre-registered backtest plan and the commit that fixed it
        plan = json.loads(bt.read_text())
        first = git("log", "--reverse", "--format=%H|%cI|%s", "--", "pipeline/backtest.json").splitlines()
        if first:
            h, t, sub = first[0].split("|", 2)
            plan["_committed"] = {"hash": h, "short": h[:7], "time": t, "subject": sub}
        (DATA / "backtest_plan.json").write_text(json.dumps(plan, indent=1))
    print("granules", len(out["granules"]), "gauges", len(out["gauges"]), "commits", len(cm),
          "config before results:", out["config"]["committed_before_results"])


if __name__ == "__main__":
    main()
