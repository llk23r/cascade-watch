"""Render the film frame by frame (deterministic, 30 fps), synthesise the score, mux to H.264 + AAC.
Needs the project served at http://127.0.0.1:8765 (python3 -m http.server 8765 from the project root).

Usage: python demo/film/render.py  ->  demo/cascade-watch-30s.mp4 (+ poster frame)
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FPS = 30
URL = "http://127.0.0.1:8765/demo/film/index.html"
frames = HERE / "_frames"
shutil.rmtree(frames, ignore_errors=True); frames.mkdir()

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--force-color-profile=srgb"])
    pg = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    pg.goto(URL, wait_until="networkidle")
    for _ in range(120):
        if pg.evaluate("window.FILM && FILM.ready"):
            break
        time.sleep(0.5)
    dur = pg.evaluate("FILM.duration")
    marks = pg.evaluate("Object.assign({T: FILM.T}, FILM.MARKS)")
    (HERE / "marks.json").write_text(json.dumps(marks, indent=1))
    n = int(round(dur * FPS))
    # warm the atlas image cache so frame swaps never show a blank
    pg.evaluate("Promise.all(Array.from({length: 150}, (_, i) => ['before','after'].map(k => new Promise(ok => { const im = new Image(); im.onload = im.onerror = ok; im.src = 'atlas/' + k + '_' + String(i).padStart(3, '0') + '.jpg'; }))).flat())")
    t0 = time.time()
    for i in range(n):
        pg.evaluate(f"FILM.render({i / FPS})")
        pg.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        pg.screenshot(path=str(frames / f"f{i:04d}.jpg"), type="jpeg", quality=93)
        if i % 90 == 0:
            print(f"frame {i}/{n}  {time.time() - t0:.0f}s", flush=True)
    b.close()

subprocess.run([sys.executable, str(HERE / "score.py")], check=True)
out = ROOT / "demo" / "cascade-watch-30s.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "f%04d.jpg"), "-i", str(HERE / "score.wav"),
                "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-t", f"{dur:.2f}", "-movflags", "+faststart", str(out)], check=True)
shutil.copy(frames / f"f{int(1.2 * FPS):04d}.jpg", ROOT / "demo" / "poster.jpg")
shutil.rmtree(frames, ignore_errors=True)
print(out)
