"""`python3 -m dottie.continuity` — the operator's entry point into the anchor.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I4 — --anchor/--bind/--verify all act on the chain head, never a stale row.
# I9 — --token-file reads a secret from a file; nothing is inlined or echoed.
# I8 — --json prints only what the store returned.
# Mechanical move out of runtime/dottie/continuity.py — bodies are byte-identical.
# Re-exported by dottie/continuity/__init__.py so `from dottie import continuity` is unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .anchor import _content_bytes, anchor_identity, bind_model, load_anchor, verify
from .backends import _store
from .common import (
    DEFAULT_AGENT,
    DEFAULT_IDENTITY,
    DEFAULT_PRINCIPAL,
    ContinuityError,
    _sha256,
)
from ..core import NotFound
from .selftest import selftest

def _token_from(args):
    if getattr(args, "token", None):
        return args.token
    path = getattr(args, "token_file", None)
    if path:
        return Path(path).expanduser().read_text().strip()
    return None


def _resolve_store(args):
    return _store(getattr(args, "base_url", None), _token_from(args),
                  getattr(args, "mount", None), identity=args.identity,
                  engine=getattr(args, "mind_engine", None))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m dottie.continuity",
        description="Durable identity anchor: prove the identity outlives the model.")
    parser.add_argument("--selftest", action="store_true",
                        help="run the full restart / binding-change / reserved-field proof")
    parser.add_argument("--anchor", action="store_true", help="write the first anchor")
    parser.add_argument("--show", action="store_true", help="print the newest anchor")
    parser.add_argument("--verify", action="store_true",
                        help="verify the newest anchor against the lineage record")
    parser.add_argument("--bind", metavar="MODEL_ID", help="append a model binding")
    parser.add_argument("--provider", default=None, help="provider label for --bind")
    parser.add_argument("--print-anchor-digest", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--identity", default=DEFAULT_IDENTITY)
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--principal", default=DEFAULT_PRINCIPAL)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--mount", default=None, help="engine mount root (directory store)")
    parser.add_argument("--mind-engine", default=None, help="path to mind.py")
    parser.add_argument("--workdir", default=None,
                        help="scratch root for the selftest's private mount")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        if args.print_anchor_digest:
            anchor = load_anchor(store=_resolve_store(args), identity=args.identity)
            if anchor is None:
                print("ANCHOR-MISSING", flush=True)
                os._exit(8)
            print("ANCHOR-SHA256 " + _sha256(_content_bytes(anchor).decode("utf-8")),
                  flush=True)
            # The contract is recovery by a process that does NOT get to unwind: no atexit,
            # no finally, no interpreter cleanup. Only the digest above leaves.
            os._exit(9)
        if args.selftest:
            ok, _ = selftest(args.base_url, _token_from(args), args.mount,
                             identity=args.identity, agent=args.agent,
                             principal=args.principal, engine=args.mind_engine,
                             workdir=args.workdir)
            return 0 if ok else 1
        store = _resolve_store(args)
        if args.anchor:
            anchor = anchor_identity(store=store, identity=args.identity)
            print(json.dumps(anchor, indent=2, sort_keys=True))
            return 0
        anchor = load_anchor(store=store, identity=args.identity)
        if anchor is None:
            print("no anchor in the store; run --anchor", file=sys.stderr)
            return 3
        if args.bind:
            anchor = bind_model(anchor, args.bind, store=store, provider=args.provider,
                               identity=args.identity)
            print(json.dumps(anchor, indent=2, sort_keys=True))
            return 0
        if args.verify:
            mismatches = verify(anchor)
            print(json.dumps({"row": anchor.get("anchor_row_id"),
                              "backend": anchor.get("anchor_backend"),
                              "mismatches": mismatches}, indent=2, sort_keys=True))
            return 0 if not mismatches else 1
        print(json.dumps(anchor, indent=2, sort_keys=True))
        return 0
    except (ContinuityError, NotFound, ValueError) as exc:
        print("continuity: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
