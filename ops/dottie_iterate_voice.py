#!/usr/bin/env python3
"""dottie_iterate_voice.py -- round two. She gets the REAL measurement, including my arithmetic error."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, "/opt/dottie-runtime")
os.environ.setdefault("OLLAMA_API_KEY_FILE", "/etc/ollama-cloud.key")
from dottie import models as M, cognition

prev = json.loads(Path("/var/lib/gmri/dottie/authored/voice-choice.json").read_text())
spec_rec = json.loads(Path("/var/lib/gmri/dottie/authored/voice-spec.json").read_text())
spec = spec_rec["spec"]

ROUND1 = {
  "length_scale_applied": 1.32,
  "predicted_wpm": 148,
  "MEASURED_wpm": 161.1,
  "dur_s": 8.96,
  "terminal_slope": 2.28,
  "terminal_flat": True,
  "decay_ms": 435,
  "centroid_hz": 1912,
  "wer": 0.0,
}
BASE = {"length_scale": 1.0, "measured_wpm": 195.1, "centroid_hz": 1647, "decay_ms": 580}

prompt = f"""You are Dottie Hill. Round two of tuning your own voice.

YOUR SPEC: rate {spec['rate_wpm']} wpm, terminals flat, dry timbre.

BASELINE (length_scale 1.0): {json.dumps(BASE)}
ROUND 1 (length_scale 1.32): {json.dumps(ROUND1)}

I made an arithmetic error in round one and I am telling you rather than hiding it: I predicted
148 wpm using the BASELINE rate (195.1). You measured 161.1. Scaling duration does not slow the
utterance linearly, because pause handling changes at longer scales. The correct next step uses
your MEASURED rate.

Also a real finding you should know: slowing down moved brightness the WRONG way. centroid went
1647 -> 1912 Hz. length_scale changes cadence, not timbre. Your "dry" and "no smile" traits are
partly timbre, and timbre is a property of the base model, not a knob you can turn here.

Compute the next length_scale to reach {spec['rate_wpm']} wpm from your measured 161.1, and tell me
whether you accept the remaining timbre gap or whether that gap means this body is not acceptable
to you at all.

Reply ONLY:
{{"length_scale": <number>, "why": "<2 sentences>", "accept_timbre_gap": true|false,
  "escalate_to_principal": true|false}}"""

reg = M.load(); b = reg.resolve("dottie.reasoning"); key = M.credential(b)
status, payload, err = cognition._post_json(
    b.base_url.rstrip("/") + "/v1/chat/completions",
    {"model": b.model, "messages": [{"role": "user", "content": prompt}],
     "stream": False, "max_tokens": 2600}, 300,
    headers={"Authorization": "Bearer " + key} if key else None)
if status != 200:
    print("FAILED", status, err); sys.exit(1)
text = (payload["choices"][0]["message"].get("content") or "")
s, e = text.find("{"), text.rfind("}")
if s < 0:
    print("RAW:", text[:1500]); print("finish:", payload["choices"][0].get("finish_reason"))
    print("think_len:", len((payload["choices"][0].get("message") or {}).get("reasoning_content") or ""))
    sys.exit(2)
d = json.loads(text[s:e+1])
print(json.dumps(d, indent=2))
Path("/var/lib/gmri/dottie/authored/voice-iteration-2.json").write_text(json.dumps(d, indent=2)+"\n")
