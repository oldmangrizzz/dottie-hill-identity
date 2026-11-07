#!/usr/bin/env python3
"""loop_rate.py -- measure lfm3's thinking-loop rate, the metric weight-space editing targets.

Takehi et al. 2026 ("Sparks of Weight-Space Programming", MIT CSAIL) report that Qwen3.5-0.8B hit
its 32,768-token generation limit on 93.0% of MATH-500 items, and that a 0.016% weight edit --
subtracting one direction from 64 columns of two MLP down-projections -- dropped that to 33.1%.
576 of the 579 newly-correct problems were previously TRUNCATED, not previously wrong. Their
conclusion: the edit removed interference, it did not add reasoning ability.

That distinction matters here because Dottie's honesty gates forbid claiming added ability. So the
only admissible metric is: did the model FINISH instead of looping?

This harness establishes the baseline that any intervention must beat. It measures, per item:
  truncated  -- finish_reason == "length" (the loop consumed the whole budget)
  empty      -- content empty after reasoning (nothing was ever emitted)
  loop_score -- highest repeated n-gram share in the reasoning trace

Usage: loop_rate.py [--budget N] [--items N] [--url ...] [--model ...]
Exit code is always 0: this is a measurement, not a gate.
"""
import argparse
import json
import re
import urllib.request

PROMPTS = [
    "What is 47 times 19? Answer with just the number.",
    "If a train travels 60 km in 45 minutes, what is its speed in km/h? Answer with just the number.",
    "Name the capital of Australia. One word only.",
    "What is 12% of 250? Answer with just the number.",
    "Which is larger, 7/8 or 5/6? Answer with just the fraction.",
    "A farmer has 17 sheep and all but 9 run away. How many remain? Answer with just the number.",
    "What is the next prime after 29? Answer with just the number.",
    "Reply with exactly: LOOP FREE",
]


def call(url, model, prompt, budget):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": budget,
        "stream": False,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read().decode())
    ch = d["choices"][0]
    msg = ch.get("message") or {}
    return {
        "finish": ch.get("finish_reason"),
        "content": msg.get("content") or "",
        "reasoning": msg.get("reasoning_content") or "",
        "tokens": (d.get("usage") or {}).get("completion_tokens"),
    }


def loop_score(text):
    """Share of 8-grams that repeat. A tight loop approaches 1.0."""
    t = re.sub(r"\s+", " ", text or "")
    if len(t) < 32:
        return 0.0
    grams = [t[i:i + 8] for i in range(len(t) - 8)]
    if not grams:
        return 0.0
    return round(1.0 - len(set(grams)) / len(grams), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=512)
    ap.add_argument("--items", type=int, default=8)
    ap.add_argument("--url", default="http://127.0.0.1:8488/v1/chat/completions")
    ap.add_argument("--model", default="lfm3")
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    rows = []
    for p in PROMPTS[:a.items]:
        try:
            r = call(a.url, a.model, p, a.budget)
        except Exception as exc:
            rows.append({"prompt": p, "error": "%s: %s" % (type(exc).__name__, exc)})
            print("ERR ", p[:44], type(exc).__name__, flush=True)
            continue
        row = {
            "prompt": p,
            "finish": r["finish"],
            "truncated": r["finish"] == "length",
            "empty": r["content"].strip() == "",
            "loop_score": loop_score(r["reasoning"]) if r["reasoning"] else loop_score(r["content"]),
            "tokens": r["tokens"],
        }
        rows.append(row)
        print("%-5s trunc=%-5s empty=%-5s loop=%-6s tok=%-5s %s"
              % ("TRUNC" if row["truncated"] else "ok", row["truncated"], row["empty"],
                 row["loop_score"], row["tokens"], p[:44]), flush=True)

    ok = [r for r in rows if "error" not in r]
    if ok:
        n = len(ok)
        trunc = sum(1 for r in ok if r["truncated"])
        empty = sum(1 for r in ok if r["empty"])
        print()
        print("BASELINE  budget=%d  items=%d" % (a.budget, n))
        print("  truncated      = %d/%d  (%.1f%%)   <- the paper's headline metric"
              % (trunc, n, 100.0 * trunc / n))
        print("  empty content  = %d/%d  (%.1f%%)" % (empty, n, 100.0 * empty / n))
        print("  mean loop_score= %.3f" % (sum(r["loop_score"] for r in ok) / n))
        print("  mean tokens    = %.1f" % (sum(r["tokens"] or 0 for r in ok) / n))
        if a.json_out:
            with open(a.json_out, "w") as f:
                json.dump({"budget": a.budget, "rows": rows}, f, indent=2)
            print("  wrote", a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
