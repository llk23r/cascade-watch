"""Record a 30-second tour of the app (Space Apps demo limit: 30 s). Usage: python tests/record_demo.py"""
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo"
OUT.mkdir(exist_ok=True)
tmp = OUT / "_raw"
shutil.rmtree(tmp, ignore_errors=True)

with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(tmp), record_video_size={"width": 1440, "height": 900})
    pg = ctx.new_page()
    t0 = time.time()
    pg.goto("http://127.0.0.1:8765/index.html", wait_until="networkidle")
    time.sleep(5)                      # map settles (trimmed from the start below)
    lead = time.time() - t0
    # 1 · the morning: run the wave
    pg.keyboard.press("4"); time.sleep(2.2)
    pg.click("#play"); time.sleep(7.5)
    # 2 · the creep: real NISAR motion, our numbers vs Meena
    pg.keyboard.press("2"); time.sleep(4.5)
    # 3 · the haystack: rules on/off re-rank live
    pg.keyboard.press("3"); time.sleep(3.0)
    pg.click('label[for="r-sigma"]'); time.sleep(1.2)
    pg.click('label[for="r-sigma"]'); time.sleep(1.2)
    # radar before/after: the avalanche path through monsoon cloud
    pg.click('.layerbar button[data-layer="radar"]'); time.sleep(1.0)
    pg.evaluate("""() => { const s = document.getElementById('rc-slider'); let v = 0;
      const id = setInterval(() => { v += 0.04; s.value = Math.min(1, v); s.dispatchEvent(new Event('input')); if (v >= 1) clearInterval(id); }, 60); }""")
    time.sleep(3.0)
    # 5 · the answer
    pg.keyboard.press("5"); time.sleep(4.5)
    total = time.time() - t0
    vid = pg.video.path()
    ctx.close(); b.close()

dur = min(30.0, total - lead)
mp4 = OUT / "cascade-watch-30s.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{lead:.2f}", "-i", vid, "-t", f"{dur:.2f}",
                "-vf", "fps=30,scale=1440:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-movflags", "+faststart", str(mp4)], check=True)
shutil.rmtree(tmp, ignore_errors=True)
print(mp4, f"{dur:.1f}s")
