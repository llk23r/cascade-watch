#!/usr/bin/env python3
"""Reproducible fetch of public event evidence for the 26 Aug 2026 Langtang
ice-rock avalanche / Bhote Koshi-Trishuli flash flood.

Writes into raw/event/<source>/ and appends every file to raw/event/manifest.json
(source URL, retrieval UTC, license). Nothing here is estimated: it only
downloads and unpacks what the publishers host.

Usage:
    .venv/bin/python -I pipeline/fetch_event.py [usgs] [zenodo] [unosat] [ems] [dhm] [geofon] [papers]
(no args = all steps, in that order; `dhm` needs the Acharya package from `zenodo`)

Needs: requests, pyogrio + geopandas (UNOSAT GDB / EMS shapefiles -> GeoJSON).
No credentials are used. The derived summaries raw/event/{timeline,villages_arrival,facts}.json
were compiled by hand from the fetched sources (each item carries its source URL and quote).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "raw" / "event"
MANIFEST = OUT / "manifest.json"
MAX_BYTES = 300 * 1024 * 1024  # skip any single file > 300 MB
UA = {"User-Agent": "Mozilla/5.0 (research data fetch; cascade-watch)"}


# --------------------------------------------------------------------------- utils
def utcnow() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"files": [], "missing": []}


def save_manifest(m: dict) -> None:
    m["files"].sort(key=lambda f: f["path"])
    MANIFEST.write_text(json.dumps(m, indent=2, ensure_ascii=False))


def record(m: dict, path: Path, url: str, license_: str, note: str = "") -> None:
    rel = str(path.relative_to(ROOT))
    m["files"] = [f for f in m["files"] if f["path"] != rel]
    h = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    m["files"].append({"path": rel, "source_url": url, "retrieved_utc": utcnow(),
                       "bytes": path.stat().st_size if path.is_file() else None,
                       "sha256": h, "license": license_, "note": note})


def get(url: str, dest: Path, m: dict, license_: str, note: str = "",
        max_bytes: int = MAX_BYTES, headers_override: dict | None = None, **kw) -> Path | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=headers_override or UA, stream=True, timeout=120, **kw) as r:
        r.raise_for_status()
        size = int(r.headers.get("Content-Length") or 0)
        if size > max_bytes:
            m["missing"].append({"item": dest.name, "url": url,
                                 "reason": f"skipped: {size} bytes > limit"})
            return None
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    record(m, dest, url, license_, note)
    return dest


def unzip(zpath: Path, dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest.resolve())):
                continue  # zip-slip guard
            z.extract(info, dest)
            names.append(info.filename)
    return names


# --------------------------------------------------------------------------- USGS
USGS_Q = "https://earthquake.usgs.gov/fdsnws/event/1/query"
USGS_LICENSE = "Public domain (USGS)"


def fetch_usgs(m: dict) -> None:
    d = OUT / "usgs"
    for eid in ("us7000tbwb", "us7000tc90"):
        get(f"{USGS_Q}?eventid={eid}&format=geojson", d / f"{eid}_detail.geojson", m, USGS_LICENSE,
            "current preferred products")
        get(f"{USGS_Q}?eventid={eid}&format=geojson&includesuperseded=true",
            d / f"{eid}_detail_superseded.geojson", m, USGS_LICENSE,
            "all product versions incl. superseded (shows M4.4 earthquake -> M5.2 landslide)")
    get(f"{USGS_Q}?eventid=us7000tbwb&format=quakeml&includeallorigins=true&includeallmagnitudes=true",
        d / "us7000tbwb_quakeml_allorigins.xml", m, USGS_LICENSE)
    # summarise the version history into a small table
    det = json.loads((d / "us7000tbwb_detail_superseded.geojson").read_text())
    hist = []
    for name in ("origin",):
        for pr in sorted(det["properties"]["products"][name], key=lambda x: x["updateTime"]):
            p = pr["properties"]
            hist.append({
                "product_update_utc": dt.datetime.fromtimestamp(pr["updateTime"] / 1000, dt.UTC)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "event_type": p.get("event-type"), "magnitude": p.get("magnitude"),
                "magnitude_type": p.get("magnitude-type"), "origin_time": p.get("eventtime"),
                "lat": p.get("latitude"), "lon": p.get("longitude"), "depth_km": p.get("depth"),
                "review_status": p.get("review-status")})
    texts = {}
    for name in ("general-header", "general-text", "impact-text"):
        texts[name] = [{"update_utc": dt.datetime.fromtimestamp(pr["updateTime"] / 1000, dt.UTC)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "text": pr.get("contents", {}).get("", {}).get("bytes")}
                       for pr in sorted(det["properties"]["products"].get(name, []),
                                        key=lambda x: x["updateTime"])]
    out = d / "us7000tbwb_history.json"
    out.write_text(json.dumps({"source": f"{USGS_Q}?eventid=us7000tbwb&format=geojson&includesuperseded=true",
                               "origin_versions": hist, "text_products": texts}, indent=2, ensure_ascii=False))
    record(m, out, f"{USGS_Q}?eventid=us7000tbwb&format=geojson&includesuperseded=true", USGS_LICENSE,
           "derived summary of origin product versions and text products")


# --------------------------------------------------------------------------- Zenodo
ZENODO = "https://zenodo.org/api/records"
ZENODO_SETS = {
    # Meena 2026 (NISAR GOFF site series + footprint screen)
    "meena_22730419": {"record": "22730419", "files": None},
    # Sah 2026 concept 22541071 -> versions; pull latest + the earlier versions with
    # the S1 detection-limit material (v1.0.0, v2.0.0, v5.0.0)
    "sah_23299807_v6.1.1": {"record": "23299807", "files": None},
    "sah_22763831_v5.0.0": {"record": "22763831", "files": None},
    "sah_22676918_v2.0.0": {"record": "22676918", "files": None},
    "sah_22541072_v1.0.0": {"record": "22541072", "files": None},
    # Acharya 2026 data package (EarthArXiv 14687, doi 10.31223/X5HN5H):
    # ten-minute DHM river-watch stage series recovered by the author.
    # concept 22153581; latest v3.0.0 plus the original v1.0.0 (larger package)
    "acharya_22679444_v3.0.0": {"record": "22679444", "files": None},
    "acharya_22153582_v1.0.0": {"record": "22153582", "files": None},
}


def fetch_zenodo(m: dict) -> None:
    for name, cfg in ZENODO_SETS.items():
        d = OUT / "zenodo" / name
        meta_url = f"{ZENODO}/{cfg['record']}"
        r = requests.get(meta_url, headers=UA, timeout=60)
        r.raise_for_status()
        rec = r.json()
        (d).mkdir(parents=True, exist_ok=True)
        (d / "record.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False))
        lic = (rec.get("metadata", {}).get("license") or {}).get("id", "unknown")
        record(m, d / "record.json", meta_url, lic, "Zenodo record metadata")
        for f in rec.get("files", []):
            key, size = f["key"], f["size"]
            if cfg["files"] and key not in cfg["files"]:
                continue
            if size > MAX_BYTES:
                m["missing"].append({"item": f"{name}/{key}", "url": f["links"]["self"],
                                     "reason": f"skipped: {size} bytes > 300 MB"})
                continue
            dest = d / "download" / key
            if not (dest.exists() and dest.stat().st_size == size):
                get(f["links"]["self"], dest, m, _file_license(key, lic))
            else:
                record(m, dest, f["links"]["self"], _file_license(key, lic))
            if key.lower().endswith(".zip"):
                unzip(dest, d / "unzipped" / key[:-4])


def _file_license(key: str, default: str) -> str:
    if "CC-BY-NC" in key:
        return "cc-by-nc-4.0 (per filename)"
    return default


# --------------------------------------------------------------------------- UNOSAT
# The unosat.org product pages are a JS app; their JSON backend is
# https://unosat.org/our_products/<id>, and files live under /static/unosat_filesystem/<id>/.
UNOSAT = "https://unosat.org"
UNOSAT_PRODUCTS = [4257, 4258, 4259, 4260]  # 4257 = Rasuwa mudflow/rockflow extent (GLIDE FL20260826NPL)
UNOSAT_LICENSE = ("UNOSAT/UNITAR product; free to use with attribution 'Analysis: UNOSAT' "
                  "(see map footer); imagery copyrights as listed in product 'sources'")


def fetch_unosat(m: dict) -> None:
    seen: dict[str, Path] = {}  # sha256 -> first stored copy (4258/4259/4260 share one package)
    for pid in UNOSAT_PRODUCTS:
        d = OUT / "unosat" / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        meta_url = f"{UNOSAT}/our_products/{pid}"
        meta = requests.get(meta_url, headers=UA, timeout=60).json()
        (d / "product.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        record(m, d / "product.json", meta_url, UNOSAT_LICENSE, "UNOSAT product metadata (JSON backend)")
        ev = meta["map_event"]
        links = [ev.get("gdp_link"), ev.get("shp_link"), ev.get("kml_link"), ev.get("excel_table")]
        if ev.get("pdf_name"):
            links.append(f"/static/unosat_filesystem/{pid}/{ev['pdf_name']}")
        if ev.get("image_file"):
            links.append(ev["image_file"])
        for link in filter(None, links):
            if not link.startswith("/static"):
                link = "/static" + link
            url = UNOSAT + requests.utils.quote(link)
            dest = d / Path(link).name.replace(" ", "")
            try:
                get(url, dest, m, UNOSAT_LICENSE)
            except requests.HTTPError as e:
                m["missing"].append({"item": f"unosat {pid} {Path(link).name}", "url": url,
                                     "reason": f"HTTP {e.response.status_code}"})
                continue
            if dest.suffix != ".zip":
                continue
            sha = hashlib.sha256(dest.read_bytes()).hexdigest()
            if sha in seen:
                first = seen[sha]
                dest.unlink()
                m["files"] = [f for f in m["files"] if f["path"] != str(dest.relative_to(ROOT))]
                m["files"].append({"path": str(dest.relative_to(ROOT)), "source_url": url,
                                   "retrieved_utc": utcnow(), "bytes": first.stat().st_size,
                                   "sha256": sha, "license": UNOSAT_LICENSE,
                                   "note": f"NOT STORED: byte-identical to {first.relative_to(ROOT)}"})
                continue
            seen[sha] = dest
            unzip(dest, d / "unzipped" / dest.stem)
    convert_unosat_vectors(m)


def convert_unosat_vectors(m: dict) -> None:
    """Every layer of each stored UNOSAT geodatabase -> GeoJSON EPSG:4326, plus the
    detachment outline of product 4257 as raw/event/unosat/detachment.geojson."""
    import pyogrio
    for pid in UNOSAT_PRODUCTS:
        base = OUT / "unosat" / str(pid) / "unzipped"
        if not base.exists():
            continue
        # only real geodatabase folders (they hold *.gdbtable files directly); the zip
        # may wrap the .gdb in a folder of the same name. The SHP package repeats the
        # GDB layers, so only the GDB is converted.
        for src in [p for p in base.rglob("*.gdb") if p.is_dir() and any(p.glob("*.gdbtable"))]:
            for lyr, geom in pyogrio.list_layers(src):
                if geom is None:  # raster catalogue tables, no geometry
                    continue
                gdf = pyogrio.read_dataframe(src, layer=lyr)
                if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
                    gdf = gdf.to_crs(4326)
                out = OUT / "unosat" / str(pid) / "geojson" / f"{lyr}.geojson"
                out.parent.mkdir(parents=True, exist_ok=True)
                pyogrio.write_dataframe(gdf, out, driver="GeoJSON")
                record(m, out, f"derived from {src.relative_to(ROOT)} layer {lyr}", UNOSAT_LICENSE,
                       f"converted to EPSG:4326; {len(gdf)} features")
                if pid == 4257 and lyr.endswith("DetachmentZone"):
                    det = OUT / "unosat" / "detachment.geojson"
                    pyogrio.write_dataframe(gdf, det, driver="GeoJSON")
                    record(m, det, f"https://unosat.org/products/4257 (GDB layer {lyr})",
                           UNOSAT_LICENSE, "UNOSAT possible detachment outline, EPSG:4326")


# --------------------------------------------------------------------------- Copernicus EMS
EMS_API = "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/?code=EMSR927"
EMS_LICENSE = "Copernicus EMS data policy: free and open, attribution '(c) European Union, Copernicus EMS'"


def fetch_ems(m: dict) -> None:
    """Copernicus EMS Rapid Mapping activation EMSR927 (Flood in Nepal): every delivered
    grading (GRA) vector package. Cancelled products are listed under missing."""
    import pyogrio
    d = OUT / "copernicus_ems"
    d.mkdir(parents=True, exist_ok=True)
    act = requests.get(EMS_API, headers=UA, timeout=60).json()
    (d / "EMSR927_activation.json").write_text(json.dumps(act, indent=2, ensure_ascii=False))
    record(m, d / "EMSR927_activation.json", EMS_API, EMS_LICENSE, "activation metadata")
    r = act["results"][0] if "results" in act else act
    for aoi in r.get("aois", []):
        for p in aoi.get("products", []):
            url = p.get("downloadPath")
            if not url:
                m["missing"].append({"item": f"EMSR927 AOI{aoi.get('number'):02d} {aoi.get('name')} {p.get('type')}",
                                     "url": EMS_API, "reason": (p.get("version") or {}).get("reason") or "no download"})
                continue
            dest = d / Path(url).name
            if get(url, dest, m, EMS_LICENSE, f"AOI {aoi.get('name')}", max_bytes=100 * 1024 * 1024):
                udir = d / "unzipped" / dest.stem
                unzip(dest, udir)
                for shp in udir.rglob("*.shp"):
                    gdf = pyogrio.read_dataframe(shp)
                    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
                        gdf = gdf.to_crs(4326)
                    out = d / "geojson" / f"{shp.stem}.geojson"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    pyogrio.write_dataframe(gdf, out, driver="GeoJSON")
                    record(m, out, f"derived from {shp.relative_to(ROOT)}", EMS_LICENSE,
                           f"EPSG:4326; {len(gdf)} features")


# --------------------------------------------------------------------------- DHM river watch
# Station page:  https://www.dhm.gov.np/hydrology/hms-Single/<station_id>  (embeds `var river = '{...}'`
#                with name, lat/lon, warning_level, danger_level, series_id; plus a CSRF token)
# Series:        POST https://dhm.gov.np/site/getRiverWatchBySeriesId_Single
#                form: csrf_test_name, date=YYYY-MM-DD, period (1 point/10-min, 2 hourly, 3 daily,
#                4 7-day point), seriesid -> JSON {data:{table (HTML), chart (JS with ISO-UTC samples)}}
# Station list:  POST https://dhm.gov.np/site/riverWatchTableViewData
# The archive behind the series endpoint is rolling (about two weeks back, see retention probe),
# so 26 Aug 2026 can no longer be retrieved from DHM. The event-day series are therefore taken
# from the Acharya & Paudel data package (Zenodo 10.5281/zenodo.22679444), which archived what this
# same endpoint returned on 1 Sep 2026 (re-verified 9 Sep 2026).
DHM_PAGE = "https://www.dhm.gov.np/hydrology/hms-Single/{}"
DHM_SERIES = "https://dhm.gov.np/site/getRiverWatchBySeriesId_Single"
DHM_LIST = "https://dhm.gov.np/site/riverWatchTableViewData"
DHM_LICENSE = "Government of Nepal, DHM public river-watch data (no explicit licence stated)"
DHM_STATIONS = {  # upstream -> downstream; ids from the river-watch station list
    4898: "Langtang Khola at Kyangjin", 4913: "Bhotekoshi at Rasuwagadi",
    191: "Bhote Koshi at Shyaprubesi", 190: "Langtang Khola at Shyaprubesi",
    4784: "Lantang Khola at Shyaprubesi", 4657: "Trishuli Khola at Dhunche",
    52: "Trishuli at Betrawati", 4783: "Trishuli River at Betrawati",
    4658: "Phalakhu Khola at Betrawati", 5705: "Trishuli at Galchi",
    5611: "Phurke (Malekhu)", 4781: "Trishuli River at Kali Khola",
    4661: "Trishuli River at Bhorle", 265: "Narayani at Devghat",
}
NPT = dt.timezone(dt.timedelta(hours=5, minutes=45))
ACHARYA_GAUGES = ("raw/event/zenodo/acharya_22679444_v3.0.0/unzipped/zenodo_upload/"
                  "bhotekoshi-trishuli-2026/data/gauge_timeseries.json")


def _dhm_station(sess: requests.Session, sid: int) -> tuple[str | None, str | None, dict]:
    import re
    html = ""
    for attempt in range(3):
        try:
            html = sess.get(DHM_PAGE.format(sid), timeout=45).text
        except requests.RequestException:
            html = ""
        if 'name="seriesid"' in html or "var river" in html:
            break
    csrf = re.search(r'csrf_test_name"\s+value="([a-f0-9]+)"', html)
    ser = re.search(r'name="seriesid"[^>]*value="(\d+)"', html)
    blob = re.search(r"var river\s*=\s*'(.*?)';", html, re.S)
    try:
        meta = json.loads(blob.group(1)) if blob else {}
    except json.JSONDecodeError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {"_page_record_not_a_dict": meta}
    meta.pop("images", None)
    return (csrf.group(1) if csrf else None), (ser.group(1) if ser else None), meta


def _dhm_series(sess, csrf, ser, day, period=1) -> list[dict]:
    """Samples from the chart payload, which carries ISO-8601 UTC datetimes."""
    import re
    r = sess.post(DHM_SERIES, data={"csrf_test_name": csrf, "date": day, "period": period,
                                    "seriesid": ser},
                  headers={"X-Requested-With": "XMLHttpRequest"}, timeout=60).json()
    if r.get("status") != "success":
        return []
    c = re.search(r"var river = '(.*?)';", r["data"]["chart"], re.S)
    if not c:
        return []
    raw = c.group(1).encode().decode("unicode_escape")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def fetch_dhm(m: dict) -> None:
    import csv
    d = OUT / "dhm"
    d.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers.update(UA)
    # 1. station list snapshot (current status)
    lst = sess.post(DHM_LIST, headers={"X-Requested-With": "XMLHttpRequest"}, timeout=60).json()
    (d / "riverwatch_station_list_snapshot.json").write_text(json.dumps(lst, indent=1, ensure_ascii=False))
    record(m, d / "riverwatch_station_list_snapshot.json", DHM_LIST, DHM_LICENSE,
           f"snapshot of all {len(lst.get('data', []))} river-watch stations at retrieval time")
    # 2. per-station metadata + attempt to pull the event days directly from DHM
    probe = {"queried_utc": utcnow(), "endpoint": DHM_SERIES, "stations": {}}
    meta_all = {}
    for sid, name in DHM_STATIONS.items():
        csrf, ser, meta = _dhm_station(sess, sid)
        meta_all[sid] = {"requested_name": name, "series_id": ser, "page_url": DHM_PAGE.format(sid),
                         "page_record": meta}
        res = {}
        if csrf and ser:
            for day in ("2026-08-25", "2026-08-26", "2026-08-27"):
                res[day] = len(_dhm_series(sess, csrf, ser, day))
        probe["stations"][sid] = {"name": meta.get("name", name), "samples_returned": res}
    # retention edge on a station that reports today (Narayani at Devghat)
    csrf, ser, _ = _dhm_station(sess, 265)
    today = dt.date.today()
    first = None
    for back in range(0, 60):
        day = (today - dt.timedelta(days=back)).isoformat()
        if _dhm_series(sess, csrf, ser, day):
            first = day
        elif first is not None:
            break
    probe["retention_probe_station"] = 265
    probe["earliest_day_with_data"] = first
    (d / "dhm_archive_probe.json").write_text(json.dumps(probe, indent=1, ensure_ascii=False))
    record(m, d / "dhm_archive_probe.json", DHM_SERIES, DHM_LICENSE,
           "proof that the public endpoint no longer returns 25-27 Aug 2026 (rolling archive)")
    # 3. event-day series from the Acharya package (archived DHM output), NPT -> UTC
    src = json.loads((ROOT / ACHARYA_GAUGES).read_text())
    src_url = "https://doi.org/10.5281/zenodo.22679444 (data/gauge_timeseries.json)"
    stations = []
    w0 = dt.datetime(2026, 8, 26, 0, 0, tzinfo=dt.UTC)
    w1 = dt.datetime(2026, 8, 26, 8, 0, tzinfo=dt.UTC)
    for key, st in src["stations"].items():
        if key.startswith("_"):
            continue
        sid = st["station_id"]
        live = meta_all.get(sid, {}).get("page_record", {})
        rows = []
        for hhmm, val in st["series_26aug"]:
            t = dt.datetime.fromisoformat(f"2026-08-26T{hhmm}:00").replace(tzinfo=NPT)
            tu = t.astimezone(dt.UTC)
            rows.append({"time_npt": t.strftime("%Y-%m-%dT%H:%M%z"), "time_utc": tu.strftime("%Y-%m-%dT%H:%MZ"),
                         "stage_m": val, "in_window_0000_0800_utc": w0 <= tu <= w1})
        slug = key.lower().replace(" ", "_").replace("(", "").replace(")", "")
        out = d / f"{slug}_{sid}_20260826.csv"
        with open(out, "w", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)
        record(m, out, src_url, "CC-BY-4.0 (Acharya & Paudel data package; values from DHM river watch)",
               f"DHM station {sid}; NPT labels converted to UTC (+05:45 offset)")
        last = st.get("last_report_26aug_npt")
        last_utc = (dt.datetime.fromisoformat(f"2026-08-26T{last}:00").replace(tzinfo=NPT)
                    .astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%MZ")) if last else None
        stations.append({
            "corridor_node": key, "station_id": sid, "series_id": st.get("series_id"),
            "dhm_name": st.get("dhm_name"), "dhm_description": st.get("dhm_description"),
            "district": st.get("district"), "station_index": st.get("station_index"),
            "lat": st.get("lat"), "lon": st.get("lon"),
            "warning_level_m": st.get("warning_m"), "danger_level_m": st.get("danger_m"),
            "dhm_page_today": {"lat": live.get("latitude"), "lon": live.get("longitude"),
                               "warning_level": live.get("warning_level"),
                               "danger_level": live.get("danger_level"),
                               "waterLevel": live.get("waterLevel"), "status": live.get("status"),
                               "source_url": DHM_PAGE.format(sid)},
            "samples_26aug": len(rows),
            "last_report_26aug_npt": last, "last_report_26aug_utc": last_utc,
            "last_value_26aug_m": st.get("last_value_26aug_m"),
            "last_value_fraction_of_warning": (round(st["last_value_26aug_m"] / st["warning_m"], 3)
                                               if st.get("warning_m") and st.get("last_value_26aug_m")
                                               and not st.get("reported_through_end_of_day") else None),
            "stopped_transmitting": not st.get("reported_through_end_of_day", True),
            "first_missing_report_npt": (st.get("arrival") or {}).get("by_npt")
            if not st.get("reported_through_end_of_day", True) else None,
            "destroyed_confirmed_by_source": st.get("destroyed_confirmed"),
            "arrival_bracket_npt": ([st["arrival"]["after_npt"], st["arrival"]["by_npt"]]
                                    if st.get("arrival") else None),
            "arrival_basis": (st.get("arrival") or {}).get("basis"),
            "csv": str(out.relative_to(ROOT)),
            "series_source": src_url,
        })
    doc = {"_note": ("Station coordinates and thresholds as archived by Acharya & Paudel from the DHM "
                     "station pages (Sep 2026), with today's DHM page values alongside for comparison. "
                     "The ten-minute series for 26 Aug 2026 are NOT retrievable from DHM any more "
                     "(see dhm_archive_probe.json); they come from the archived copy in the Zenodo "
                     "package. Times in that package are NPT (UTC+05:45)."),
           "series_archive_meta": src.get("_meta"),
           "stations": stations,
           "dhm_station_pages_today": meta_all}
    (d / "stations.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    record(m, d / "stations.json", f"{DHM_PAGE.format('{id}')} + {src_url}", DHM_LICENSE,
           "station metadata and gauge-failure summary")


# --------------------------------------------------------------------------- GEOFON
def fetch_geofon(m: dict) -> None:
    q = "https://geofon.gfz.de/fdsnws/event/1/query?eventid=gfz2026qrfy"
    d = OUT / "geofon"
    get(f"{q}&format=text", d / "gfz2026qrfy.txt", m, "GFZ GEOFON, CC BY 4.0")
    get(f"{q}&format=xml&includeallorigins=true&includeallmagnitudes=true", d / "gfz2026qrfy.xml", m,
        "GFZ GEOFON, CC BY 4.0")


# --------------------------------------------------------------------------- papers + agency reports
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
PAPERS = [
    # (dest, url, licence, note)
    ("papers/acharya/Acharya2026_EarthArXiv_14687_v5.pdf",
     "https://eartharxiv.org/repository/object/14687/download/25908/", "CC-BY-4.0",
     "Acharya & Paudel, EarthArXiv 14687 latest version (v5, last updated 2026-09-10), doi 10.31223/X5HN5H"),
    ("papers/acharya/eartharxiv_view_14687.html", "https://eartharxiv.org/repository/view/14687/",
     "CC-BY-4.0", "landing page with version list"),
    ("papers/kutoglu/Kutoglu2026_RemoteSensing_18_3727.pdf",
     "https://mdpi-res.com/d_attachment/remotesensing/remotesensing-18-03727/article_deploy/remotesensing-18-03727.pdf",
     "CC-BY-4.0", "Kutoglu, Softa & Akgun 2026, Remote Sens. 18(21) 3727, doi 10.3390/rs18213727 "
                  "(www.mdpi.com returns 403 to scripts; mdpi-res.com hosts the same PDF)"),
    ("papers/kutoglu/crossref_rs18213727.json", "https://api.crossref.org/works/10.3390/rs18213727",
     "CC0 (Crossref metadata)", "abstract + metadata"),
    ("papers/researchsquare/Meena2026_rs-11022365_v1.pdf",
     "https://assets-eu.researchsquare.com/files/rs-11022365/v1_covered_4c6582c8-722d-4f29-bbf6-f445eb4cffe5.pdf",
     "CC-BY-4.0", "Meena 2026 preprint, doi 10.21203/rs.3.rs-11022365/v1"),
    ("papers/researchsquare/Meena2026_rs-11022365_zones.csv",
     "https://assets-eu.researchsquare.com/files/rs-11022365/v1/b0dbe1d825f6eb03362df3d2.csv",
     "CC-BY-4.0", "Supplementary Data 1: the 101 validated zones"),
    ("papers/researchsquare/crossref_rs-11022365.json",
     "https://api.crossref.org/works/10.21203/rs.3.rs-11022365/v1", "CC0 (Crossref metadata)", "abstract"),
    ("papers/researchsquare/Hassan2026_rs-11167819_v1.pdf",
     "https://assets-eu.researchsquare.com/files/rs-11167819/v1/0a87c8f5-9dbf-4037-82eb-ac6737c70b5b.pdf",
     "CC-BY-4.0", "Hassan et al. 2026 preprint, doi 10.21203/rs.3.rs-11167819/v1"),
    ("papers/researchsquare/crossref_rs-11167819.json",
     "https://api.crossref.org/works/10.21203/rs.3.rs-11167819/v1", "CC0 (Crossref metadata)", "abstract"),
    ("casualties/NDRRMA_Rasuwa_Flood_SitRep_01_01092026.pdf",
     "https://ndrrma.gov.np/mediafiles/rasuwa/Rasuwa_Flood_SitRep_Temp_ENG_01_01092026.pdf",
     "Government of Nepal public document", "NDRRMA Situation Report #01, 1 Sep 2026 09:00"),
]


def fetch_papers(m: dict) -> None:
    for dest, url, lic, note in PAPERS:
        try:
            get(url, OUT / dest, m, lic, note, headers_override=BROWSER_UA)
        except requests.HTTPError as e:
            m["missing"].append({"item": dest, "url": url, "reason": f"HTTP {e.response.status_code}"})


# --------------------------------------------------------------------------- main
STEPS = {"usgs": fetch_usgs, "zenodo": fetch_zenodo, "unosat": fetch_unosat, "ems": fetch_ems,
         "dhm": fetch_dhm, "geofon": fetch_geofon, "papers": fetch_papers}


def main(argv: list[str]) -> None:
    steps = argv or list(STEPS)
    m = load_manifest()
    for s in steps:
        print(f"[fetch_event] {s}")
        STEPS[s](m)
        save_manifest(m)


if __name__ == "__main__":
    main(sys.argv[1:])
