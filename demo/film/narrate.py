"""Voiceover for the film: one line per beat, spoken by a local neural voice (Kokoro-82M, Apache-2.0), placed at
the beat's start and mixed over the score with the music ducked under the voice. Numbers are read from /data.
Usage: python demo/film/narrate.py  ->  demo/film/mix.wav (needs score.wav and marks.json from render.py/score.py)
"""
import json
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SR = 48000


def data(name):
    return json.loads((ROOT / "data" / name).read_text())


def lines():
    T = json.loads((HERE / "marks.json").read_text())["T"]
    rp, sc, ctx, bt = data("replay.json"), data("scorecard.json"), data("context.json"), data("backtest.json")
    lp = ctx["timing"]["last_pass"]; w = sc["windows"][lp]
    alert = round(rp["alert_min"])
    bg = bt["sites"]["budhigandaki"]["positive_pass"]["result"]
    return [
        (T["s1"] + 0.6, T["s2"], "August 2026. Ice and rock break away above Langtang, Nepal."),
        (T["s2"] + 0.4, T["s3"], f"The flood outruns the warning. The alert comes {alert} minutes later."),
        (T["s3"] + 0.2, T["s4"], "But Nye-sar radar had watched this slope all summer."),
        (T["s4"] + 0.3, T["s5"], "Cascade Watch ranks every moving slope, with a rule fixed in advance."),
        (T["s5"] + 0.2, T["s6"], "Within five kilometres, it flagged a disaster it had never seen."),
        (T["s6"] + 0.2, T["s7"], "Every number traces to its source."),
        (T["s7"] + 0.3, 29.5, "Cascade Watch."),
    ]


def main():
    from kokoro import KPipeline
    from scipy.signal import resample_poly
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    with wave.open(str(HERE / "score.wav")) as w:
        n = w.getnframes(); music = np.frombuffer(w.readframes(n), "<i2").reshape(-1, 2).astype(np.float64) / 32768
    vo = np.zeros(len(music))
    report = []
    prev_end = 0.0
    for start, end, text in lines():
        start = max(start, prev_end + 0.15)             # never overlap the previous line
        room = end - start
        for speed in (1.0, 1.05, 1.1, 1.12):
            a = np.concatenate([x for _, _, x in pipe(text, voice="af_heart", speed=speed)])
            if len(a) / 24000 <= room:
                break
        a = resample_poly(a, 2, 1)                       # 24 kHz -> 48 kHz
        i0 = int(start * SR); i1 = min(len(vo), i0 + len(a))
        vo[i0:i1] += a[: i1 - i0]
        prev_end = start + len(a) / SR
        report.append((round(start, 2), round(len(a) / SR, 2), round(room, 2), speed, "OVER" if len(a) / SR > room else "ok", text))
    vo *= 0.8 / max(1e-9, np.abs(vo).max())
    env = np.abs(vo)
    k = int(0.25 * SR); env = np.convolve(env, np.ones(k) / k, mode="same")
    duck = 1 - 0.55 * np.clip(env / (env.max() * 0.15 + 1e-9), 0, 1)
    mix = music * duck[:, None] * 0.9 + vo[:, None] * 0.95
    mix *= 0.89 / np.abs(mix).max()
    with wave.open(str(HERE / "mix.wav"), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((mix * 32767).astype("<i2").tobytes())
    for r in report:
        print(r)


if __name__ == "__main__":
    main()
