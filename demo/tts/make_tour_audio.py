"""Pre-record the guided tour's narration with a local neural voice (Kokoro-82M, Apache-2.0).

1. Reads the tour text exactly as the page builds it from /data (via a headless browser), so numbers match.
2. Normalises it for speech (times, units, acronyms) without changing the caption text.
3. Writes data/tour/step_NN.m4a and data/tour/manifest.json with a hash of each caption: the page plays a file only
   when its hash matches the caption on screen.
Needs the project served on port 8765. Usage: python demo/tts/make_tour_audio.py [voice]
"""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "tour"
OUT.mkdir(parents=True, exist_ok=True)
VOICE = sys.argv[1] if len(sys.argv) > 1 else "af_heart"


def script():
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto("http://127.0.0.1:8765/index.html?intro=0", wait_until="networkidle")
        for _ in range(80):
            if pg.evaluate("window.CW && CW.ready()"):
                break
            time.sleep(0.5)
        time.sleep(2)
        s = pg.evaluate("CWTour.script()")
        b.close()
    return s


def spoken(t):
    t = re.sub(r"\b0?(\d{1,2}):(\d{2})(?::\d{2})?\b", lambda m: f"{int(m.group(1))}:{m.group(2)}", t)
    t = re.sub(r"(\d)\s?km\b", r"\1 kilometres", t)
    t = re.sub(r"(\d)\s?m\b", r"\1 metres", t)
    t = t.replace("NISAR", "Nye-sar").replace("UNOSAT", "U.N.O.SAT").replace("ASF", "A.S.F.").replace("ISRO", "Isro").replace("ERA5", "E.R.A. five")
    t = t.replace("→", "to").replace("’", "'")
    return t


def main():
    from kokoro import KPipeline
    steps = script()
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    man = {"voice": VOICE, "engine": "Kokoro-82M (Apache-2.0), local", "steps": []}
    for f in OUT.glob("step_*.m4a"):
        f.unlink()
    for s in steps:
        audio = np.concatenate([a for _, _, a in pipe(spoken(s["say"]), voice=VOICE, speed=1.0)])
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            sf.write(tmp.name, audio, 24000)
            name = f"step_{s['i']:02d}.m4a"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp.name, "-af", "loudnorm=I=-18:TP=-1.5", "-c:a", "aac", "-b:a", "80k", "-ac", "1", str(OUT / name)], check=True)
        man["steps"].append({"i": s["i"], "title": s["title"], "hash": s["hash"], "file": name, "seconds": round(len(audio) / 24000, 2)})
        print(name, s["title"], f"{len(audio) / 24000:.1f}s", flush=True)
    (OUT / "manifest.json").write_text(json.dumps(man, indent=1))
    print("total", round(sum(x["seconds"] for x in man["steps"]), 1), "s")


if __name__ == "__main__":
    main()
