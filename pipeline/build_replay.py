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

COLLAPSE = datetime.fromisoformat(CFG["windows"]["event_utc"].replace("Z", "+00:00"))   # pre-registered event time (USGS origin)
NPT = timezone(timedelta(hours=5, minutes=45))


def source_short(url: str) -> str:
    for key, name in (("eartharxiv", "Acharya 2026"), ("earthquake.usgs.gov", "USGS"), ("kathmandupost", "Kathmandu Post (not archived)"),
                      ("ndrrma", "NDRRMA"), ("zenodo.22679444", "Acharya & Paudel data"), ("rs18213727", "Kutoğlu 2026"), ("rs-11167819", "Hassan 2026"),
                      ("geofon", "GEOFON"), ("unosat", "UNOSAT"), ("copernicus", "Copernicus EMS")):
        if key in url:
            return name
    return url.split("/")[2] if "//" in url else url


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

    # Event log: every item comes from raw/event/timeline.json, which carries a source URL and a verbatim quote.
    timeline = json.loads((RAW / "event" / "timeline.json").read_text())["events"]
    tmax = anchors[-1][0] + 1
    events = []
    for e in timeline:
        t = e.get("t_min_after_collapse")
        if t is None or t < -1 or t > tmax or not e.get("source_url"):
            continue
        url = e["source_url"].split(" ")[0]
        events.append(dict(t_min=round(max(t, 0.0), 2), npt=(e.get("npt") or "")[11:16], label=e["label"], url=url,
                           src=source_short(url), quote=e.get("quote", ""), kind=e.get("kind")))
    sms = next(e for e in timeline if e.get("kind") == "alert" and e["label"].lower().startswith("mass sms"))
    facts = json.loads((RAW / "event" / "facts.json").read_text())["facts"]
    collapse_npt = facts["timing"]["collapse_npt"]["value"]
    # places with a sourced arrival report of their own (villages_arrival.json) carry it alongside the routed estimate
    places = {p["name"]: p for p in json.loads((RAW / "event" / "villages_arrival.json").read_text())["places"]}
    for v in dedup:
        pl = places.get(v["name"])
        for rep in (pl or {}).get("other_arrival_reports") or []:
            tr = rep.get("time_range") or ([rep["time"], rep["time"]] if rep.get("time") else None)
            if tr:
                v.setdefault("reported", []).append(dict(t_lo=tr[0]["t_min"], t_hi=tr[1]["t_min"], npt=tr[0]["npt"] + ("–" + tr[1]["npt"] if tr[1] is not tr[0] else ""),
                                                          source_url=rep["source_url"].split(" ")[0], quote=rep.get("quote", "")))
        if pl and pl.get("lead_time_min"):
            v["lead_time"] = dict(pl["lead_time_min"], source_url=pl["lead_time_min"]["source_url"].split(" ")[0])
    out = dict(
        collapse_utc=COLLAPSE.isoformat().replace("+00:00", "Z"), collapse_npt=collapse_npt,
        alert_min=round(sms["t_min_after_collapse"], 2), alert_label=sms["label"], alert_source=sms["source_url"].split(" ")[0], alert_quote=sms.get("quote", ""),
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
