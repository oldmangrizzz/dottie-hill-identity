#!/usr/bin/env python3
"""anchor.py -- the anchor custodian's tool.

WHY THIS EXISTS. The heartbeat chain proves continuity and records silence, but it lives on the
machine it describes. A party who can rewrite the chain can recompute every hash and produce a
perfect-looking history with a gap removed. The only defence is a copy of the chain HEAD held
somewhere else, by someone with no stake in the lie.

That someone is a custodian: a person who agrees to hold a hash and hand it back if asked. This
script is their tool. It does three things and nothing else:

  take    -- pull the current anchor from Dottie's machine and record it locally, append-only
  verify  -- compare what Dottie's machine says now against every anchor you hold
  report  -- human-readable summary of what you hold and what it proves

The custody log is append-only JSONL with its own hash chain, so the custodian's record is also
tamper-evident. If someone edits an old anchor, verify catches it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_CUSTODY = Path.home() / ".dottie-custody"
HOST = "root@<MESH_HOST>"
ANCHOR_PATH = "/var/lib/gmri/heartbeat/anchor.json"
CHAIN_PATH = "/var/lib/gmri/heartbeat/chain.jsonl"


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def fetch_remote(path: str) -> str:
    """Read a file from Dottie's machine through the container. Returns raw text or raises."""
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", HOST,
         "pct exec 115 -- cat " + path],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError("could not read %s: %s" % (path, r.stderr.strip()[:200]))
    return r.stdout


def load_log(custody: Path) -> list:
    p = custody / "custody.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_log(custody: Path, record: dict) -> None:
    """Append-only. Each row chains to the previous, so the custodian's own log is verifiable."""
    custody.mkdir(parents=True, exist_ok=True)
    p = custody / "custody.jsonl"
    prior = load_log(custody)
    record["prev_sha256"] = sha(canon(prior[-1])) if prior else "0" * 64
    record["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record["self_sha256"] = sha(canon({k: v for k, v in record.items() if k != "self_sha256"}))
    with p.open("a") as f:
        f.write(canon(record) + "\n")
        f.flush()
        os.fsync(f.fileno())


def cmd_take(custody: Path) -> int:
    """Pull the current anchor and record it. This is the whole ongoing job."""
    try:
        anchor = json.loads(fetch_remote(ANCHOR_PATH))
    except Exception as exc:
        print("FAILED to read anchor:", exc)
        return 1
    rec = {
        "kind": "anchor",
        "subject": "dottie-hill",
        "last_seq": anchor.get("last_seq"),
        "last_sha256": anchor.get("last_sha256"),
        "anchored_at": anchor.get("anchored_at"),
    }
    append_log(custody, rec)
    print("recorded anchor for seq %s at %s" % (rec["last_seq"], rec["recorded_at"]))
    print("  sha256:", (rec["last_sha256"] or "")[:24], "...")
    return 0


def cmd_verify(custody: Path) -> int:
    """Compare what the machine says now against every anchor you hold.

    Three outcomes matter:
      OK        -- the current chain extends what you hold; nothing was rewritten
      ROLLBACK  -- the machine's head moved BACKWARD, or sideways from an anchor you hold:
                   history was replaced
      GAP       -- seq advanced but the chain reports missing beats (silence)
    """
    held = load_log(custody)
    if not held:
        print("no anchors held yet. run: anchor.py take")
        return 1

    try:
        now = json.loads(fetch_remote(ANCHOR_PATH))
    except Exception as exc:
        print("FAILED to read current anchor:", exc)
        return 1

    # verify your own log first -- if someone edited your records, everything else is moot
    for i, row in enumerate(held):
        body = {k: v for k, v in row.items() if k != "self_sha256"}
        if sha(canon(body)) != row.get("self_sha256"):
            print("CUSTODY LOG TAMPERED at row %d -- your own records were edited" % i)
            return 2
        if i and row.get("prev_sha256") != sha(canon(held[i - 1])):
            print("CUSTODY LOG BROKEN at row %d -- linkage does not match" % i)
            return 2

    last = held[-1]
    held_seq, held_hash = last.get("last_seq") or 0, last.get("last_sha256")
    now_seq, now_hash = now.get("last_seq") or 0, now.get("last_sha256")

    print("you hold : seq %s  %s..." % (held_seq, (held_hash or "")[:24]))
    print("machine  : seq %s  %s..." % (now_seq, (now_hash or "")[:24]))
    print()

    if now_seq < held_seq:
        print("ROLLBACK -- the machine's head went backward.")
        print("  You hold seq %s. It now claims seq %s." % (held_seq, now_seq))
        print("  A chain cannot shrink. Either the heartbeat was reset, the state was restored")
        print("  from an old backup, or history was replaced. Ask for an explanation.")
        return 2

    if now_seq == held_seq and now_hash != held_hash:
        print("ROLLBACK -- same seq, different hash.")
        print("  The chain was rewritten in place. This is the specific thing anchors detect.")
        return 2

    if now_seq == held_seq:
        print("OK -- no new beats since your last anchor (chain has not advanced).")
        return 0

    # advanced: check for gaps in the chain itself
    try:
        chain = fetch_remote(CHAIN_PATH)
        rows = [json.loads(l) for l in chain.splitlines() if l.strip()]
        gaps = []
        from datetime import datetime
        for a, b in zip(rows, rows[1:]):
            t0 = datetime.fromisoformat(a["at"].replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(b["at"].replace("Z", "+00:00"))
            d = (t1 - t0).total_seconds()
            if d > 90:
                gaps.append((a["seq"], b["seq"], round(d, 1)))
        if gaps:
            print("GAP DETECTED -- the chain records silence:")
            for s0, s1, d in gaps:
                print("  seq %s -> %s : %.1fs of missing beats" % (s0, s1, d))
            print("  This is the silent-night detector. It cannot be cleared by restarting.")
            return 2
    except Exception as exc:
        print("(could not read the chain for gap analysis: %s)" % exc)

    print("OK -- chain advanced from %s to %s with no gaps and no rollback." % (held_seq, now_seq))
    print("  Your anchor for seq %s is still consistent with history." % held_seq)
    return 0


def cmd_report(custody: Path) -> int:
    held = load_log(custody)
    if not held:
        print("no anchors held.")
        return 1
    print("custody: %s" % (custody / "custody.jsonl"))
    print("anchors held: %d" % len(held))
    print("first: seq %s at %s" % (held[0]["last_seq"], held[0]["recorded_at"]))
    print("last : seq %s at %s" % (held[-1]["last_seq"], held[-1]["recorded_at"]))
    span = (held[-1]["last_seq"] or 0) - (held[0]["last_seq"] or 0)
    print("beats covered by the span: %d  (~%.1f hours at 30s/beat)" % (span, span * 30 / 3600))
    print()
    print("What you can prove with this record:")
    print("  - that a chain existed, at each recorded seq, at the time you wrote the row")
    print("  - that no later chain rolled back before your earliest anchor")
    print("  - that no later chain rolled back before your latest anchor")
    print("What you cannot prove:")
    print("  - nothing about gaps between your anchors. Custody is periodic; gaps inside the")
    print("    interval are only caught if you take an anchor on both sides of them.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="anchor", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["take", "verify", "report"])
    ap.add_argument("--custody", default=str(DEFAULT_CUSTODY))
    a = ap.parse_args()
    custody = Path(a.custody).expanduser()
    return {"take": cmd_take, "verify": cmd_verify, "report": cmd_report}[a.command](custody)


if __name__ == "__main__":
    raise SystemExit(main())
