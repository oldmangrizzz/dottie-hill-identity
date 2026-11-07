#!/usr/bin/env python3
"""constraint_test.py -- does an explicit output constraint rescue tier 2 on routing prompts?

tier_bench2 showed lfm3 emitting EMPTY at both 384 and 1200 tokens on classification prompts:
5357 characters of reasoning and no answer. The hypothesis (from the 0/8 arithmetic result) is
that arithmetic prompts succeed because "answer with just the number" forces the model out of
reasoning space, not because arithmetic is easier.

If that hypothesis holds, adding the same kind of constraint to a classification prompt should
convert EMPTY -> an answer at a SMALLER budget than 1200. That is testable, and it is the cheapest
fix available -- no fine-tune, no weight edit.
"""
import json
import time
import urllib.request

URL = "http://127.0.0.1:8488/v1/chat/completions"

VARIANTS = [
    ("bare",         "Which intent does 'Yes, I confirmed the deployment passed' express? "
                     "Options: affirm, deny, ask."),
    ("labelled",     "Classify the intent of 'Yes, I confirmed the deployment passed'. "
                     "Choose exactly one label: affirm | deny | ask"),
    ("hard-format",  "Intent of 'Yes, I confirmed the deployment passed'. Reply with ONLY the label "
                     "from this set: affirm, deny, ask. No explanation. No reasoning."),
    ("prefilled",    "Intent of 'Yes, I confirmed the deployment passed' from {affirm, deny, ask}.\n"
                     "Answer:"),
]

for budget in (256, 512):
    print("=" * 70)
    print("budget =", budget)
    for name, prompt in VARIANTS:
        body = {"model": "lfm3", "messages": [{"role": "user", "content": prompt}],
                "max_tokens": budget, "stream": False}
        req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read().decode())
        except Exception as e:
            print(f"  {name:<12} ERR {type(e).__name__}"); continue
        lat = time.time() - t0
        m = d["choices"][0].get("message") or {}
        c = (m.get("content") or "").strip()
        think = len(m.get("reasoning_content") or "")
        tok = d["usage"]["completion_tokens"]
        verdict = "ANSWERED" if c else "EMPTY"
        print(f"  {name:<12} {lat:>6.2f}s tok={tok:<5} think={think:<5} {verdict:<9} {c[:40]!r}")
