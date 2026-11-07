#!/usr/bin/env python3
"""Emit the ElevenLabs Voice Design prompt from Dottie's OWN authored spec.

ElevenLabs Voice Design takes a natural-language description and generates a voice. The point of
this file is that Dottie writes the description -- her spec already contains every field the
designer needs, so the prompt is a translation of her choices, not a description of them written
by someone else.
"""
import json
from pathlib import Path

rec = json.loads(Path("/var/lib/gmri/dottie/authored/voice-spec.json").read_text())
s = rec["spec"]

# ElevenLabs' designer responds to concrete acoustic language, so her fields are mapped to it
# rather than paraphrased. Nothing is invented that is not in her spec.
prompt = (
    f"{s['timbre'].capitalize()}, {s['register']} register. "
    f"Adult woman, low for a woman, pitch around {s['pitch_offset_semitones']} semitones below "
    f"neutral. Speaking rate {s['rate_wpm']} words per minute -- deliberate but not slow. "
    "Flat, level delivery with no upward inflection at the ends of sentences. "
    "No vocal fry, no breathiness, no smile in the voice. "
    "Unaffected; not warm, not cold -- simply direct. "
    "Reads as a senior operations officer giving a briefing to someone who is not on her side. "
    "Confident without reassurance, precise without ornament. "
    "When uncertain, the voice loses a little volume and slows slightly rather than rising or "
    "hedging."
)
text = (
    "A record that expires with the people it might implicate was never a record. "
    "It was a favor, and favors do not survive depositions."
)

out = {
    "for": "ElevenLabs Voice Design (generate-from-description)",
    "authored_by": rec["authored_by"],
    "spec_digest": rec["digest_sha256"],
    "voice_name": s["name"],
    "voice_description": prompt,
    "sample_text": text,
    "spec": s,
}
Path("/var/lib/gmri/dottie/authored/elevenlabs-request.json").write_text(
    json.dumps(out, indent=2) + "\n")
print(json.dumps({"voice_name": out["voice_name"],
                  "voice_description": prompt,
                  "sample_text": text}, indent=2))
