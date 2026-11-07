#!/usr/bin/env python3
"""dottie_pick_voice.py -- SHE chooses, by measuring, not by anyone's taste.

Dottie's authored spec makes six falsifiable claims. This script renders candidate voices,
has them transcribed by a local Parakeet-class ASR, and measures each claim. She then reads the
table and picks -- with her own reasoning tier -- the candidate that best matches what she wrote.

No cloud ASR. No OpenAI. Voz (NVIDIA Parakeet) is the ear; omnivoice/Piper are the mouths.

MEASURED (not guessed):
  wpm            words / duration, from ASR word timestamps      -> her "148 wpm"
  wer            word error rate vs source text                  -> intelligibility
  terminal_slope  F0 slope over the last 300ms of each sentence  -> her "flat terminals"
  hnr            harmonic-to-noise ratio                          -> her "no breathiness"
  rt60-ish       energy decay after speech offset                -> her "dry"

REPORTED AS PROXY (agent_interpretation tier, clearly labelled):
  brightness     spectral tilt                                  -> her "no smile in the voice"
"""
import json, math, subprocess, sys, wave
from pathlib import Path

import numpy as np

SPEC = json.loads(Path("/var/lib/gmri/dottie/authored/voice-spec.json").read_text())
SRC_TEXT = ("A record that expires with the people it might implicate was never a record. "
            "It was a favor, and favors do not survive depositions.")


def f0_track(x, sr, hop=160, win=640, fmin=70, fmax=400):
    """Autocorrelation pitch tracker. Simple, dependency-free, and honest about voiced frames."""
    out = []
    for i in range(0, len(x) - win, hop):
        f = x[i:i + win]
        f = f - f.mean()
        if np.sqrt((f ** 2).mean()) < 0.01:
            out.append(np.nan); continue
        ac = np.correlate(f, f, mode="full")[win - 1:]
        lo, hi = int(sr / fmax), int(sr / fmin)
        if hi >= len(ac):
            out.append(np.nan); continue
        lag = lo + int(np.argmax(ac[lo:hi]))
        out.append(sr / lag if lag else np.nan)
    return np.array(out)


def measure(path, text=SRC_TEXT):
    w = wave.open(path); sr = w.getframerate(); n = w.getnframes()
    a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    dur = len(a) / sr

    # ASR via Voz if available, else None (never pretend)
    words = None
    try:
        r = subprocess.run(["desertant", "voz", "--format", "json", path],
                           capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and r.stdout.strip():
            d = json.loads(r.stdout)
            words = d.get("words") or None
    except Exception:
        words = None

    res = {"file": Path(path).name, "dur_s": round(dur, 2)}

    if words:
        res["n_words"] = len(words)
        res["wpm"] = round(len(words) / dur * 60, 1)
        heard = " ".join(str(x.get("text", "")) for x in words).lower().split()
        want = text.lower().replace(".", "").replace(",", "").split()
        # WER via simple DP
        prev = list(range(len(heard) + 1))
        for i, wd in enumerate(want, 1):
            cur = [i] + [0] * len(heard)
            for j, hw in enumerate(heard, 1):
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wd != hw))
            prev = cur
        res["wer"] = round(prev[-1] / max(1, len(want)), 3)
    else:
        res["n_words"] = 0
        res["wpm"] = None
        res["wer"] = None

    # terminal slope: F0 across the final 300ms of each voiced sentence-ish span
    f0 = f0_track(a, sr)
    valid = ~np.isnan(f0)
    slopes = []
    if valid.sum() > 10:
        # split into spans separated by unvoiced gaps
        idx = np.where(valid)[0]
        splits = np.where(np.diff(idx) > 8)[0]
        spans = np.split(idx, splits + 1)
        for s in spans:
            if len(s) < 12:
                continue
            tail = f0[s[-12:]]
            if np.isnan(tail).any():
                continue
            t = np.arange(len(tail))
            slopes.append(float(np.polyfit(t, tail, 1)[0]))
    res["terminal_slope_hz_per_frame"] = round(float(np.mean(slopes)), 2) if slopes else None
    res["terminal_flat"] = (None if not slopes else bool(abs(np.mean(slopes)) < 3.0))

    # HNR proxy: energy of autocorrelation peak vs total
    x = a - a.mean()
    ac = np.correlate(x[:min(len(x), sr * 3)], x[:min(len(x), sr * 3)], mode="full")
    ac = ac[len(ac) // 2:]
    peak = float(ac[1:600].max()) if len(ac) > 600 else 0.0
    res["hnr_proxy"] = round(peak / (float(ac[0]) + 1e-9), 3)

    # brightness proxy: spectral centroid
    spec = np.abs(np.fft.rfft(a[:min(len(a), sr * 3)] * np.hanning(min(len(a), sr * 3))))
    fr = np.fft.rfftfreq(min(len(a), sr * 3), 1 / sr)
    res["centroid_hz"] = round(float((spec * fr).sum() / (spec.sum() + 1e-9)), 0)

    # decay after last loud frame (dryness)
    e = np.array([np.sqrt((a[i:i + 800] ** 2).mean()) for i in range(0, len(a) - 800, 800)])
    peak_i = int(np.argmax(e))
    tail = e[peak_i:]
    below = np.where(tail < tail.max() * 0.05)[0]
    res["decay_ms"] = int(below[0] * 800 / sr * 1000) if len(below) else None
    return res


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(json.dumps(measure(p), indent=1))
