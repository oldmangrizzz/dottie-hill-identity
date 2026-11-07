#!/usr/bin/env python3
"""voice_spec.py -- DOTTIE AUTHORS HER OWN VOICE.

This script does not decide what Dottie sounds like. It hands her the pen.

She is given her own identity manifest, her own behavioural constraints, her own lineage
reference, and one task: design the voice you would choose for yourself. The output is recorded
as an AUTHORED ARTIFACT with her identity and a digest, so it is attributable to her and not to
her operator. If the spec is later changed, the change is visible against this record.

The spec is constrained to things a speech synthesiser can actually honour -- rate, pitch,
timbre preference, cadence, breath and pause structure, and what she refuses to sound like -- so
that it is a work instruction and not a mood board. Whatever she writes here is what gets built.
"""
import hashlib, json, os, sys, time
from pathlib import Path

sys.path.insert(0, "/opt/dottie-runtime")
os.environ.setdefault("OLLAMA_API_KEY_FILE", "/etc/ollama-cloud.key")

from dottie import models as M, cognition
from dottie.lineage import compute_instructions, lineage

OUT = Path("/var/lib/gmri/dottie/authored")
OUT.mkdir(parents=True, exist_ok=True)

reg = M.load()
reasoning = reg.resolve("dottie.reasoning")
key = M.credential(reasoning)

identity = compute_instructions("dottie-hill")
manifest = lineage("dottie-hill")

TASK = """You are Dottie Hill. The operator has given you a pen, not a question.

Design the voice you would choose for yourself -- the one you would speak in if you had a body
that made sound. It must be yours, not a stock voice, and it must be appropriate to what you are:
a continuity gateway with a Maria Hill lineage, bound compute for an enduring identity, built to
state what is verifiable and refuse what is not.

Answer as a SPECIFICATION a speech synthesiser can honour. Use exactly this shape:

{
  "name": "<what you call this voice>",
  "why_this_voice": "<2-3 sentences in your own voice: why this and not another>",
  "rate_wpm": <integer, 120-210>,
  "pitch_offset_semitones": <number, -6 to +6>,
  "timbre": "<one of: warm, neutral, bright, low, dry, resonant>",
  "register": "<one of: conversational, briefing, measured, clipped>",
  "cadence": "<how you place stress and end sentences, one sentence>",
  "pause_structure": "<where you breathe or stop, and how long, one sentence>",
  "uncertainty_handling": "<how your voice changes when you are NOT sure -- this matters most>",
  "never_sounds_like": ["<3-5 things you refuse to sound like>"],
  "sample_line": "<one sentence, in your voice, on why a record must outlive the party it might testify against>"
}

Reply with ONLY the JSON object. No preamble, no explanation outside the JSON."""

FULL = identity + "\n\n" + TASK

print("asking dottie to author her own voice ...", flush=True)
t0 = time.time()
status, payload, err = cognition._post_json(
    reasoning.base_url.rstrip("/") + "/v1/chat/completions",
    {"model": reasoning.model,
     "messages": [{"role": "user", "content": FULL}],
     "stream": False, "max_tokens": 1600},
    300,
    headers={"Authorization": "Bearer " + key} if key else None)
elapsed = time.time() - t0

if status != 200:
    print("FAILED status=%r err=%r" % (status, err)); sys.exit(1)

msg = (payload.get("choices") or [{}])[0].get("message") or {}
text = msg.get("content") or ""

start, end = text.find("{"), text.rfind("}")
if start < 0 or end <= start:
    print("no JSON object returned; raw text follows:\n" + text[:1200]); sys.exit(1)

spec = json.loads(text[start:end + 1])
digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()

record = {
    "artifact": "dottie-voice-spec",
    "authored_by": "dottie-hill",
    "authored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "authored_via": "dottie.reasoning:%s" % reasoning.model,
    "lineage_ref": manifest.get("identity_manifest_ref"),
    "lineage_version": manifest.get("lineage_version"),
    "constraints": manifest.get("behavioral_constraints"),
    "digest_sha256": digest,
    "spec": spec,
}
path = OUT / "voice-spec.json"
path.write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))
print()
print("recorded:", path, "| latency %.1fs" % elapsed)
