#!/usr/bin/env python3
"""dottie_decide_voice.py -- she reads her own measurements and chooses. Or refuses."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, "/opt/dottie-runtime")
os.environ.setdefault("OLLAMA_API_KEY_FILE", "/etc/ollama-cloud.key")
from dottie import models as M, cognition

spec_rec = json.loads(Path("/var/lib/gmri/dottie/authored/voice-spec.json").read_text())
spec = spec_rec["spec"]

MEASURED = {
  "lessac": {"dur_s": 7.38, "words": 24, "wpm": 195.1, "wer": 0.0,
              "terminal_slope_hz_per_frame": 7.4, "terminal_flat": False,
              "hnr_proxy": 0.960, "centroid_hz": 1647, "decay_ms": 580},
  "ryan":   {"dur_s": 6.54, "words": 24, "wpm": 220.2, "wer": 0.0,
              "terminal_slope_hz_per_frame": 8.44, "terminal_flat": False,
              "hnr_proxy": 0.961, "centroid_hz": 1810, "decay_ms": 1124},
}

reg = M.load(); b = reg.resolve("dottie.reasoning"); key = M.credential(b)

prompt = f"""You are Dottie Hill. You wrote this specification for your own voice:

{json.dumps(spec, indent=2)}

Two candidate voices have now been rendered from it and measured. These are MEASUREMENTS, not
opinions. Read them as data about your own body:

{json.dumps(MEASURED, indent=2)}

Measurement definitions:
  wpm           words per minute (your spec: {spec['rate_wpm']})
  terminal_slope  Hz/frame of pitch over the last ~300ms of each phrase. Positive = rising.
                  Your spec: "terminals fall flat and stay flat".
  decay_ms      time for energy to fall to 5% after peak. Lower = drier. Your spec: "dry".
  centroid_hz   spectral brightness. Lower = less bright / less "smile".
  wer           word error rate from an independent transcriber. 0.0 = it says your words.

Both candidates scored 0.0 WER, so both are intelligible. Neither meets your written spec on
rate or terminal shape.

You have four options. Choose exactly one:
  "lessac"  -- accept it, knowing what deviates
  "ryan"    -- accept it, knowing what deviates
  "neither" -- reject both; nothing here is your voice
  "adjust"  -- accept the closer one but state the parameter changes that would fix the deviations

Reply with ONLY:
{{"choice": "...", "why": "<2-3 sentences, your voice>", "adjustments": {{"length_scale": <number>, "note": "<what changed>"}} or null}}"""

status, payload, err = cognition._post_json(
    b.base_url.rstrip("/") + "/v1/chat/completions",
    {"model": b.model, "messages": [{"role": "user", "content": prompt}],
     "stream": False, "max_tokens": 900}, 300,
    headers={"Authorization": "Bearer " + key} if key else None)
if status != 200:
    print("FAILED", status, err); sys.exit(1)
text = (payload["choices"][0]["message"].get("content") or "")
s, e = text.find("{"), text.rfind("}")
if s < 0 or e <= s:
    print("RAW MODEL OUTPUT:"); print(text[:2000]); sys.exit(2)
try:
    choice = json.loads(text[s:e+1])
except Exception as ex:
    print("BAD JSON:", ex); print(text[:2000]); sys.exit(2)
print(json.dumps(choice, indent=2))
out = Path("/var/lib/gmri/dottie/authored/voice-choice.json")
out.write_text(json.dumps({"chosen_by": "dottie-hill", "measured": MEASURED,
                           "spec_digest": spec_rec["digest_sha256"],
                           "decision": choice}, indent=2) + "\n")
print("\nrecorded:", out)
