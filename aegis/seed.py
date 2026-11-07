"""Seed one agent-specific memory per AEGIS intelligence.

The content is derived from each agent's identity RECORD (not invented here): its
`intelligence_id`, its `display_name`, its behavioral `constraints` and its `salience`
lenses. Every row therefore carries tokens unique to that agent, which is what makes the
isolation matrix a real test — agent X's query for Y's constraint must not reach Y's row.

Idempotency: the server absorbs an identical save as a duplicate (200, `duplicate: true`,
same id) because the digest is a function of kind + scope + agent + content. The seeded
text is a stable function of the record, so a second run cannot mint a second row.

Usage:
    python -m aegis.seed                                  # writes the five rows
    python -m aegis.seed --print                          # show the text, write nothing
    MEMORY_TOKEN=cpk-… python -m aegis.seed --base-url http://127.0.0.1:8471
"""
from __future__ import annotations

import argparse
import os
import sys

from . import registry
from .client import DEFAULT_BASE_URL, MemoryClient, MemoryFailure


def seed_text(agent_id: str) -> str:
    """The one memory this agent is seeded with, derived from its identity record."""
    record = registry.identity(agent_id)
    constraints = ", ".join(record.get("constraints") or []) or "(none recorded)"
    salience = ", ".join(record.get("salience") or []) or "(none recorded)"
    return ("%s (%s) operating constraint: %s. Salience lenses: %s."
            % (record.get("intelligence_id") or ("aegis-" + agent_id),
               record.get("display_name") or registry.AEGIS[agent_id],
               constraints,
               salience))


def seed_query(agent_id: str) -> str:
    """A query that identifies THIS agent's seeded row and no other agent's.

    The first constraint is unique per record, so lexical recall over it can only match
    the row whose text carries it — which is exactly what the 5x5 matrix exploits.
    """
    record = registry.identity(agent_id)
    constraints = record.get("constraints") or []
    if not constraints:
        raise KeyError("agent %r has no constraints in its identity record" % agent_id)
    return str(constraints[0])


def live_row_id(client: MemoryClient, agent_id: str) -> str:
    """The id of this agent's seeded row AS IT STANDS NOW.

    A `PATCH` (repin) does not mutate the row in place: it mints a successor and marks the
    predecessor SUPERSEDED, so the id a seed returned can stop being the live row. Recall
    excludes retired/superseded rows, so the live id is the one recall returns for this
    agent's own seeded text. Falls back to the save receipt's id when recall is empty.
    """
    marker = seed_query(agent_id)
    rows = client.recall(marker, limit=20, include_invalidated=False).memories
    own = [r for r in rows if r.get("agent") == agent_id]
    for row in own:
        if marker in str(row.get("content") or ""):
            return str(row.get("id") or "")
    return str(own[0].get("id") or "") if own else ""


def seed_all(clients: dict[str, MemoryClient], write: bool = True) -> dict[str, dict]:
    """Seed every agent. Returns {agent_id: {status, id, duplicate, agent, certified}}."""
    results: dict[str, dict] = {}
    for agent_id, client in clients.items():
        text = seed_text(agent_id)
        if not write:
            results[agent_id] = {"status": 0, "id": "", "duplicate": None,
                                 "agent": agent_id, "certified": None, "content": text}
            continue
        response = client.save(text)
        payload = response.payload if isinstance(response.payload, dict) else {}
        results[agent_id] = {
            "status": response.status,
            "id": str(payload.get("id") or ""),
            "duplicate": bool(payload.get("duplicate")),
            "agent": payload.get("agent"),
            "certified": payload.get("certified"),
            "content": text,
        }
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed one memory per AEGIS agent.")
    parser.add_argument("--base-url", default=os.environ.get("MEMORY_API_URL",
                                                             DEFAULT_BASE_URL))
    parser.add_argument("--token", default=os.environ.get("MEMORY_TOKEN")
                        or os.environ.get("MEMORY_API_TOKEN") or "")
    parser.add_argument("--principal", default=os.environ.get("MEMORY_PRINCIPAL", "grizz"))
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print the derived text and write nothing")
    args = parser.parse_args(argv)
    if not args.token and not args.print_only:
        parser.error("a token is required: pass --token or set MEMORY_TOKEN")
    token = args.token or "unused-for-print"
    try:
        roster = registry.clients(args.base_url, token, principal=args.principal)
        results = seed_all(roster, write=not args.print_only)
    except (KeyError, MemoryFailure) as exc:
        print("seed failed: %s" % exc, file=sys.stderr)
        return 2
    for agent_id in registry.AGENT_IDS:
        row = results[agent_id]
        if args.print_only:
            print("%-10s %s" % (agent_id, row["content"]))
            continue
        print("%-10s %-14s id=%-11s duplicate=%-5s agent=%-10s certified=%s"
              % (agent_id, row["status"], row["id"] or "-",
                 row["duplicate"], row["agent"], row["certified"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
