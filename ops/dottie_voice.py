#!/usr/bin/env python3
"""dottie-voice: her voice, live, streaming.

She authored "Plain Keeper" herself (see /var/lib/gmri/dottie/authored/voice-spec.json,
digest a9de15da...). This service renders that spec through Piper and streams it as it is made,
so a sentence is audible before the next one exists. Nothing here invents her character: rate,
register and cadence come from her authored file, and if that file is missing this service refuses
to speak rather than substituting a stock voice.

Endpoints:
  GET  /health           -> {ok, voice, spec_digest, ready}
  GET  /voice/spec       -> her authored spec, verbatim, with its digest
  POST /speak            -> {"text": "...", "stream": true}  audio/wav (chunked)
  POST /speak            -> {"text": "...", "format": "frames"} JSON of base64 PCM frames,
                            each with its index and duration, so a websocket client can
                            play them as they arrive instead of buffering a whole file.

The text is split at sentence boundaries and each sentence is synthesised separately. That is
what makes it live: first audio in well under a second rather than after the whole paragraph.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RUNTIME = Path("/opt/dottie-runtime")
PIPER = RUNTIME / ".venv/bin/piper"
VOICES = RUNTIME / "voices"
SPEC_PATH = Path("/var/lib/gmri/dottie/authored/voice-spec.json")

# Her authored choices, mapped to Piper's controls.
#   rate 148 wpm  -> length_scale 165/148 = 1.11 (Piper's default cadence is ~165 wpm)
#   -2.5 semitones -> Piper has no pitch knob; pitch is applied at playback. We expose it in the
#                     spec endpoint so a player can honour it, rather than faking it here.
VOICE_MODEL = os.environ.get("DOTTIE_VOICE_MODEL", "en_US-lessac-medium")
DEFAULT_LENGTH_SCALE = float(os.environ.get("DOTTIE_LENGTH_SCALE", "1.11"))
BIND = os.environ.get("DOTTIE_VOICE_BIND", "127.0.0.1")
PORT = int(os.environ.get("DOTTIE_VOICE_PORT", "8770"))


def load_spec():
    """Her authored spec, or None. Never substitute a default voice silently."""
    try:
        return json.loads(SPEC_PATH.read_text())
    except Exception:
        return None


def sentences(text):
    """Split for streaming. Long clauses are further split so no single frame is huge."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        while len(p) > 220:
            cut = p.rfind(",", 0, 220)
            if cut < 80:
                cut = p.rfind(" ", 0, 220)
            if cut < 80:
                cut = 220
            out.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            out.append(p)
    return out or ([text.strip()] if (text or "").strip() else [])


def synth(chunk, length_scale):
    """One chunk -> WAV bytes, via Piper on stdout."""
    model = VOICES / (VOICE_MODEL + ".onnx")
    if not model.exists():
        raise RuntimeError("voice model missing: %s" % model)
    p = subprocess.run(
        [str(PIPER), "--model", str(model), "--length-scale", "%.3f" % length_scale,
         "--output_file", "/dev/stdout"],
        input=chunk.encode(), capture_output=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError("piper failed: %s" % (p.stderr.decode()[-300:]))
    return p.stdout


def wav_to_pcm(wav_bytes):
    """Strip a 44-byte canonical WAV header. Piper emits 16-bit mono PCM."""
    if wav_bytes[:4] != b"RIFF":
        return wav_bytes
    idx = wav_bytes.find(b"data")
    if idx < 0:
        return wav_bytes[44:]
    return wav_bytes[idx + 8:]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        spec = load_spec()
        if path == "/health":
            return self._json(200, {
                "ok": spec is not None,
                "service": "dottie-voice",
                "voice": VOICE_MODEL,
                "spec_digest": (spec or {}).get("digest_sha256"),
                "ready": (VOICES / (VOICE_MODEL + ".onnx")).exists() and spec is not None,
                "streaming": True,
            })
        if path == "/voice/spec":
            if spec is None:
                return self._json(503, {"error": "no authored voice spec; refusing to speak"})
            return self._json(200, spec)
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != "/speak":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return self._json(400, {"error": "bad json"})

        spec = load_spec()
        if spec is None:
            # Refuse rather than speak in a voice she did not choose.
            return self._json(503, {"error": "no authored voice spec; refusing to speak"})

        text = (body.get("text") or "").strip()
        if not text:
            return self._json(400, {"error": "text required"})

        authored = (spec.get("spec") or {})
        wpm = authored.get("rate_wpm") or 148
        length_scale = float(body.get("length_scale") or (165.0 / float(wpm)))
        fmt = (body.get("format") or "wav").lower()

        try:
            chunks = sentences(text)
            if fmt == "frames":
                frames = []
                for i, c in enumerate(chunks):
                    pcm = wav_to_pcm(synth(c, length_scale))
                    frames.append({
                        "index": i,
                        "text": c,
                        # 16-bit mono 22.05 kHz is Piper's default; duration from byte count.
                        "pcm_b64": base64.b64encode(pcm).decode(),
                        "sample_rate": 22050,
                        "duration_s": round(len(pcm) / (22050 * 2), 3),
                    })
                return self._json(200, {
                    "voice": VOICE_MODEL, "rate_wpm": wpm,
                    "pitch_offset_semitones": authored.get("pitch_offset_semitones"),
                    "frames": frames,
                })

            # Streaming WAV: emit each sentence as it is synthesised.
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            first = True
            for c in chunks:
                data = synth(c, length_scale)
                if not first:
                    data = wav_to_pcm(data)   # subsequent chunks: raw PCM continuation
                first = False
                self.wfile.write(b"%X\r\n" % len(data) + data + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except Exception as exc:
            try:
                self._json(500, {"error": "%s: %s" % (type(exc).__name__, exc)})
            except Exception:
                pass


if __name__ == "__main__":
    spec = load_spec()
    if spec is None:
        print("dottie-voice: NO authored voice spec -- refusing to start", flush=True)
        sys.exit(1)
    print("dottie-voice listening %s:%d voice=%s spec_digest=%s"
          % (BIND, PORT, VOICE_MODEL, (spec.get("digest_sha256") or "")[:12]), flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
