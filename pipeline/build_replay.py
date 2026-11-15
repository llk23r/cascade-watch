"""Stage 4 — The replay of 26 August: flood path, gauge-bracketed front, villages, event log.

Everything is sourced: the path is D8 flow routing on the Copernicus DEM from the USGS
source; arrival times are the DHM ten-minute gauge brackets (last sample at background /
last transmission -> first rise or silence); places between gauges are interpolated by
distance along the path and labelled as such. Writes data/replay.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import hydro
from grid import CFG, DATA, RAW, TO_UTM, find

COLLAPSE = datetime(2026, 8, 26, 2, 52, 10, tzinfo=timezone.utc)
NPT = timezone(timedelta(hours=5, minutes=45))


def t_of_npt(s: str) -> float:
    if len(s) <= 5:  # "HH:MM" on 26 Aug, NPT
        s = f"2026-08-26T{s}:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=NPT)
    return (d - COLLAPSE).total_seconds() / 60.0


def main():
    sx, sy = TO_UTM.transform(*CFG["truth"]["source_lonlat"])
    rows, cols, xs, ys, along = hydro.path_from(sx, sy)
    step = 3
    path = hydro.path_lonlat(xs, ys, step)
    path_km = [round(float(a) / 1000, 3) for a in along[::step]]

    def km_of(lon, lat):
        x, y = TO_UTM.transform(lon, lat)
        d = np.hypot(xs - x, ys - y)
        i = int(np.argmin(d))
        return float(along[i] / 1000), float(d[i])

    st = json.loads(Path(find("stations.json")).read_text())
    gauges = []
    for s in st["stations"]:
        br = s.get("arrival_bracket_npt")
        km, off = km_of(s["lon"], s["lat"])
        if off > 1500:  # not on our routed path (tributary, or beyond the corridor)
            continue
        if not br:
            continue
        a, b = (t_of_npt(br[0]), t_of_npt(br[1])) if isinstance(br, list) else (None, None)
        gauges.append(dict(name=s["corridor_node"], station_id=s["station_id"], dhm_name=s["dhm_name"], lon=s["lon"], lat=s["lat"],
                           km=round(km, 2), arrival_lo=round(a, 2), arrival_hi=round(b, 2), arrival_min=round((a + b) / 2, 2),
                           warning_m=s.get("warning_level_m"), last_value_m=s.get("last_value_26aug_m"),
                           last_fraction_of_warning=s.get("last_value_fraction_of_warning"), destroyed=s.get("destroyed_confirmed_by_source"),
                           arrival_kind="DHM gauge bracket", basis=s.get("arrival_basis")))
    gauges = [g for g in gauges if g["name"] != "Betrawati (tributary)"]
    gauges.sort(key=lambda g: g["km"])

    anchors = [(0.0, 0.0)] + [(g["arrival_min"], g["km"]) for g in gauges]
    # beyond the last gauge, continue at the speed of the last gauged reach (marked extrapolated)
    if len(anchors) >= 2:
        (t1, k1), (t2, k2) = anchors[-2], anchors[-1]
        v = (k2 - k1) / (t2 - t1)
        t_end = t2 + (path_km[-1] - k2) / v
        anchors.append((round(t_end, 2), path_km[-1]))

    villages = [dict(name=g["name"], lon=g["lon"], lat=g["lat"], km=g["km"], arrival_min=g["arrival_min"],
                     arrival_lo=g["arrival_lo"], arrival_hi=g["arrival_hi"], arrival_kind="gauge") for g in gauges]
    # named places between gauges (OSM), interpolated along the path
    places = json.loads(Path(find("osm_places.geojson")).read_text())["features"]
    want = {"Timure", "Trishuli Bazar", "Bidur", "बिदुर"}
    for f in places:
        pr = f["properties"]
        nm = pr.get("name:en") or pr.get("name")
        if nm not in want or f["geometry"]["type"] != "Point":
            continue
        lon, lat = f["geometry"]["coordinates"]
        km, off = km_of(lon, lat)
        if off > 1200:
            continue
        ts = [a[0] for a in anchors]
        ks = [a[1] for a in anchors]
        t = float(np.interp(km, ks, ts))
        kind = "interpolated" if km <= gauges[-1]["km"] else "extrapolated"
        villages.append(dict(name="Bidur" if nm in ("बिदुर",) else nm, lon=lon, lat=lat, km=round(km, 2), arrival_min=round(t, 2), arrival_kind=kind))
    villages.sort(key=lambda v: v["km"])
    dedup, seen = [], set()
    for v in villages:
        if v["name"] not in seen:
            dedup.append(v); seen.add(v["name"])

    U = {
        "usgs": "usgs", "acharya": "acharya", "ndrrma": "ndrrma", "kpost": "kpost",
    }
    events = [
        dict(t_min=0.0, npt="08:37", label="Ice and rock fall from the north flank of Langtang Lirung. USGS later catalogues it as an M5.2 landslide.", source="usgs"),
        dict(t_min=0.0, npt="08:37", label="Nepal's operational log records an M4.4 earthquake, not a landslide.", source="kpost"),
        dict(t_min=2.83, npt="08:40", label="Rasuwagadhi gauge sends its last reading: 1.62 m against a 6 m warning level (27%).", source="acharya"),
        dict(t_min=12.83, npt="08:50", label="Rasuwagadhi is silent, destroyed. Syabrubesi sends its last reading: 3.8 m against 5.5 m (69%).", source="acharya"),
        dict(t_min=22.83, npt="09:00", label="Syabrubesi is silent. The district office calls the hydrology department.", source="acharya"),
        dict(t_min=37.83, npt="09:15", label="DHM confirms the flood and issues the first warning for the Trishuli.", source="acharya"),
        dict(t_min=38.83, npt="09:16", label="Mass SMS: 679,295 messages to four districts.", source="acharya"),
        dict(t_min=41.35, npt="09:18", label="USGS first publishes the event, as an M4.4 earthquake.", source="usgs"),
        dict(t_min=42.83, npt="09:20", label="Betrawati sends its last reading: 3.55 m against 4.1 m (87%). By 09:30 it is gone.", source="acharya"),
    ]
    out = dict(
        collapse_utc=COLLAPSE.isoformat().replace("+00:00", "Z"), collapse_npt="08:37:10",
        alert_min=38.83, alert_label="Mass SMS dispatch (09:16 NPT)",
        path=path, path_km=path_km, front=[[round(t, 2), round(k, 2)] for t, k in anchors],
        gauges=gauges, villages=dedup, events=events,
        note=("Arrival at a gauge is the DHM ten-minute bracket: after its last normal reading, by its first rise or silence; "
              "the replay runs the front through the bracket midpoints along the D8 flow path on the Copernicus DEM. "
              "Places between gauges are interpolated by distance along that path; beyond Betrawati the front is extrapolated at the "
              "last gauged speed."),
    )
    (DATA / "replay.json").write_text(json.dumps(out, separators=(",", ":")))
    print(json.dumps({k: v for k, v in out.items() if k in ("front", "alert_min")}))
    for v in dedup:
        print(v)


if __name__ == "__main__":
    main()
