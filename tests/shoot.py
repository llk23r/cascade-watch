"""Headless screenshots of every chapter, plus console errors. Usage: python tests/shoot.py OUTDIR [WIDTHxHEIGHT]"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
w, h = (int(v) for v in (sys.argv[2] if len(sys.argv) > 2 else "1440x900").split("x"))
url = "http://127.0.0.1:8765/index.html"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
    pg.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.goto(url, wait_until="networkidle")
    time.sleep(6)
    pg.screenshot(path=str(out / "c1.png"))
    for ch in (2, 3, 4, 5):
        pg.keyboard.press(str(ch))
        time.sleep(4.5)
        if ch == 4:
            pg.click("#play")
            time.sleep(9)
        pg.screenshot(path=str(out / f"c{ch}.png"))
    pg.keyboard.press("3"); time.sleep(3)
    pg.click('.layerbar button[data-layer="radar"]'); time.sleep(3)
    pg.screenshot(path=str(out / "radar.png"))
    b.close()
print("\n".join(errors[:40]) or "no console errors")
