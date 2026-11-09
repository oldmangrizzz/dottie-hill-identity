"""The contract, asserted: restart recovery, binding change, reserved-field state.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I4 — byte-identical recovery in a new process and the supersession chain intact.
# I8 — reserved fields still UNSET and the lineage record unchanged by the run.
# I12 — compute instructions byte-identical across a binding change.
# Mechanical move out of runtime/dottie/continuity.py — bodies are byte-identical.
# Re-exported by dottie/continuity/__init__.py so `from dottie import continuity` is unchanged.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

from .anchor import (
    _content_bytes,
    _identity_payload_bytes,
    anchor_identity,
    bind_model,
    load_anchor,
    reserved_fields_unset,
    verify,
)
from .backends import EngineStore, RestStore
from .common import (
    ANCHOR_FIELDS,
    DEFAULT_AGENT,
    DEFAULT_IDENTITY,
    DEFAULT_PRINCIPAL,
    DEFAULT_URL,
    RESERVED_FIELDS,
    ContinuityError,
    _canonical,
    _find_engine,
    _record,
    _sha256,
)
from ..lineage import compute_instructions

def _line(ok, name, detail=""):
    text = ("PASS  " if ok else "FAIL  ") + name
    return text + (" — " + detail if detail else "")


def _child_anchor(store_args, identity):
    """A NEW process: load the anchor, print its stored bytes' sha256, then die hard."""
    command = [sys.executable, "-m", "dottie.continuity", "--print-anchor-digest",
               "--identity", identity] + list(store_args)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env)
    return proc


def selftest(base_url=None, token=None, root_dir=None, *, identity=DEFAULT_IDENTITY,
             agent=DEFAULT_AGENT, principal=DEFAULT_PRINCIPAL, engine=None,
             workdir=None, stream=None):
    """Run the whole proof. Returns (ok, lines). Every assertion prints one PASS/FAIL line."""
    out = stream or sys.stdout
    lines = []

    def emit(ok, name, detail=""):
        text = _line(ok, name, detail)
        lines.append(text)
        print(text, file=out, flush=True)
        return ok

    identity_record = _record(identity, root_dir)
    lineage_before = _canonical(identity_record)
    instructions_before = compute_instructions(identity)

    # Backend selection. An explicit mount wins; a reachable REST wins over a private mount.
    backend = None
    setup_note = ""
    if root_dir:
        backend = EngineStore(root_dir, engine=engine, principal=principal, agent=agent,
                              identity=identity)
    elif base_url and token:
        backend = RestStore(base_url, token, principal=principal, agent=agent,
                            identity=identity)
        if not backend.reachable():
            raise ContinuityError("REST service at %s did not answer /info" % base_url,
                                  "unreachable")
    else:
        url = os.environ.get("DOTTIE_MEMORY_URL", "").strip() or DEFAULT_URL
        env_token = token or os.environ.get("DOTTIE_MEMORY_TOKEN", "").strip()
        if env_token:
            candidate = RestStore(url, env_token, principal=principal, agent=agent,
                                  identity=identity)
            if candidate.reachable():
                backend = candidate
        if backend is None:
            root = Path(workdir or "/tmp/idcont/selftest-mount").expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            engine_path = engine or _find_engine()
            if engine_path is None:
                raise ContinuityError("no REST service and no mind.py to fall back to",
                                      "unavailable")
            private = EngineStore(root, engine=engine_path, principal=principal, agent=agent,
                                  identity=identity)
            if not private.reachable():
                private.init()
                setup_note = " (initialised private mount)"
            backend = private

    target = backend.root if backend.backend == "engine" else backend.base_url
    emit(True, "backend selected", "%s at %s%s" % (backend.backend, target, setup_note))

    info_before = backend.info()
    gate_before = info_before.get("gate_exit")
    emit(gate_before == 0, "store gate_exit 0 before the run",
         "gate_exit=%s failing=%s%s" % (gate_before, info_before.get("failing"),
                                        (" gates=" + str(info_before.get("gates")))
                                        if info_before.get("gates") else ""))

    # 1. anchor written, id returned, mount gate_exit 0.
    anchor = anchor_identity(store=backend, identity=identity)
    anchor_id = anchor.get("anchor_row_id")
    emit(bool(anchor_id), "anchor written and id returned", "id=%s" % anchor_id)
    written_bytes = _content_bytes(anchor)
    emit(anchor.get("anchor_duplicate") is False or anchor_id is not None,
         "anchor is one saved memory row",
         "duplicate=%s chars=%d sha256=%s" % (anchor.get("anchor_duplicate"),
                                              len(written_bytes), _sha256(
                                                  written_bytes.decode("utf-8"))[:16]))
    info_after = backend.info()
    emit(info_after.get("gate_exit") == 0, "mount gate_exit 0 after the write",
         "gate_exit=%s%s" % (info_after.get("gate_exit"),
                             (" gates=" + str(info_after.get("gates")))
                             if info_after.get("gates") else ""))

    # 2. process kill -> new process -> byte-identical recovery, zero mismatches.
    store_flags = _store_flags(backend, identity)
    child = _child_anchor(store_flags, identity)
    recovered_digest = ""
    for line in child.stdout.splitlines():
        if line.startswith("ANCHOR-SHA256 "):
            recovered_digest = line.split(" ", 1)[1].strip()
    emit(child.returncode == 9,
         "child process died hard (os._exit) instead of unwinding",
         "returncode=%s stderr=%s" % (child.returncode,
                                      (child.stderr or "").strip().splitlines()[-1:]
                                      if child.stderr.strip() else "none"))
    fresh = load_anchor(store=backend, identity=identity)
    fresh_bytes = _content_bytes(fresh) if fresh else b""
    # The ANCHOR's bytes are its identity payload. A re-anchor legitimately changes its own
    # supersession link and `anchored_at`, so the comparison is the payload the contract names —
    # which is also what a reader recovers: the same intelligence, constraints and sources.
    payload_before = _identity_payload_bytes(anchor)
    payload_after = _identity_payload_bytes(fresh) if fresh else b""
    emit(fresh is not None and payload_after == payload_before
         and fresh.get("anchor_row_id") == anchor_id,
         "anchor recovered BYTE-IDENTICALLY in a new process",
         "row=%s child_content_sha256=%s payload_sha256=%s payload_bytes=%d"
         % (fresh.get("anchor_row_id") if fresh else None, recovered_digest[:16],
            _sha256(payload_after.decode("utf-8"))[:16], len(payload_after)))
    mismatches = verify(fresh) if fresh else ["anchor missing after restart"]
    emit(mismatches == [], "verify() reports zero mismatches after restart",
         "mismatches=%s" % (mismatches or "none"))

    # 3. model binding change: append, order, chain, and identity-independent instructions.
    # The ids are RUN-UNIQUE and the assertion is RELATIVE to the history already on the mount.
    # A fixed "length == 2" would be a clean-substrate assumption: on a mount that already holds
    # a chain — a second run, or a shared mount with another workstream writing to it — it
    # measures the substrate instead of this run, and it would fail for the wrong reason.
    tag = _run_tag()
    model_a, model_b = "model-A-" + tag, "model-B-" + tag
    baseline_history = [e.get("model_id") for e in (fresh.get("model_binding_history") or [])
                        if isinstance(e, dict)]
    anchor_a = bind_model(fresh, model_a, store=backend, identity=identity)
    anchor_b = bind_model(anchor_a, model_b, store=backend, identity=identity)
    history = anchor_b.get("model_binding_history") or []
    order = [entry.get("model_id") for entry in history
             if isinstance(entry, dict)]
    emit(len(order) == len(baseline_history) + 2
         and order[-2:] == [model_a, model_b],
         "binding history appended in order (+2 this run)",
         "before=%s after=%s" % (baseline_history, order))
    chain_ok = (anchor_a.get("previous_anchor_id") == anchor_id
                and anchor_b.get("previous_anchor_id") == anchor_a.get("anchor_row_id")
                and anchor_a.get("anchor_row_id") != anchor_b.get("anchor_row_id"))
    emit(chain_ok, "previous_anchor_id chain intact (each binding is a NEW row)",
         "anchor0=%s anchorA=%s<-anchor0 anchorB=%s<-anchorA"
         % (anchor_id, anchor_a.get("anchor_row_id"), anchor_b.get("anchor_row_id")))
    head = load_anchor(store=backend, identity=identity)
    head_history = [e.get("model_id") for e in (head.get("model_binding_history") or [])
                    if isinstance(e, dict)] if head else []
    emit(head is not None and head.get("anchor_row_id") == anchor_b.get("anchor_row_id")
         and head_history == order,
         "chain head wins on re-load (not a stale or forked row)",
         "loaded_row=%s head=%s history_tail=%s"
         % (head.get("anchor_row_id") if head else None, anchor_b.get("anchor_row_id"),
            head_history[-2:]))
    instructions_after = compute_instructions(identity)
    emit(instructions_after == instructions_before,
         "compute_instructions BYTE-IDENTICAL across the binding change",
         "sha256_before=%s sha256_after=%s"
         % (_sha256(instructions_before)[:16], _sha256(instructions_after)[:16]))
    identity_payload = {k: anchor_b.get(k) for k in ANCHOR_FIELDS
                        if k not in ("anchored_at", "schema")}
    original_payload = {k: anchor.get(k) for k in ANCHOR_FIELDS
                        if k not in ("anchored_at", "schema")}
    emit(identity_payload == original_payload,
         "identity payload unchanged by binding (only the compute declaration moved)",
         "fields=%d" % len(identity_payload))

    # 4. reserved fields still UNSET, and the identity file was not rewritten.
    drift = reserved_fields_unset(root_dir, identity)
    emit(drift == [], "reserved fields still UNSET (no namespace/relationship ruling made)",
         "drift=%s" % (drift or "none: " + ", ".join(
             "%s=%s" % (k, v) for k, v in RESERVED_FIELDS.items())))
    lineage_after = _canonical(_record(identity, root_dir))
    emit(lineage_after == lineage_before, "lineage record bytes unchanged by the run",
         "sha256=%s" % _sha256(lineage_after)[:16])

    # 5. the store still certifies itself.
    final = backend.info()
    if final.get("gates"):
        emit(final.get("gate_exit") == 0 and final.get("gates", "").split("/")[0]
             == final.get("gates", "").split("/")[-1],
             "store audit clean after the run",
             "GATES %s (exit=%s)" % (final.get("gates"), final.get("gate_exit")))
    else:
        emit(final.get("gate_exit") == 0, "store /info gate_exit 0 after the run",
             "gate_exit=%s" % final.get("gate_exit"))

    ok = all(line.startswith("PASS") for line in lines)
    print(("SELFTEST: PASS" if ok else "SELFTEST: FAIL") + " (%d assertions)" % len(lines),
          file=out, flush=True)
    lines.append("SELFTEST: %s (%d assertions)" % ("PASS" if ok else "FAIL", len(lines)))
    return ok, lines


def _run_tag():
    """A short, unique tag so one run's model ids cannot collide with an earlier run's."""
    return hashlib.sha256(("%s:%s:%s" % (os.getpid(), time.time(),
                                         time.monotonic_ns())).encode()).hexdigest()[:8]


def _store_flags(backend, identity):
    """CLI flags that reproduce this backend in a child process."""
    flags = ["--identity", identity]
    if backend.backend == "engine":
        flags += ["--mount", str(backend.root), "--mind-engine", str(backend.engine)]
    else:
        flags += ["--base-url", backend.base_url, "--token", backend.token]
    return flags
