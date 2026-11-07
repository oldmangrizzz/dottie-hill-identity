#!/usr/bin/env python3
"""world_model.py -- her understanding of the world, as a thing she maintains.

WHY THIS EXISTS. GMRI's mandate is the preservation of knowledge, intelligence and life. You cannot
preserve what you cannot notice going away, and you cannot notice from a single snapshot. So this is
not a dashboard: it is a maintained model with a baseline, a diff, and an honest staleness report.

THREE FACULTIES, ONE SUBSTRATE (the operator asked for all three):
  1. SITUATION   what is true now           -> snapshot()
  2. WATCH       what changed since last    -> diff()  (the preservation faculty)
  3. RESEARCH    what can be investigated   -> domains() + sources per item

DOMAIN OWNERSHIP (the Commandos, so she is not doing all of it herself):
  hazards        -> aegis-dugan    life-safety events: fire, quake, severe weather
  human_terrain  -> aegis-jones    places, movement, population, transit
  infrastructure -> aegis-dernier  installations, utilities, comms, physical plant
  world_state    -> aegis-falsworth what the workshop itself believes about the world
  bridge_health  -> aegis-morita   an observation from a degraded bridge is not an
                                   observation

  The owner names above are the DOMAINS dict, verbatim. An earlier version of this
  header named conceptual stages (verification/synthesis) that no domain key carried;
  the code was right and the prose was stale. Read the dict, not this comment.

HONESTY RULES (inherited from the workshop clock and the evidence tiers):
  * a failed fetch is reported as a failure with its cause, never as an empty world
  * staleness is a first-class field, not a footnote
  * the diff says "changed" only when the underlying observation changed, not when the
    projection merely refreshed
  * nothing here promotes an observation to fact; everything carries its source and age
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GEV = os.environ.get("GEV_BASE", "https://gev-host")
STATE = Path(os.environ.get("WORLD_STATE", "/var/lib/gmri/world"))
STATE.mkdir(parents=True, exist_ok=True)

# The Workshop is a place, so it has coordinates. Without an origin, "what is happening" has no
# answer -- GEV can only tell you about a point you name. This file is the Workshop's location.
ORIGIN_FILE = STATE / "origin.json"


def origin():
    """The Workshop's coordinates, or None if not yet set."""
    try:
        return json.loads(ORIGIN_FILE.read_text())
    except Exception:
        return None
TIMEOUT = float(os.environ.get("WORLD_TIMEOUT", "15"))

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# What each domain reads, and who owns it.
# ORIENTATION GATE (Boyd: O-O-D-A, and the middle O is the one that gets skipped).
# A decision head decides over a model. If the observation carries a condition that model
# cannot represent, the head's verdict carries no information about that condition --
# whatever confidence it reports. Measured 2026-11-05: laya cleared "warehouse, occupants
# unaccounted for" at 0.854 and "wildfire advancing, evacuation not started" at 0.685.
# Both were life-safety events. The gate names the unmodelled term and escalates instead.
try:
    from orientation import gate as orientation_gate
except ImportError:  # gate absent must not take the model down
    orientation_gate = None


def orient_hazard(state_text, verdict):
    """Wrap a hazards verdict in an orientation check. Absent gate => explicit unresolved."""
    if orientation_gate is None:
        return {"disposition": "GATE_UNAVAILABLE",
                "note": "orientation gate not importable; hazard verdicts are not cleared"}
    return orientation_gate(state_text, verdict, escalate_to="aegis-falsworth")


DOMAINS = {

    "hazards": {
        "owner": "aegis-dugan",
        "why": "life-safety events are the sharpest edge of the preservation mandate",
        "path": "/api/firms",
        "needs": "bbox",
    },
    "world_state": {
        "owner": "aegis-falsworth",
        "why": "what the workshop itself believes about the world",
        "path": "/api/grizos",
        "needs": None,
    },
    "bridge_health": {
        "owner": "aegis-morita",
        "why": "an observation from a degraded bridge is not an observation",
        "path": "/api/grizos/status",
        "needs": None,
    },
    "human_terrain": {
        "owner": "aegis-jones",
        "why": "places and movement: where people are and how they get there",
        "path": "/api/geocode",
        "needs": "place_query",
    },
    "infrastructure": {
        "owner": "aegis-dernier",
        "why": "the physical plant the workshop depends on",
        "path": "/api/military-installations",
        "needs": "bbox",
    },
}


def _get(path, params=None):
    """GET and decide success from the BODY, not the status.

    A 200 is not success. GEV returns HTTP 200 with an error object for several conditions
    (e.g. {"code":"OVERPASS_NOT_CONFIGURED"}). Keying on status alone would report an
    unconfigured source as healthy -- the exact false-success pattern this system forbids.
    """
    url = GEV.rstrip("/") + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
            body = r.read().decode("utf-8", "replace")
            data = json.loads(body) if body.strip() else {}
        # a 200 carrying an error object is a failure with a cause
        if isinstance(data, dict):
            code = data.get("code")
            err = data.get("error")
            if code or err:
                blocking = any(t in str(code or "") + str(err or "")
                               for t in ("NOT_CONFIGURED", "no_key", "UNAVAILABLE"))
                return {"ok": False, "status": r.status,
                        "error": str(err or code)[:300],
                        "code": code,
                        "blocked_by": "configuration" if blocking else None}
            if data.get("status") == "UNKNOWN" or data.get("results") == [] and "results" in data:
                pass  # empty result sets are legitimate ("nothing found"), not failures
        return {"ok": True, "status": r.status, "data": data}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        blocking = any(t in detail for t in ("NOT_CONFIGURED", "no_key", "requires"))
        return {"ok": False, "status": e.code,
                "error": "upstream HTTP %d" % e.code,
                "detail": detail,
                "blocked_by": "configuration" if blocking else None}
    except Exception as e:
        return {"ok": False, "status": None,
                "error": "%s: %s" % (type(e).__name__, str(e)[:160])}

def snapshot(params=None):
    """Read every domain once. Failures are recorded as failures, never as empty readings."""
    params = params or {}
    now = time.time()
    out = {
        "schema": "gmri.world.snapshot.v1",
        "taken_at": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "taken_epoch": round(now, 3),
        "source": GEV,
        "domains": {},
        "failed": [],
    }
    for name, spec in DOMAINS.items():
        reading = {"owner": spec["owner"], "why": spec["why"], "sources": {}}
        need, path = spec.get("needs"), spec["path"]
        if need == "bbox":
            lat, lon = params.get("lat"), params.get("lon")
            if not (lat and lon):
                reading["sources"][path] = {"ok": False, "error": "needs an origin to build a bbox"}
                out["failed"].append(name); out["domains"][name] = reading; continue
            # Prefer the recorded bounds (the geocoder's box for the Workshop's ZIP) over a
            # fixed +/-1 degree square: a point-plus-radius misses the urban core entirely.
            o = origin() or {}
            b = o.get("bounds") or {}
            if all(k in b for k in ("south", "west", "north", "east")):
                q = {"south": b["south"], "west": b["west"],
                     "north": b["north"], "east": b["east"]}
            else:
                d = 1.0
                q = {"south": float(lat)-d, "west": float(lon)-d,
                     "north": float(lat)+d, "east": float(lon)+d}
            reading["sources"][path] = _get(path, q)
        elif need == "place_query":
            reading["sources"][path] = _get(path, {"q": params.get("place") or "Granbury, Texas"})
        else:
            reading["sources"][path] = _get(path)
        if all(not s.get("ok") for s in reading["sources"].values()):
            blob = json.dumps(reading["sources"])
            if "NOT_CONFIGURED" in blob or "no_key" in blob:
                out.setdefault("blocked", []).append(name)
            else:
                out["failed"].append(name)
        out["domains"][name] = reading
    return out


def _fingerprint(snap):
    """A stable digest of WHAT WAS OBSERVED, excluding timestamps.

    A projection that merely refreshed has not changed. Only the observation counts.
    """
    import hashlib
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in sorted(o.items())
                    if k not in ("taken_at", "taken_epoch", "observed_at",
                                 "generated_at", "last_fetch", "snapshot_age_s")}
        if isinstance(o, list):
            return [strip(x) for x in o]
        return o
    return hashlib.sha256(json.dumps(strip(snap), sort_keys=True).encode()).hexdigest()


def _latest_path():
    files = sorted(STATE.glob("snap-*.json"))
    return files[-1] if files else None


def store(snap):
    path = STATE / ("snap-%d.json" % int(snap["taken_epoch"]))
    path.write_text(json.dumps(snap, indent=1))
    return path


def diff(current, previous):
    """What actually changed. Domain by domain, source by source."""
    out = {"schema": "gmri.world.diff.v1",
           "from": previous.get("taken_at") if previous else None,
           "to": current["taken_at"],
           "changed": [], "unchanged": [], "new_failures": [], "recovered": []}
    if not previous:
        out["note"] = "first observation -- no baseline to compare against"
        return out
    for name, reading in current["domains"].items():
        prev = (previous.get("domains") or {}).get(name)
        if prev is None:
            out["changed"].append({"domain": name, "reason": "new domain"})
            continue
        cur_fp = _fingerprint(reading)
        prev_fp = _fingerprint(prev)
        was_ok = any(s.get("ok") for s in prev.get("sources", {}).values())
        now_ok = any(s.get("ok") for s in reading.get("sources", {}).values())
        if now_ok and not was_ok:
            out["recovered"].append(name)
        elif was_ok and not now_ok:
            out["new_failures"].append(name)
        elif cur_fp != prev_fp:
            out["changed"].append({"domain": name, "owner": reading["owner"]})
        else:
            out["unchanged"].append(name)
    return out


def read_with_diff(params=None):
    snap = snapshot(params)
    prev_path = _latest_path()
    prev = json.loads(prev_path.read_text()) if prev_path else None
    d = diff(snap, prev)
    store(snap)
    return {"snapshot": snap, "diff": d,
            "baseline": prev_path.name if prev_path else None}


if __name__ == "__main__":
    lat = os.environ.get("WORLD_LAT")
    lon = os.environ.get("WORLD_LON")
    if not (lat and lon):
        o = origin()
        if o:
            lat, lon = o.get("lat"), o.get("lon")
    params = {"lat": lat, "lon": lon} if lat and lon else {}
    result = read_with_diff(params)
    print(json.dumps(result, indent=1))
