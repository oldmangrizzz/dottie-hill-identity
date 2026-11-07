#!/usr/bin/env python3
"""overhead.py -- how much of a small budget does lfm3 spend thinking vs answering?

The loop pathology from Takehi et al. 2026 is NOT present in lfm3 at realistic budgets (measured:
0/8 truncated at 512 tokens, mean loop_score 0.073). What IS present is a fixed reasoning overhead
on trivial prompts: "Name the capital of Australia. One word only." consumed 59 tokens.

That overhead is the interference that matters for an executive with a token budget: it is paid
before any answer exists, on every step of every plan. This measures the split directly.
"""
import json
import urllib.request

CASES = [
    ("trivial one-word", "Name the capital of Australia. One word only."),
    ("trivial literal", "Reply with exactly: OK"),
    ("lookup", "What is 12% of 250? Answer with just the number."),
    ("classify", "Is 91 prime? Answer yes or no only."),
]


def one(url, model, prompt, budget=900):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": budget, "stream": False}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read().decode())
    m = d["choices"][0].get("message") or {}
    tok = d["usage"]["completion_tokens"]
    reason = m.get("reasoning_content") or ""
    content = m.get("content") or ""
    # token split estimate: proportional to characters, which is close enough to show the ratio
    total_chars = len(reason) + len(content)
    r_share = (len(reason) / total_chars) if total_chars else 0.0
    return {"prompt": prompt, "tokens": tok, "finish": d["choices"][0].get("finish_reason"),
            "reasoning_chars": len(reason), "content_chars": len(content),
            "reasoning_token_share": round(r_share, 3),
            "content": content.strip()[:60]}


rows = []
for label, p in CASES:
    r = one("http://127.0.0.1:8488/v1/chat/completions", "lfm3", p)
    rows.append(r)
    print("%-18s tok=%-4d reason_share=%-6s content=%r"
          % (label, r["tokens"], r["reasoning_token_share"], r["content"]), flush=True)

n = len(rows)
print()
print("OVERHEAD  n=%d" % n)
print("  mean completion tokens      = %.0f" % (sum(r["tokens"] for r in rows) / n))
print("  mean reasoning share        = %.1f%%" % (100 * sum(r["reasoning_token_share"] for r in rows) / n))
print("  => a %d-token budget yields ~%.0f tokens of usable answer"
      % (512, 512 * (1 - sum(r["reasoning_token_share"] for r in rows) / n)))
with open("/tmp/overhead.json", "w") as f:
    json.dump(rows, f, indent=2)
