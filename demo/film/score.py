"""Original score for the 30-second film, synthesised here (no samples, no licensed music).
Cues are timed to the film's marks (marks.json is written by render.py from the page itself).

Usage: python demo/film/score.py  ->  demo/film/score.wav
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
from scipy import signal

HERE = Path(__file__).resolve().parent
SR = 48000
DUR = 29.9
N = int(SR * DUR)
t = np.arange(N) / SR
rng = np.random.default_rng(2608)
L = np.zeros(N); R = np.zeros(N)


def env(start, attack, hold, release, curve=2.0):
    e = np.zeros(N)
    a0, a1, h1, r1 = start, start + attack, start + attack + hold, start + attack + hold + release
    m = (t >= a0) & (t < a1); e[m] = ((t[m] - a0) / max(attack, 1e-4)) ** curve
    m = (t >= a1) & (t < h1); e[m] = 1
    m = (t >= h1) & (t < r1); e[m] = (1 - (t[m] - h1) / max(release, 1e-4)) ** curve
    return e


def add(x, pan=0.0, gain=1.0):
    global L, R
    pan = np.clip(pan, -1, 1)
    L += x * gain * np.sqrt(0.5 * (1 - pan)); R += x * gain * np.sqrt(0.5 * (1 + pan))


def tone(f, detune=0.0, harm=(1.0, 0.18, 0.06)):
    ph = 2 * np.pi * f * (1 + detune) * t
    return sum(a * np.sin((k + 1) * ph + k * 0.7) for k, a in enumerate(harm))


def pad(freqs, start, attack, hold, release, gain=0.05, pan_spread=0.5):
    e = env(start, attack, hold, release, 1.6)
    lfo = 1 + 0.12 * np.sin(2 * np.pi * 0.17 * t + start)
    for i, f in enumerate(freqs):
        p = (i / max(len(freqs) - 1, 1) - 0.5) * 2 * pan_spread
        add((tone(f, -0.0025) + tone(f, 0.0025)) * e * lfo, p, gain / len(freqs) ** 0.5)


def lowpass(x, fc, order=2):
    b, a = signal.butter(order, fc / (SR / 2), "low"); return signal.lfilter(b, a, x)


def bandpass(x, f0, f1, order=2):
    b, a = signal.butter(order, [f0 / (SR / 2), f1 / (SR / 2)], "band"); return signal.lfilter(b, a, x)


def impact(at, gain=0.5):
    tt = np.clip(t - at, 0, None); m = t >= at
    f = 30 + 32 * np.exp(-tt * 2.2)
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 1.4) * m
    rum = lowpass(rng.standard_normal(N), 140, 3) * np.exp(-tt * 0.9) * m * 3
    add(sub * gain); add(rum * gain * 0.35, -0.2); add(np.roll(rum, 900) * gain * 0.35, 0.2)


def thump(at, gain=0.25):
    tt = np.clip(t - at, 0, None); m = (t >= at) & (t < at + 0.35)
    add(np.sin(2 * np.pi * 58 * tt) * np.exp(-tt * 18) * m * gain)


def click(at, gain=0.05, f=2100, pan=0.0):
    tt = np.clip(t - at, 0, None); m = (t >= at) & (t < at + 0.06)
    add(np.sin(2 * np.pi * f * tt) * np.exp(-tt * 90) * m, pan, gain)


def bell(at, freqs, gain=0.08, decay=1.8, pan=0.0):
    tt = np.clip(t - at, 0, None); m = t >= at
    x = sum(np.sin(2 * np.pi * f * tt) * np.exp(-tt * decay * (1 + i * 0.6)) * (0.6 ** i) for i, f in enumerate(freqs))
    add(x * m * (1 - np.exp(-tt * 300)), pan, gain)


def whoosh(at, dur, gain=0.06, up=True):
    tt = (t - at) / dur; m = (tt >= 0) & (tt <= 1)
    n = rng.standard_normal(N)
    e = np.where(m, np.sin(np.pi * np.clip(tt, 0, 1)) ** 2, 0)
    hi = bandpass(n, 900, 5000, 2); lo = bandpass(n, 200, 1200, 2)
    mix = np.where(m, (tt if up else 1 - tt), 0)
    add((hi * mix + lo * (1 - mix)) * e, (tt - 0.5) * 0.6 if up else 0, gain)


def cut(at, gain=0.09):
    tt = np.clip(t - at, 0, None); m = (t >= at) & (t < at + 0.25)
    add(bandpass(rng.standard_normal(N), 300, 2500) * np.exp(-tt * 30) * m, 0.1, gain)
    add(np.sin(2 * np.pi * 92 * tt) * np.exp(-tt * 9) * m, 0, gain * 1.6)


def sparkle(a, b, count, gain=0.012):
    for k in range(count):
        at = a + (b - a) * (k / count) + rng.uniform(0, (b - a) / count)
        bell(at, [rng.uniform(1300, 3200)], gain=gain * rng.uniform(0.5, 1), decay=7, pan=rng.uniform(-0.8, 0.8))


def main():
    mk = json.loads((HERE / "marks.json").read_text())
    T = mk["T"]
    A2, C3, E3, G3, A3, B3, C4, D4, E4, F2, G2, D3, B4, D5, E5, G5 = 110, 130.81, 164.81, 196, 220, 246.94, 261.63, 293.66, 329.63, 87.31, 98, 146.83, 493.88, 587.33, 659.25, 783.99

    # 1 · opening: airy A-minor add9 over the valley; a low impact when the flood starts
    pad([A2, E3, C4, B4], 0.0, 2.2, 2.6, 1.4, gain=0.055)
    sparkle(1.5, 2.6, 6, 0.008)                         # the outline drawing in
    impact(2.95, 0.42)
    # 2 · the race: heartbeat and a held minor-second tension, gauge cuts, the SMS chime
    pad([A2, 116.54, E3], T["s2"] - 0.4, 1.0, 4.4, 0.6, gain=0.04, pan_spread=0.3)
    beat = 0.8
    k = T["s2"] + 0.25
    while k < T["s3"] - 0.3:
        thump(k, 0.18 + 0.12 * (k - T["s2"]) / 5); thump(k + 0.22, 0.10 + 0.08 * (k - T["s2"]) / 5); k += beat
    for g in mk["gauges"]:
        if g < T["s3"]:
            cut(g)
    bell(mk["sms"], [E5 * 2, B4 * 2], gain=0.07, decay=2.2, pan=0.15)
    bell(mk["sms"] + 0.16, [B4 * 2, E5 * 2], gain=0.05, decay=2.2, pan=-0.15)
    # 3 · the watcher: a riser, then a radar shimmer and one tick per pass
    whoosh(T["s3"] - 0.45, 0.6, 0.05)
    pad([E3, B3, E4, G5 * 0.5], T["s3"], 0.6, 3.0, 0.6, gain=0.045)
    for c in mk["chips"]:
        click(c + 0.05, 0.05, 2400, 0.3)
    tr = (t >= T["s3"] + 0.2) & (t < T["s4"]); sh = np.sin(2 * np.pi * 1318.5 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 6 * t)) * tr
    add(sh * env(T["s3"] + 0.2, 0.6, 2.6, 0.6), 0.4, 0.012)
    # 4 · the tool: hopeful F-major 7, sparkle as the patches appear, whooshes for the sort and the window
    pad([F2, C3, A3, E4], T["s4"] - 0.2, 0.8, 5.0, 0.8, gain=0.055)
    sparkle(T["s4"] + 0.35, T["s4"] + 1.2, 22, 0.01)
    whoosh(15.25, 1.1, 0.05)
    bell(16.2, [G5, D5 * 2], gain=0.03, decay=3)
    whoosh(18.05, 0.9, 0.05, up=False)
    # 5 · the finding: C-major lift, a clear chime on the rank, a muted thud on the miss
    pad([C3, G3, E4, D5], T["s5"] - 0.1, 0.5, 4.2, 0.6, gain=0.06)
    bell(T["s5"] + 0.25, [G5, D5 * 2, G5 * 2], gain=0.07, decay=1.6)
    thump(T["s5"] + 2.62, 0.22); add(lowpass(rng.standard_normal(N), 300) * env(T["s5"] + 2.62, 0.01, 0.05, 0.4), 0, 0.05)
    # 6 · built on: warm G
    pad([G2, D3, B3, A3 * 2], T["s6"] - 0.1, 0.6, 2.6, 0.5, gain=0.05)
    for i in range(6):
        click(T["s6"] + 0.3 + i * 0.12, 0.025, 1800 + 150 * i, -0.3 + 0.12 * i)
    # 7 · end: resolve to C major, shimmer, fade
    pad([C3, G3, C4, E4, G5 * 0.5], T["s7"] - 0.2, 0.9, 1.0, 1.3, gain=0.07)
    bell(T["s7"] + 0.2, [C4 * 4, G5 * 2], gain=0.04, decay=1.2)

    # space: a soft synthetic hall (decaying, low-passed noise impulse), then master
    ir_n = int(SR * 2.4); it = np.arange(ir_n) / SR
    irL = lowpass(rng.standard_normal(ir_n), 6000) * np.exp(-it * 2.6); irR = lowpass(rng.standard_normal(ir_n), 6000) * np.exp(-it * 2.6)
    irL /= np.abs(irL).sum() ** 0.5 * 8; irR /= np.abs(irR).sum() ** 0.5 * 8
    wl = signal.fftconvolve(L, irL)[:N]; wr = signal.fftconvolve(R, irR)[:N]
    outL, outR = L * 0.8 + wl * 0.5, R * 0.8 + wr * 0.5
    fade = np.clip(t / 0.4, 0, 1) * np.clip((DUR - t) / 0.6, 0, 1)
    st = np.stack([outL, outR], 1) * fade[:, None]
    st = np.tanh(st * 1.6) / 1.6
    st *= 0.89 / np.abs(st).max()                          # peak -1 dBFS
    pcm = (st * 32767).astype("<i2")
    with wave.open(str(HERE / "score.wav"), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    print("score.wav", f"{DUR}s")


if __name__ == "__main__":
    main()
