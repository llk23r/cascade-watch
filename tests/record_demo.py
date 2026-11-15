"""Record a 30-second tour of the app (Space Apps demo limit: 30 s).
Usage: python tests/record_demo.py [light|dark]"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo"
OUT.mkdir(exist_ok=True)
tmp = OUT / "_raw"
shutil.rmtree(tmp, ignore_errors=True)
scheme = sys.argv[1] if len(sys.argv) > 1 else "light"


def slide(pg, sel, to, step=0.04, ms=50):
    """Drag a range input smoothly to `to` (0..1 of its range)."""
    pg.evaluate("""([sel, to, step, ms]) => { const s = document.querySelector(sel); const lo = +s.min, hi = +s.max;
      let v = (+s.value - lo) / (hi - lo); const dir = to > v ? 1 : -1;
      const id = setInterval(() => { v += dir * step; if ((dir > 0 && v >= to) || (dir < 0 && v <= to)) { v = to; clearInterval(id); }
        s.value = lo + v * (hi - lo); s.dispatchEvent(new Event('input')); }, ms); }""", [sel, to, step, ms])


with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme,
                        record_video_dir=str(tmp), record_video_size={"width": 1440, "height": 900})
    pg = ctx.new_page()
    t0 = time.time()
    pg.goto("http://127.0.0.1:8765/index.html?intro=1", wait_until="networkidle")
    time.sleep(6)                      # map settles behind the welcome sheet (trimmed below)
    lead = time.time() - t0 - 2.6      # keep the last 2.6 s of the welcome sheet: the question
    # 1 · the morning: start the replay, the wave runs the river against the alert
    pg.click("#w-start"); time.sleep(8.0)
    # 2 · the creep: NISAR motion per pass, our numbers vs Meena
    pg.keyboard.press("2"); time.sleep(4.2)
    # 3 · the haystack: switch a rule off and on, the ranking re-sorts live
    pg.keyboard.press("3"); time.sleep(2.8)
    pg.click('label[for="r-sigma"]'); time.sleep(1.3)
    pg.click('label[for="r-sigma"]'); time.sleep(1.1)
    # radar before/after: the avalanche path through monsoon cloud
    pg.click('.layerbar button[data-layer="radar"]'); time.sleep(0.6)
    pg.evaluate("() => { const s = document.getElementById('rc-slider'); s.value = 0; s.dispatchEvent(new Event('input')); }")
    time.sleep(0.4)
    slide(pg, "#rc-slider", 1.0); time.sleep(2.4)
    # 5 · the answer
    pg.keyboard.press("5"); time.sleep(4.0)
    total = time.time() - t0
    vid = pg.video.path()
    ctx.close(); b.close()

dur = min(30.0, total - lead)
mp4 = OUT / "app-screen-tour.mp4"   # the film itself is built by demo/film/render.py
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{lead:.2f}", "-i", vid, "-t", f"{dur:.2f}",
                "-vf", "fps=30,scale=1440:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-movflags", "+faststart", str(mp4)], check=True)
shutil.rmtree(tmp, ignore_errors=True)
print(mp4, f"{dur:.1f}s")
