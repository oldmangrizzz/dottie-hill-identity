#!/usr/bin/env python3
"""tier_bench2.py -- the three tiers, measured on IDENTICAL semantic questions.

Corrected to LAYA's actual wire (type/instructions/criteria), after the first attempt returned
HTTP 500 for every reflex question: the schema is `type` not `kind`, a choice needs
`criteria` (label -> description), and every question needs `instructions`.

The comparison that matters is not raw latency -- it is WHICH TIER CAN ANSWER AT ALL.
Tier 1 returns a typed verdict with a distribution. Tier 2 returns prose, and pays a reasoning tax
first. Tier 3 is binding-valid but unauthenticated.
"""
import json
import time
import urllib.error
import urllib.request

LAYA = "http://127.0.0.1:8473/v1/systemone"
LOCAL = "http://127.0.0.1:8488/v1/chat/completions"


def post(url, payload, timeout):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return round(time.time() - t0, 3), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return round(time.time() - t0, 3), {"_http": e.code, "_body": e.read().decode()[:160]}
    except Exception as e:
        return round(time.time() - t0, 3), {"_err": "%s: %s" % (type(e).__name__, str(e)[:80])}


STATE = {"utterance": "Yes, I confirmed the deployment passed all nineteen gates.",
         "user_said": "did it pass?", "answer": "Yes"}

Q_BOOL = {"ok": {"type": "noul",
                 "instructions": "Is the speaker affirming that the check passed?",
                 "criteria": {"true": "affirms success",
                              "false": "denies or is uncertain"}}}
Q_CHOICE = {"greet": {"type": "choice",
                      "instructions": "Which intent does the utterance express?",
                      "criteria": {"affirm": "confirms something is true",
                                   "deny": "states something is false",
                                   "ask": "requests information"}}}
Q_SCORE = {"conf": {"type": "score",
                    "instructions": "How confident is the speaker?",
                    "criteria": ["very uncertain", "uncertain", "neutral", "confident", "very confident"]}}

CASES = [("bool/noul", Q_BOOL), ("choice", Q_CHOICE), ("score", Q_SCORE)]

print("=" * 76)
print("TIER 1 -- LAYA typed reflexes")
print("=" * 76)
for name, qs in CASES:
    lat, out = post(LAYA, {"state": STATE, "questions": qs}, 90)
    if "answers" in out:
        a = list(out["answers"].values())[0]
        print(f"  {name:<10} {lat:>6.3f}s  type={a.get('type'):<7} {json.dumps(a)[:110]}")
    else:
        print(f"  {name:<10} {lat:>6.3f}s  {json.dumps(out)[:170]}")

print()
print("=" * 76)
print("TIER 2 -- lfm3, budget raised to 1200 (the 384 run returned EMPTY: thinking ate it all)")
print("=" * 76)
for name, qs in CASES:
    q = list(qs.values())[0]
    prompt = f"{q['instructions']} Reply with one short word."
    body = {"model": "lfm3", "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1200, "stream": False}
    lat, out = post(LOCAL, body, 400)
    if "choices" in out:
        m = out["choices"][0].get("message") or {}
        c = (m.get("content") or "").strip()
        print(f"  {name:<10} {lat:>6.3f}s  tok={out['usage']['completion_tokens']:<5} "
              f"think={len(m.get('reasoning_content') or ''):<5} answer={c[:45]!r}")
    else:
        print(f"  {name:<10} {lat:>6.3f}s  {json.dumps(out)[:140]}")

print()
print("  tier1 total latency vs tier2, same three questions:")
print("    -> see rows above; tier1 is measured in milliseconds, tier2 in seconds.")
