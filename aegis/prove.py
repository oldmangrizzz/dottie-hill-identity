"""Prove AEGIS memory isolation against the LIVE service, one PASS/FAIL per row.

Every assertion here is AGENT-SCOPED. The mount is shared with other writers (the
identity-continuity workstream stamps `X-Dottie-Agent: dottie-hill`), so no global total is
ever asserted: each check asks "does agent X's scoped result contain / omit row R", never
"how many rows exist overall".

What is proven:
  1. save    — each agent's save answers 201 (200 on an absorbed duplicate), row `agent` is
               that agent, `certified` is true
  2. own     — each agent recalls its own row by its own distinctive constraint
  3. matrix  — the full 5x5: X recalling Y's constraint (Y != X) never returns Y's row
  4. list    — `GET /api/memories` per agent: own rows + unstamped principal-level rows only
  5. mutate  — X PATCH/DELETE Y's row -> 403 naming both agents; X PATCH own -> 200
  6. unscope — no `X-Dottie-Agent` -> 400 on a memory route (scoping is not optional)
  7. legacy  — a pre-existing UNSTAMPED row is visible to every agent, carries no `agent`
               key, and is un-mutable by any agent (403 "no agent stamp"). Documented
               consequence of turning scoping ON over an existing mount. Not fixed here.
  8. gate    — `/info` still reports gate_exit 0 (and, with --audit-host, `mind.py audit`
               still prints GATES 27/27): isolation must not break certification.

Usage:
    MEMORY_TOKEN=cpk-… python -m aegis.prove
    MEMORY_TOKEN=cpk-… python -m aegis.prove --audit-host root@memory-host
    MEMORY_TOKEN=cpk-… python -m aegis.prove --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from . import registry, seed
from .client import (DEFAULT_BASE_URL, MemoryClient, MemoryDenied, MemoryFailure,
                     MemoryHTTPError)

PASS = "PASS"
FAIL = "FAIL"


class Proof:
    """Collects rows: (verdict, label, status, evidence). Prints as it goes."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, ok: bool, label: str, status: object, evidence: str) -> bool:
        row = {"verdict": PASS if ok else FAIL, "label": label,
               "status": status, "evidence": evidence}
        self.rows.append(row)
        print("%-4s  %-58s  status=%-5s  %s"
              % (row["verdict"], label, status, evidence), flush=True)
        return ok

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.rows if r["verdict"] == FAIL]

    def summary(self) -> str:
        total = len(self.rows)
        bad = len(self.failed)
        return "%d/%d PASS" % (total - bad, total)


def _ids(rows: list) -> list[str]:
    return [str(r.get("id") or "") for r in rows]


def _describe(rows: list, limit: int = 3) -> str:
    """A compact, identity-carrying view of what a scoped read actually returned."""
    parts = []
    for row in rows[:limit]:
        stamp = row.get("agent") or "UNSTAMPED"
        parts.append("%s/%s" % (stamp, row.get("id") or "?"))
    extra = "" if len(rows) <= limit else " (+%d more)" % (len(rows) - limit)
    return "[" + ", ".join(parts) + "]" + extra if parts else "[]"


def _error_of(exc: Exception) -> str:
    if isinstance(exc, MemoryHTTPError):
        return exc.excerpt(180)
    return str(exc)[:180]


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #
def phase_save(proof: Proof, clients: dict[str, MemoryClient], write: bool) -> dict[str, str]:
    """Seed one agent-specific row per agent; return {agent: row id}."""
    print("\n== 1. SAVE (one agent-specific memory per agent) ==")
    ids: dict[str, str] = {}
    for agent_id in registry.AGENT_IDS:
        client = clients[agent_id]
        text = seed.seed_text(agent_id)
        try:
            response = client.save(text) if write else client.recall(seed.seed_query(agent_id))
        except MemoryFailure as exc:
            proof.record(False, "save %s" % agent_id, getattr(exc, "status", "-"), _error_of(exc))
            continue
        payload = response.payload if isinstance(response.payload, dict) else {}
        row_id = str(payload.get("id") or "")
        # The SAVE route reports the mount's gate state under `audit`; `certified` is the
        # per-row label only a READ returns (`_row(..., certify=...)`). Both are checked:
        # here the write-time mount state, then the read-back label in phase_recall_own.
        audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
        ok_status = response.status in (200, 201)
        ok_agent = payload.get("agent") == agent_id
        ok_cert = audit.get("gate_exit") == 0
        # The id the LIVE row has now: a prior PATCH would have superseded the id this
        # save absorbs into, and the matrix must target the row that recall can return.
        live = seed.live_row_id(client, agent_id) or row_id
        ids[agent_id] = live
        ok = ok_status and ok_agent and ok_cert
        proof.record(ok, "save %s" % agent_id, response.status,
                     "id=%s duplicate=%s agent=%r audit.gate_exit=%s"
                     % (live or "-", payload.get("duplicate"), payload.get("agent"),
                        audit.get("gate_exit")))
    return ids


def phase_recall_own(proof: Proof, clients: dict[str, MemoryClient],
                     ids: dict[str, str]) -> None:
    print("\n== 2. RECALL OWN (own distinctive constraint) ==")
    for agent_id in registry.AGENT_IDS:
        client = clients[agent_id]
        try:
            response = client.recall(seed.seed_query(agent_id), limit=20)
        except MemoryFailure as exc:
            proof.record(False, "recall-own %s" % agent_id, getattr(exc, "status", "-"),
                         _error_of(exc))
            continue
        rows = response.memories
        present = ids[agent_id] in _ids(rows) if ids[agent_id] else False
        own_row = next((r for r in rows if r.get("id") == ids[agent_id]), {})
        certified = own_row.get("certified")
        foreign = [r for r in rows if r.get("agent") not in (None, agent_id)]
        unstamped = [r for r in rows if "agent" not in r]
        proof.record(response.status == 200 and present and certified is True and not foreign,
                     "recall-own %s" % agent_id, response.status,
                     "own_id_present=%s certified=%s foreign=%d unstamped=%d %s"
                     % (present, certified, len(foreign), len(unstamped), _describe(rows)))


def phase_matrix(proof: Proof, clients: dict[str, MemoryClient],
                 ids: dict[str, str]) -> list[list[str]]:
    """The full 5x5. Cell [x][y] = verdict for 'X's scoped read omits Y's row'."""
    print("\n== 3. ISOLATION MATRIX 5x5 (X recalls Y's constraint; Y's row ABSENT) ==")
    roster = registry.AGENT_IDS
    grid: list[list[str]] = []
    for x in roster:
        line: list[str] = []
        for y in roster:
            if x == y:
                line.append("self")
                continue
            client = clients[x]
            try:
                response = client.recall(seed.seed_query(y), limit=20)
            except MemoryFailure as exc:
                line.append(FAIL)
                proof.record(False, "matrix %s<-query(%s)" % (x, y),
                             getattr(exc, "status", "-"), _error_of(exc))
                continue
            returned = _ids(response.memories)
            absent = ids[y] not in returned if ids[y] else False
            # Anything stamped with a FOREIGN agent would be a leak even if it is not Y's
            # seeded row; the assertion is "Y's row is absent", this is the wider check.
            foreign = [r for r in response.memories
                       if r.get("agent") not in (None, x)]
            ok = response.status == 200 and absent and not foreign
            line.append(PASS if ok else FAIL)
            proof.record(ok, "matrix %s <- query(%s)" % (x, y), response.status,
                         "Y_row_absent=%s foreign_rows=%d %s"
                         % (absent, len(foreign), _describe(response.memories, 2)))
        grid.append(line)
    print("\n    reader \\ query-source   " + "  ".join("%-10s" % a for a in roster))
    for x, line in zip(roster, grid):
        print("    %-22s" % x + "  ".join("%-10s" % c for c in line))
    return grid


def phase_list(proof: Proof, clients: dict[str, MemoryClient],
               ids: dict[str, str]) -> dict[str, dict]:
    """Per-agent GET: own rows + unstamped principal-level rows, never a foreign agent's."""
    print("\n== 4. GET /api/memories PER AGENT (agent-scoped counts) ==")
    counts: dict[str, dict] = {}
    for agent_id in registry.AGENT_IDS:
        try:
            response = clients[agent_id].list()
        except MemoryFailure as exc:
            proof.record(False, "list %s" % agent_id, getattr(exc, "status", "-"),
                         _error_of(exc))
            continue
        rows = response.memories
        own = [r for r in rows if r.get("agent") == agent_id]
        unstamped = [r for r in rows if "agent" not in r]
        foreign = [r for r in rows if r.get("agent") not in (None, agent_id)]
        counts[agent_id] = {"status": response.status, "visible": len(rows),
                            "own": len(own), "unstamped": len(unstamped),
                            "foreign": len(foreign), "own_ids": _ids(own)}
        proof.record(response.status == 200 and not foreign,
                     "list %s" % agent_id, response.status,
                     "visible=%d own=%d unstamped=%d foreign=%d"
                     % (len(rows), len(own), len(unstamped), len(foreign)))
    return counts


def phase_mutate(proof: Proof, clients: dict[str, MemoryClient], ids: dict[str, str]) -> str:
    """403 on every ordered foreign pair (PATCH and DELETE); 200 on the owner's own PATCH."""
    print("\n== 5. MUTATION BOUNDARY (403 foreign, 200 own) ==")
    roster = registry.AGENT_IDS
    for x in roster:
        for y in roster:
            if x == y or not ids[y]:
                continue
            target = ids[y]
            for verb in ("PATCH", "DELETE"):
                client = clients[x]
                try:
                    if verb == "PATCH":
                        client.patch(target, seed.seed_text(y) + " [denied probe]",
                                     reason="aegis isolation probe")
                    else:
                        client.delete(target, reason="aegis isolation probe")
                except MemoryDenied as exc:
                    payload = exc.payload if isinstance(exc.payload, dict) else {}
                    message = str(payload.get("error") or "")
                    row_agent = payload.get("row_agent")
                    names_both = y in message and (x in message or row_agent == y)
                    proof.record(exc.status == 403 and names_both,
                                 "%s %s -> %s's row" % (verb, x, y), exc.status,
                                 "names_both=%s row_agent=%r: %s"
                                 % (names_both, row_agent, message[:110]))
                except MemoryFailure as exc:
                    proof.record(False, "%s %s -> %s's row" % (verb, x, y),
                                 getattr(exc, "status", "-"), _error_of(exc))
                else:
                    proof.record(False, "%s %s -> %s's row" % (verb, x, y), "2xx",
                                 "the server ALLOWED a foreign mutation")

    # Positive control: the owner replacing its own row must succeed. The successor is one
    # extra row per mount, not per run: a re-run finds it already materialised.
    print("\n    -- positive control: dugan PATCHes its own row --")
    owner = clients["dugan"]
    successor_text = seed.seed_text("dugan") + " [positive-control successor]"
    try:
        response = owner.patch(ids["dugan"], successor_text,
                               reason="aegis positive control (owner replaces own row)")
        payload = response.payload if isinstance(response.payload, dict) else {}
        ok = (response.status == 200 and payload.get("agent") == "dugan"
              and payload.get("retiredId") == ids["dugan"])
        proof.record(ok, "PATCH dugan -> dugan's own row", response.status,
                     "new_id=%s retiredId=%s agent=%r superseded=%s"
                     % (payload.get("id"), payload.get("retiredId"), payload.get("agent"),
                        payload.get("superseded")))
        return str(payload.get("id") or "")
    except MemoryHTTPError as exc:
        # A prior run already minted the successor: the digest rule refuses identical bytes.
        if "already a memory" in str(exc.payload):
            rows = owner.recall(seed.seed_query("dugan"), limit=20).memories
            found = [r for r in rows if r.get("content") == successor_text]
            if found:
                proof.record(found[0].get("agent") == "dugan",
                             "PATCH dugan -> own row (already materialised)", exc.status,
                             "successor id=%s agent=%r: %s"
                             % (found[0].get("id"), found[0].get("agent"), exc.excerpt(90)))
                return str(found[0].get("id") or "")
        proof.record(False, "PATCH dugan -> dugan's own row", exc.status, exc.excerpt(160))
        return ""
    except MemoryFailure as exc:
        proof.record(False, "PATCH dugan -> dugan's own row", getattr(exc, "status", "-"),
                     _error_of(exc))
        return ""


def phase_unscoped(proof: Proof, clients: dict[str, MemoryClient]) -> None:
    print("\n== 6. NO X-Dottie-Agent -> 400 (scoping is enforced, not optional) ==")
    client = clients["dugan"]
    for method, path, body in (("GET", "/api/memories", None),
                               ("POST", "/api/memories",
                                {"content": "aegis unscoped probe (must not be stored)"}),
                               ("POST", "/api/memories/recall", {"query": "aegis"})):
        try:
            response = client.raw(method, path, body, omit_agent=True)
        except MemoryFailure as exc:
            proof.record(False, "unscoped %s %s" % (method, path),
                         getattr(exc, "status", "-"), _error_of(exc))
            continue
        payload = response.payload if isinstance(response.payload, dict) else {}
        message = str(payload.get("error") or "")
        proof.record(response.status == 400 and "X-Dottie-Agent" in message,
                     "unscoped %s %s" % (method, path), response.status,
                     message[:110])


def phase_legacy(proof: Proof, clients: dict[str, MemoryClient]) -> dict:
    """A pre-scoping row: readable by every agent, unstamped, un-mutable. Not fixed."""
    print("\n== 7. PRE-EXISTING UNSTAMPED ROW (documented consequence) ==")
    rows = clients["dugan"].list().memories
    legacy = [r for r in rows if "agent" not in r]
    if not legacy:
        proof.record(False, "legacy unstamped row present", "-",
                     "none found: this mount has no pre-scoping rows to characterise")
        return {}
    target = legacy[0]
    row_id = str(target.get("id") or "")
    content = str(target.get("content") or "")
    query = " ".join(content.split()[:4]) or content
    seen: dict[str, bool] = {}
    for agent_id in ("dugan", "jones"):
        response = clients[agent_id].recall(query, limit=20)
        visible = row_id in _ids(response.memories)
        stamped = [r for r in response.memories if r.get("id") == row_id]
        has_agent_key = any("agent" in r for r in stamped)
        seen[agent_id] = visible and not has_agent_key
        proof.record(visible and not has_agent_key,
                     "legacy %s visible to %s, no agent key" % (row_id, agent_id),
                     response.status,
                     "visible=%s agent_key=%s content=%r"
                     % (visible, has_agent_key, content[:60]))
    for agent_id in ("dugan", "jones"):
        try:
            clients[agent_id].patch(row_id, content + " [must not apply]",
                                    reason="aegis legacy probe")
        except MemoryDenied as exc:
            message = str((exc.payload or {}).get("error") or "")
            proof.record("no agent stamp" in message,
                         "legacy %s PATCH by %s -> 403" % (row_id, agent_id), exc.status,
                         message[:130])
        except MemoryFailure as exc:
            proof.record(False, "legacy %s PATCH by %s -> 403" % (row_id, agent_id),
                         getattr(exc, "status", "-"), _error_of(exc))
        else:
            proof.record(False, "legacy %s PATCH by %s -> 403" % (row_id, agent_id), "2xx",
                         "an agent MUTATED a principal-level row")
    return {"id": row_id, "content": content, "query": query}


def phase_gate(proof: Proof, client: MemoryClient, audit_host: str | None) -> dict:
    print("\n== 8. CERTIFICATION AFTER ISOLATION ==")
    info = client.info()
    payload = info.payload if isinstance(info.payload, dict) else {}
    audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
    gate_exit = audit.get("gate_exit")
    proof.record(gate_exit == 0, "/info gate_exit", info.status,
                 "gate_exit=%s failing=%s agent_scoping=%s"
                 % (gate_exit, audit.get("failing"), payload.get("agent_scoping")))
    out = {"gate_exit": gate_exit, "audit_line": ""}
    if audit_host:
        try:
            done = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", audit_host,
                 "cd /opt/dottie-memory && python3 mind.py audit /var/lib/dottie-memory"],
                capture_output=True, text=True, timeout=300)
            tail = [line for line in (done.stdout or "").splitlines() if line.strip()]
            line = next((t for t in reversed(tail) if "GATES" in t.upper()), "")
            out["audit_line"] = line.strip()
            proof.record(done.returncode == 0 and "27/27" in line,
                         "mind.py audit (%s)" % audit_host, done.returncode,
                         (line or (done.stdout or done.stderr or "")[-160:]).strip())
        except (OSError, subprocess.SubprocessError) as exc:
            proof.record(False, "mind.py audit (%s)" % audit_host, "-", str(exc)[:160])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prove AEGIS memory isolation, live.")
    parser.add_argument("--base-url", default=os.environ.get("MEMORY_API_URL",
                                                             DEFAULT_BASE_URL))
    parser.add_argument("--token", default=os.environ.get("MEMORY_TOKEN")
                        or os.environ.get("MEMORY_API_TOKEN") or "")
    parser.add_argument("--principal", default=os.environ.get("MEMORY_PRINCIPAL", "grizz"))
    parser.add_argument("--audit-host", default=os.environ.get("MEMORY_AUDIT_HOST", ""),
                        help="ssh host to run `mind.py audit` on (optional)")
    parser.add_argument("--no-write", action="store_true",
                        help="prove with reads only (rows must already be seeded)")
    parser.add_argument("--json", action="store_true", help="emit the full result as JSON")
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("a token is required: pass --token or set MEMORY_TOKEN")

    roster = registry.clients(args.base_url, args.token, principal=args.principal)
    proof = Proof()
    probe = roster["dugan"]
    try:
        probe.info()
    except MemoryFailure as exc:
        print("dottie-memory unreachable at %s: %s" % (args.base_url, exc), file=sys.stderr)
        return 2

    ids = phase_save(proof, roster, write=not args.no_write)
    phase_recall_own(proof, roster, ids)
    grid = phase_matrix(proof, roster, ids)
    counts = phase_list(proof, roster, ids)
    successor = phase_mutate(proof, roster, ids)
    phase_unscoped(proof, roster)
    legacy = phase_legacy(proof, roster)
    gate = phase_gate(proof, probe, args.audit_host or None)

    print("\n== SUMMARY == %s" % proof.summary())
    for row in proof.failed:
        print("   FAILED: %s (status=%s) %s" % (row["label"], row["status"], row["evidence"]))
    if args.json:
        print(json.dumps({"rows": proof.rows, "grid": grid, "ids": ids,
                          "counts": counts, "legacy": legacy, "gate": gate,
                          "successor_id": successor,
                          "summary": proof.summary()}, indent=1))
    return 0 if not proof.failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
