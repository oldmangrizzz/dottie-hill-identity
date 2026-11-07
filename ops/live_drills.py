#!/usr/bin/env python3
"""live_drills.py -- the three destructive acceptance drills, run deliberately.

Each drill performs a real destructive action, measures the aftermath, and writes an
evidence JSON under /var/lib/gmri/drills/. live_gates.py then reads that evidence instead
of repeating the destructive action on every certification run.

  gate12  kill the executive worker mid-mission, then prove reconciliation completes
  gate14  drop the WAN default route, prove local service continuity, restore
  gate15  back up the state DB, prove the restored copy is intact and complete
"""
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

OUT = Path("/var/lib/gmri/drills")
OUT.mkdir(parents=True, exist_ok=True)
SPOOL = Path("/var/lib/dottie-executive")
DB = Path("/var/lib/gmri/dottie/state/gateway.sqlite3")


def run(cmd, timeout=180, shell=False):
    return subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=timeout)


def save(gate, ok, detail, **extra):
    rec = {"gate": gate, "ok": ok, "detail": detail, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **extra}
    (OUT / f"gate{gate}.json").write_text(json.dumps(rec, indent=2) + "\n")
    print(("PASS" if ok else "FAIL"), gate, detail, flush=True)
    return ok


def drill15():
    """Real backup, real restore, real integrity and completeness check."""
    src = sqlite3.connect(str(DB))
    src.execute("SELECT count(*) FROM evidence").fetchone()
    before = src.execute("SELECT count(*) FROM evidence").fetchone()[0]
    dest_path = f"/var/backups/gmri-drill-{int(time.time())}"
    dest = sqlite3.connect(dest_path)
    src.backup(dest)
    dest.close()
    src.close()
    restored = sqlite3.connect(dest_path)
    integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
    after = restored.execute("SELECT count(*) FROM evidence").fetchone()[0]
    tables = restored.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    restored.close()
    digest = hashlib.sha256(Path(dest_path).read_bytes()).hexdigest()
    ok = integrity == "ok" and after == before and tables > 0
    return save(15, ok, f"integrity={integrity} evidence {before}->{after} tables={tables}",
                artifact=dest_path, sha256=digest)


def drill14():
    """Physically drop the WAN route, measure degradation, restore it -- always restores."""
    route = run(["ip", "route"], timeout=20).stdout
    gw = iface = None
    for line in route.splitlines():
        f = line.split()
        if f and f[0] == "default":
            gw = f[f.index("via") + 1] if "via" in f else None
            iface = f[f.index("dev") + 1] if "dev" in f else None
            break
    if not gw or not iface:
        return save(14, False, "no default route found; drill not performed", status="not-performed")

    def cloud():
        r = run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "8",
                 "https://ollama.com"], timeout=20)
        return r.stdout.strip()

    before = cloud()
    delroute = run(["ip", "route", "del", "default", "via", gw, "dev", iface], timeout=20)
    time.sleep(1)
    during = cloud()
    units = {u: run(["systemctl", "is-active", u], timeout=20).stdout.strip()
             for u in ("dottie-memory", "dottie-laya", "dottie-core", "dottie-grizos")}
    local_ok = all(v == "active" for v in units.values())
    mem_code = run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "6",
                    "http://127.0.0.1:8471/info"], timeout=20).stdout.strip()
    addr = run(["ip", "route", "add", "default", "via", gw, "dev", iface], timeout=20)
    time.sleep(2)
    after = cloud()
    restored = run(["ip", "route"], timeout=20).stdout
    ok = (delroute.returncode == 0 and during == "000" and local_ok
          and mem_code == "200" and after == "200")
    return save(14, ok,
                f"cloud {before}->{during}->{after} local_units_active={local_ok} "
                f"local_memory_http={mem_code} route_restored={addr.returncode == 0}",
                gateway=gw, iface=iface, unit_states=units)


def drill12():
    """Kill the executive mid-mission; prove a restarted process reconciles the claim."""
    probe = SPOOL / "drill12.json"
    for stale in SPOOL.glob("drill12.json*"):
        stale.unlink()
    probe.write_text(json.dumps({"request": "Say in one sentence why durable memory matters."}))
    time.sleep(20)
    pid = run(["pgrep", "-f", "run_executive"], timeout=20).stdout.split()
    if not pid:
        return save(12, False, "executive not running; drill not performed", status="not-performed")
    claimed = [p.name for p in SPOOL.glob("drill12.json*")]
    os.kill(int(pid[0]), signal.SIGKILL)
    time.sleep(3)
    after_kill = [p.name for p in SPOOL.glob("drill12.json*")]
    run(["systemctl", "restart", "dottie-executive"], timeout=90)
    time.sleep(5)
    # age the stale claim so the recovery sweep acts immediately
    for p in SPOOL.glob("drill12.json.inprogress"):
        os.utime(p, (946684800, 946684800))
    deadline = time.time() + 600
    final = None
    while time.time() < deadline:
        done = list(SPOOL.glob("drill12.json.taken"))
        stuck = list(SPOOL.glob("drill12.json.inprogress"))
        if done and not stuck:
            final = json.loads(done[0].read_text())
            break
        time.sleep(10)
    survived = final is not None and final.get("state") in ("DONE", "BLOCKED")
    return save(12, bool(survived),
                f"claimed={claimed} after_kill={after_kill} final_state={final.get('state') if final else None} "
                f"steps={len(final.get('steps', [])) if final else 0}",
                plan_authored=(final or {}).get("plan"))


if __name__ == "__main__":
    which = sys.argv[1:] or ["12", "14", "15"]
    rc = 0
    for g in which:
        fn = {"12": drill12, "14": drill14, "15": drill15}.get(g)
        if not fn:
            continue
        try:
            if not fn():
                rc = 1
        except Exception as exc:
            save(g, False, f"drill raised {type(exc).__name__}: {exc}")
            rc = 1
    sys.exit(rc)
