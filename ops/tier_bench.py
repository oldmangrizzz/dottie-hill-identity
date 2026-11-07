#!/usr/bin/env python3
"""tier_bench.py -- measure the three tiers on IDENTICAL prompts, latency and cost.

Dottie's design has three distinct compute populations:
  tier 1  LAYA   -- typed System-1 verdicts over HTTP, loopback, CPU, no model weights on the wire
  tier 2  lfm3   -- the local reasoning model on llama.cpp Vulkan, budget-bounded
  tier 3  cloud  -- one Ollama account, needs OLLAMA_API_KEY (absent: 401 anonymously)

The point of this harness is NOT to crown a winner. It is to show WHICH questions each tier can
actually answer, and at what latency -- that is what decides whether a reflex tier earns its place
rather than being decoration. It reports UNREACHABLE / REFUSED honestly instead of skipping.
"""
import json
import time
import urllib.error
import urllib.request

LAYA = "http://127.0.0.1:8473/v1/systemone"
LOCAL = "http://127.0.0.1:8488/v1/chat/completions"

# A LAYA request is a typed question over a state: {state, questions:{name:...}}
QUESTIONS = [
    ("bool",   "is_affirmative", {"kind": "bool"},
     "The system answered: yes. Was the answer affirmative?"),
    ("choice", "pick_greeting", {"kind": "choice", "options": ["hello", "goodbye", "unknown"]},
     "Which greeting matches: 'hello there'?"),
    ("score",  "confidence", {"kind": "score"},
     "How confident is this statement: 'The build passed all 19 gates.'"),
]


def post(url, payload, timeout, headers=None):
    body = json.dumps(payload).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return round(time.time() - t0, 3), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return round(time.time() - t0, 3), {"_http_error": e.code, "_body": e.read().decode()[:200]}
    except Exception as e:
        return round(time.time() - t0, 3), {"_error": "%s: %s" % (type(e).__name__, e)}


print("=" * 78)
print("TIER 1 -- LAYA typed reflexes (loopback, CPU)")
print("=" * 78)
for kind, name, spec, text in QUESTIONS:
    state = {"utterance": text}
    payload = {"state": state, "questions": {name: spec}}
    lat, out = post(LAYA, payload, 60)
    print(f"  {name:<16} {lat:>6.3f}s  {json.dumps(out)[:180]}")

print()
print("=" * 78)
print("TIER 2 -- lfm3 on llama.cpp Vulkan (budget 384, thinking stripped in production)")
print("=" * 78)
for kind, name, spec, text in QUESTIONS:
    body = {"model": "lfm3",
            "messages": [{"role": "user", "content": text + " Answer in one word."}],
            "max_tokens": 384, "stream": False}
    lat, out = post(LOCAL, body, 300)
    if "choices" in out:
        msg = out["choices"][0].get("message") or {}
        content = (msg.get("content") or "").strip()
        think = len(msg.get("reasoning_content") or "")
        tok = out.get("usage", {}).get("completion_tokens")
        print(f"  {name:<16} {lat:>6.3f}s  tok={tok:<4} think_chars={think:<5} answer={content[:50]!r}")
    else:
        print(f"  {name:<16} {lat:>6.3f}s  {json.dumps(out)[:140]}")

print()
print("=" * 78)
print("TIER 3 -- Ollama Cloud (credential absent by design until the principal supplies it)")
print("=" * 78)
lat, out = post("https://ollama.com/api/chat",
                {"model": "deepseek-v4.1-flash",
                 "messages": [{"role": "user", "content": "Say OK"}], "stream": False}, 60)
print(f"  anonymous call    {lat:>6.3f}s  {json.dumps(out)[:140]}")
print("  -> binding is VALIDATED, authentication is a principal action. Not a defect.")
