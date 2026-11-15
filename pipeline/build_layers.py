"""Stage 5b — Vector context layers for the map (real sources only, simplified for the web).

  data/rivers.geojson     OSM rivers + named streams in the corridor (English names)
  data/glaciers.geojson   glacier outlines in the study box (RGI/GLIMS, see raw/ancillary/manifest.json)
  data/observed.geojson   UNOSAT flood/mudslide extent, affected buildings and roads, barrier lakes
"""
from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import mapping, shape

from grid import DATA, RAW, find
from hydro import english_name


def write(name: str, feats: list) -> None:
    (DATA / name).write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    print(name, len(feats))


def simplify_geom(g: dict, tol: float) -> dict:
    s = shape(g)
    s = s.simplify(tol, preserve_topology=True)
    return json.loads(json.dumps(mapping(s), default=float), parse_float=lambda v: round(float(v), 5))


def rivers() -> None:
    p = find("osm_waterways.geojson")
    feats = []
    for ft in json.loads(Path(p).read_text())["features"]:
        pr = ft.get("properties") or {}
        nm = english_name(pr)
        if pr.get("waterway") == "river" or nm:
            feats.append({"type": "Feature", "properties": {"name": nm, "waterway": pr.get("waterway")},
                          "geometry": simplify_geom(ft["geometry"], 0.0002)})
    write("rivers.geojson", feats)


def glaciers() -> None:
    p = find("glaciers.geojson")
    feats = [{"type": "Feature", "properties": {}, "geometry": simplify_geom(ft["geometry"], 0.0002)}
             for ft in json.loads(Path(p).read_text())["features"]]
    write("glaciers.geojson", feats)


def observed() -> None:
    base = RAW / "event" / "unosat" / "4257" / "geojson"
    layers = {
        "UNOSAT_Multisensor_20260826_20260828_FloodExtent.geojson": "flood",
        "UNOSAT_Multisensor_20260826_20260828_AffectedBuildings.geojson": "building",
        "UNOSAT_Multisensor_20260826_20260828_AffectedRoads.geojson": "road",
        "UNOSAT_Cartosat3_20260828_BarrierLakes.geojson": "lake",
    }
    feats = []
    for fn, kind in layers.items():
        f = base / fn
        if not f.exists():
            continue
        for ft in json.loads(f.read_text())["features"]:
            if not ft.get("geometry"):
                continue
            g = ft["geometry"] if ft["geometry"]["type"] == "Point" else simplify_geom(ft["geometry"], 0.00008)
            feats.append({"type": "Feature", "properties": {"kind": kind}, "geometry": g})
    write("observed.geojson", feats)


if __name__ == "__main__":
    rivers()
    glaciers()
    observed()
