#!/usr/bin/env python3
"""Append-only, hash-chained heartbeat with off-box anchorability.

WHY THIS EXISTS (the silent-night failure mode)
-----------------------------------------------
The failure this module is built to expose is not a wrong log line. It is an
ABSENCE. A record that simply stops is indistinguishable, from the inside, from
a record that was never kept: no error is raised, no counter is decremented, no
alert fires, because nothing happened -- and nothing happening looks exactly
like nothing being requested.

Therefore the GAP itself is made a first-class, verifiable artifact:

  * every beat is written to an append-only JSONL chain and sealed with a
    SHA-256 of its own canonical form plus the SHA-256 of the previous line,
    so a removed, reordered, or edited beat breaks the chain verifiably;
  * every beat is timestamped, so the interval between beats is measurable, so
    a *silence* longer than the tolerated interval is itself a verification
    FAILURE rather than an absence of data;
  * a small anchor file (last_seq / last_sha256 / anchored_at) can be copied
    off the machine, so an external party can detect that heartbeats stopped,
    and when, WITHOUT trusting the machine that stopped.

Chain construction
------------------
canonical(obj) = json.dumps(obj, sort_keys=True, separators=(",", ":"))

  seq         1, 2, 3, ... (contiguous; a hole is a failure)
  at          UTC ISO8601, e.g. 2026-11-04T19:00:00Z
  prev_sha256 sha256(canonical(previous stored record)), 64 zeros for seq 1
  self_sha256 sha256(canonical(this record WITHOUT the self_sha256 field))
  subject     free text, default "dottie-hill"
  host        hostname at write time
  note        free text, default ""

The stored record is therefore {seq, at, prev_sha256, self_sha256, subject,
host, note} serialised as one canonical JSON object per line. prev_sha256 for
line N is the hash of the *whole stored* line N-1 (self_sha256 included), which
is why editing any field of any line is detectable both locally
(self_sha256) and downstream (the next line's prev_sha256).

Verification failures (all reported through ok / broken_at)
----------------------------------------------------------
  (a) a line whose self_sha256 does not match its content   -> tampering
  (b) a line whose prev_sha256 does not match its parent    -> tampering /
      splicing / deletion of a middle line
  (c) non-monotonic or non-contiguous seq                   -> reordering /
      truncation-from-the-middle / duplicated write
  (d) any inter-line gap greater than --tolerance-multiplier * --interval
      (default 3x 30s = 90s) -> the silent-night detector: the beat stopped
      and nobody said so

This module is pure stdlib, writes nothing outside its data directory, does not
import ``dottie``, and does not touch any other service.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

DATA_DIR = os.environ.get("GMRI_HEARTBEAT_DIR", "/var/lib/gmri/heartbeat")
CHAIN_PATH = os.path.join(DATA_DIR, "chain.jsonl")
ANCHOR_PATH = os.path.join(DATA_DIR, "anchor.json")

DEFAULT_INTERVAL = 30.0
DEFAULT_TOLERANCE_MULTIPLIER = 3.0
GENESIS_PREV = "0" * 64
DEFAULT_SUBJECT = "dottie-hill"

_FIELDS = ("seq", "at", "prev_sha256", "self_sha256", "subject", "host", "note")


# --------------------------------------------------------------------------- #
# canonical form + hashing
# --------------------------------------------------------------------------- #

def canonical(obj: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace. Hash input for the chain."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_hash(record: Dict[str, Any]) -> str:
    """Hash of a *stored* record, self_sha256 included (used for prev linkage)."""
    return sha256_text(canonical(record))


def self_hash(record: Dict[str, Any]) -> str:
    """Hash of a record with the self_sha256 field removed."""
    body = {k: v for k, v in record.items() if k != "self_sha256"}
    return sha256_text(canonical(body))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_at(text: str) -> datetime:
    """Parse the chain's UTC ISO8601 timestamps ('Z' or '+00:00')."""
    raw = str(text).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #

def read_lines(path: str) -> List[str]:
    """Return non-empty logical lines. A trailing blank line is tolerated."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return []
    lines = raw.splitlines()
    while lines and lines[-1].strip() == "":
        lines.pop()
    return lines


def parse_line(line: str) -> Dict[str, Any]:
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise ValueError("record is not a JSON object")
    return obj


def tail_state(path: str) -> Tuple[int, str]:
    """Return (last_seq, last_record_hash); (0, GENESIS) when the chain is empty."""
    lines = read_lines(path)
    if not lines:
        return 0, GENESIS_PREV
    last = parse_line(lines[-1])
    return int(last["seq"]), record_hash(last)


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #

def _ensure_dir(path: str) -> None:
    os.makedirs(path, mode=0o755, exist_ok=True)


def append_beat(
    *,
    path: str = CHAIN_PATH,
    subject: str = DEFAULT_SUBJECT,
    note: str = "",
    at: Optional[str] = None,
) -> Dict[str, Any]:
    """Append exactly one sealed beat. Returns the stored record.

    The append is serialised with an exclusive flock and fsynced, so two
    processes cannot interleave a half line or fork the chain.
    """
    _ensure_dir(os.path.dirname(path) or ".")
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.seek(0)
            data = fh.read()
            lines = [ln for ln in data.splitlines() if ln.strip()]
            if lines:
                last = parse_line(lines[-1])
                seq = int(last["seq"]) + 1
                prev = record_hash(last)
            else:
                seq = 1
                prev = GENESIS_PREV
            record: Dict[str, Any] = {
                "seq": seq,
                "at": at or utc_now(),
                "prev_sha256": prev,
                "subject": str(subject),
                "host": socket.gethostname(),
                "note": str(note),
            }
            record["self_sha256"] = self_hash(record)
            line = canonical(record)
            if data and not data.endswith("\n"):
                line = "\n" + line  # heal a torn tail before appending
            fh.seek(0, os.SEEK_END)
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return record


def write_anchor(
    *,
    path: str = CHAIN_PATH,
    anchor_path: str = ANCHOR_PATH,
) -> Dict[str, Any]:
    """Write the off-box anchor: what an external party copies and keeps."""
    lines = read_lines(path)
    if not lines:
        anchor = {"last_seq": 0, "last_sha256": GENESIS_PREV, "anchored_at": utc_now()}
    else:
        last = parse_line(lines[-1])
        anchor = {
            "last_seq": int(last["seq"]),
            "last_sha256": record_hash(last),
            "anchored_at": utc_now(),
        }
    _ensure_dir(os.path.dirname(anchor_path) or ".")
    tmp = anchor_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(canonical(anchor) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, anchor_path)
    return anchor


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #

def verify(
    path: str = CHAIN_PATH,
    *,
    interval: float = DEFAULT_INTERVAL,
    tolerance_multiplier: float = DEFAULT_TOLERANCE_MULTIPLIER,
    tolerance: Optional[float] = None,
) -> Dict[str, Any]:
    """Walk the chain and report integrity, ordering, and silence.

    Returns::

        {"ok": bool, "lines": n, "first_seq": int|None, "last_seq": int|None,
         "broken_at": int|None, "gap_seconds_max": float, ...extras}

    ``ok`` is False if ANY of: a line is unparseable, a self_sha256 mismatch, a
    prev_sha256 linkage break, a non-contiguous or duplicate seq, or an
    inter-line silence longer than the tolerance. ``broken_at`` names the seq at
    which the first defect is anchored (the offending line, or the expected seq
    for a missing line).
    """
    tol = float(tolerance) if tolerance is not None else float(interval) * float(tolerance_multiplier)

    lines = read_lines(path)
    result: Dict[str, Any] = {
        "ok": True,
        "lines": 0,
        "first_seq": None,
        "last_seq": None,
        "broken_at": None,
        "gap_seconds_max": 0.0,
        "tolerance_seconds": tol,
        "interval_seconds": float(interval),
        "errors": [],
    }
    if not lines:
        result["ok"] = True  # an empty chain is empty, not broken: say so plainly
        result["errors"].append("empty chain: no beats written yet")
        return result

    result["lines"] = len(lines)
    prev_record: Optional[Dict[str, Any]] = None
    prev_seq: Optional[int] = None
    prev_dt: Optional[datetime] = None
    gap_at: Optional[int] = None

    for idx, line in enumerate(lines):
        try:
            record = parse_line(line)
        except Exception as exc:
            result["ok"] = False
            result["errors"].append(f"line {idx + 1}: unparseable record: {exc}")
            if result["broken_at"] is None:
                result["broken_at"] = (prev_seq + 1) if prev_seq is not None else 1
            break

        missing = [f for f in _FIELDS if f not in record]
        if missing:
            result["ok"] = False
            result["errors"].append(f"seq {record.get('seq')}: missing fields {missing}")
            if result["broken_at"] is None:
                result["broken_at"] = record.get("seq")
            break

        seq = int(record["seq"])
        if idx == 0:
            result["first_seq"] = seq
        elif seq != prev_seq + 1:
            reason = "duplicate/backwards seq" if seq <= prev_seq else "seq hole"
            result["ok"] = False
            result["errors"].append(
                f"{reason} at line {idx + 1}: expected seq {prev_seq + 1}, found {seq}"
            )
            if result["broken_at"] is None:
                result["broken_at"] = seq if seq <= prev_seq else prev_seq + 1

        # (a) self hash
        if self_hash(record) != record["self_sha256"]:
            result["ok"] = False
            result["errors"].append(f"seq {seq}: self_sha256 mismatch (line tampered)")
            if result["broken_at"] is None:
                result["broken_at"] = seq

        # (b) linkage
        if prev_record is None:
            if record["prev_sha256"] != GENESIS_PREV:
                result["ok"] = False
                result["errors"].append(
                    f"seq {seq}: first line prev_sha256 is not the zero genesis hash"
                )
                if result["broken_at"] is None:
                    result["broken_at"] = seq
        else:
            expected_prev = record_hash(prev_record)
            if record["prev_sha256"] != expected_prev:
                result["ok"] = False
                result["errors"].append(
                    f"seq {seq}: prev_sha256 does not match parent (chain broken)"
                )
                if result["broken_at"] is None:
                    result["broken_at"] = seq

        # (d) silence
        try:
            dt: Optional[datetime] = parse_at(record["at"])
        except Exception as exc:
            result["ok"] = False
            result["errors"].append(f"seq {seq}: unparseable timestamp {record['at']!r}: {exc}")
            if result["broken_at"] is None:
                result["broken_at"] = seq
            dt = None
        if dt is not None and prev_dt is not None:
            gap = (dt - prev_dt).total_seconds()
            if gap > result["gap_seconds_max"]:
                result["gap_seconds_max"] = float(gap)
            if gap > tol:
                result["ok"] = False
                if gap_at is None:
                    gap_at = seq
                result["errors"].append(
                    f"seq {seq}: silence of {gap:.1f}s exceeds tolerance {tol:.1f}s"
                )
                if result["broken_at"] is None:
                    result["broken_at"] = seq
        if dt is not None:
            prev_dt = dt

        prev_record = record
        prev_seq = seq
        result["last_seq"] = seq

    result["gap_at"] = gap_at
    return result


# --------------------------------------------------------------------------- #
# loop + CLI
# --------------------------------------------------------------------------- #

def run_loop(
    *,
    interval: float = DEFAULT_INTERVAL,
    path: str = CHAIN_PATH,
    anchor_path: str = ANCHOR_PATH,
    subject: str = DEFAULT_SUBJECT,
    note: str = "",
    max_beats: Optional[int] = None,
) -> int:
    """Beat forever (or max_beats times). Each beat refreshes the anchor."""
    _ensure_dir(os.path.dirname(path) or ".")
    _ensure_dir(os.path.dirname(anchor_path) or ".")
    beats = 0
    next_at = time.monotonic()
    while max_beats is None or beats < max_beats:
        try:
            record = append_beat(path=path, subject=subject, note=note)
            anchor = write_anchor(path=path, anchor_path=anchor_path)
            sys.stderr.write(
                f"beat seq={record['seq']} at={record['at']} sha={record['self_sha256'][:12]} "
                f"anchor_seq={anchor['last_seq']}\n"
            )
            sys.stderr.flush()
        except Exception as exc:  # keep beating; a failed beat is loud, not silent
            sys.stderr.write(f"beat failed: {type(exc).__name__}: {exc}\n")
            sys.stderr.flush()
        beats += 1
        if max_beats is not None and beats >= max_beats:
            break
        next_at += interval
        delay = next_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_at = time.monotonic()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Append-only hash-chained heartbeat for dottie-hill.")
    p.add_argument("--path", default=CHAIN_PATH, help=f"chain file (default {CHAIN_PATH})")
    p.add_argument("--anchor-path", default=ANCHOR_PATH, help=f"anchor file (default {ANCHOR_PATH})")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="seconds between beats")
    p.add_argument("--subject", default=DEFAULT_SUBJECT, help="subject label for beats")
    p.add_argument("--note", default="", help="free-text note attached to each beat")
    p.add_argument("--once", action="store_true", help="append exactly one beat and exit")
    p.add_argument("--loop", action="store_true", help="append a beat every --interval seconds")
    p.add_argument("--beats", type=int, default=None, help="with --loop: stop after N beats")
    p.add_argument("--verify", action="store_true", help="verify the chain, print JSON, exit 1 if broken")
    p.add_argument("--anchor", action="store_true", help="write anchor.json from the current chain")
    p.add_argument(
        "--tolerance-multiplier",
        type=float,
        default=DEFAULT_TOLERANCE_MULTIPLIER,
        help="max tolerated silence, in multiples of --interval (default 3)",
    )
    args = p.parse_args(argv)

    if args.verify:
        report = verify(
            args.path,
            interval=args.interval,
            tolerance_multiplier=args.tolerance_multiplier,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1

    if args.anchor:
        anchor = write_anchor(path=args.path, anchor_path=args.anchor_path)
        print(json.dumps(anchor, indent=2, sort_keys=True))
        return 0

    if args.once:
        record = append_beat(path=args.path, subject=args.subject, note=args.note)
        write_anchor(path=args.path, anchor_path=args.anchor_path)
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0

    if args.loop:
        return run_loop(
            interval=args.interval,
            path=args.path,
            anchor_path=args.anchor_path,
            subject=args.subject,
            note=args.note,
            max_beats=args.beats,
        )

    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
