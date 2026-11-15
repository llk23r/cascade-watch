"""Copy the Atlas views into data/atlas/ for the app, with the real overlays for that camera and full provenance
(prompts, seed, Atlas operation ids, camera, depth source). Usage: python demo/atlas/export_for_app.py"""
import json
import shutil
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "data" / "atlas"
OUT.mkdir(parents=True, exist_ok=True)
film = ROOT / "demo" / "film" / "atlas"
for k in ("before", "after"):
    Image.open(film / f"{k}_000.jpg").convert("RGB").resize((1600, 900), Image.LANCZOS).save(OUT / f"{k}.jpg", quality=86)
ov = json.loads((film / "overlays.json").read_text())
f0 = ov["frames"][0]
vis = ov["path_visible"]
outline = [[round(q[0] / 1.2, 1), round(q[1] / 1.2, 1)] for q in f0["outline"] if q]
cen = [sum(q[0] for q in outline) / len(outline), sum(q[1] for q in outline) / len(outline)]
pts = [(i, q) for i, q in enumerate(f0["path"]) if q and vis[i]]
k0 = min(range(len(pts)), key=lambda j: (pts[j][1][0] / 1.2 - cen[0]) ** 2 + (pts[j][1][1] / 1.2 - cen[1]) ** 2)
path = []
for j in range(k0, len(pts)):
    if j > k0 and pts[j][0] != pts[j - 1][0] + 1:
        break
    path.append([round(pts[j][1][0] / 1.2, 1), round(pts[j][1][1] / 1.2, 1)])
o = lambda f: json.loads((HERE / "out" / f).read_text())
meta = o("close_nw_meta.json")
req_b, req_a = o("req_close_before.json"), o("req_close_after2.json")
view = dict(
    width=1600, height=900, outline=outline, path=path,
    label="AI visualization, not a photograph",
    how=("World Labs Atlas (atlasChisel) painted the appearance of the real terrain: depth rendered from the Copernicus DEM for this camera. "
         "The 'after' view is an Atlas inpaint limited to the UNOSAT detachment zone and the routed flood corridor. "
         "The yellow outline (UNOSAT 4257) and the red route (D8 flow routing on the DEM) are data, projected with the same camera."),
    camera=meta["camera"], camera_lonlat_alt=meta["camera_lonlat_alt"], look_at=meta["look_at_lonlat"], depth_source=meta["source"],
    atlas=dict(task="atlasChisel", seed=req_b["modelParameters"]["seed"],
               before=dict(operation=o("op_close_before.json")["id"], prompt=req_b["prompt"]),
               after=dict(operation=o("op_close_after2.json")["id"], prompt=req_a["prompt"], mask="UNOSAT outline + routed flood corridor (300 m wide) projected into this camera"),
               ),
)
(OUT / "view.json").write_text(json.dumps(view, indent=1))
print("data/atlas written:", len(outline), "outline pts,", len(path), "path pts")
