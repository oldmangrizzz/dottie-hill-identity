#!/usr/bin/env python3
"""workshop_clock.py -- her time, anchored to a place.

WHY THIS AND NOT NTP. UTC is an abstraction; a number from a host. The Workshop is a real location
with weather, daylight and events. Anchoring her clock to GEV means her sense of "now" is the same
"now" as the place she works in -- not a system daemon's opinion.

Two sources, and they serve different purposes:

  LOCAL      -- the container's inherited clock. Monotonic cadence, sub-second resolution, always
                available, never dependent on the network.
  WORKSHOP   -- GEV's own observed_at/generated_at. A real-world anchor: it is what the world
                looked like at that moment, from a system that is watching the actual place.

The local clock is what she counts with. The workshop anchor is what she *knows the time by* --
and where they disagree, the disagreement is data.

Output is a JSON record she can reason over:
  now_utc, local_offset_vs_workshop_s, workshop_observed_at, workshop_generated_at,
  drift_class (ok | drifting | unavailable), source_health
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

GEV = os.environ.get("GEV_BASE", "https://gev-host")
TIMEOUT = float(os.environ.get("WORKSHOP_CLOCK_TIMEOUT", "12"))
DRIFT_OK = 2.0        # seconds: acceptable disagreement with the workshop anchor
DRIFT_WARN = 10.0     # seconds: beyond this she should stop claiming precision

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def local_now():
    return time.time()


def workshop_anchor():
    """GEV's own view of now-and-where. Returns (epoch, iso, healthy) or None."""
    try:
        req = urllib.request.Request(GEV.rstrip("/") + "/api/grizos",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
            d = json.loads(r.read().decode())
        if d.get("projection_only") is not True:
            return None
        epoch = d.get("observed_at")
        iso = (d.get("snapshot") or {}).get("generated_at")
        healthy = d.get("upstream_healthy") is not False and d.get("stale") is not True
        if isinstance(epoch, (int, float)):
            return float(epoch), iso, healthy
        return None
    except Exception:
        return None


def read():
    local = local_now()
    record = {
        "schema": "gmri.workshop.clock.v1",
        "now_utc": datetime.fromtimestamp(local, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "local_epoch": round(local, 3),
        "source_local": "container-inherited (derived from chrony-synced host)",
        "source_workshop": GEV,
        "workshop_observed_at": None,
        "workshop_generated_at": None,
        "offset_s": None,
        "drift_class": "unavailable",
        "workshop_healthy": None,
    }
    w = workshop_anchor()
    if w is None:
        record["drift_class"] = "unavailable"
        record["note"] = ("workshop anchor unreachable -- local clock still valid for cadence, "
                          "but she should not claim the world's time")
        return record

    epoch, iso, healthy = w
    offset = local - epoch
    record.update({
        "workshop_observed_at": round(epoch, 3),
        "workshop_generated_at": iso,
        "offset_s": round(offset, 3),
        "workshop_healthy": healthy,
    })
    if not healthy:
        record["drift_class"] = "drifting"
        record["note"] = "workshop anchor is stale or degraded; treat the reading as approximate"
    elif abs(offset) <= DRIFT_OK:
        record["drift_class"] = "ok"
    elif abs(offset) <= DRIFT_WARN:
        record["drift_class"] = "drifting"
        record["note"] = "local clock and workshop disagree; do not claim sub-second precision"
    else:
        record["drift_class"] = "drifting"
        record["note"] = ("local clock and workshop disagree by more than %gs -- one of them is "
                          "wrong and she should say so rather than pick" % DRIFT_WARN)
    return record


if __name__ == "__main__":
    rec = read()
    print(json.dumps(rec, indent=2))
    sys.exit(0 if rec["drift_class"] == "ok" else 1)
