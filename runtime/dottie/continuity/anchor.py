"""The identity anchor: write it, load it by chain head, verify it, supersede it by link.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I4 — append-only; a binding is ONE NEW row that supersedes the previous anchor by link.
# I8 — reserved fields stay unset and are only read.
# I2 — an unknown intelligence raises NotFound('unknown intelligence').
# Mechanical move out of runtime/dottie/continuity.py — bodies are byte-identical.
# Re-exported by dottie/continuity/__init__.py so `from dottie import continuity` is unchanged.
"""
from __future__ import annotations

import json

from .backends import _store
from .common import (
    ANCHOR_FIELDS,
    ANCHOR_QUERY,
    ANCHOR_SCHEMA,
    DEFAULT_AGENT,
    DEFAULT_IDENTITY,
    DEFAULT_PRINCIPAL,
    MAX_ANCHOR_CHARS,
    MAX_RECALL,
    RESERVED_FIELDS,
    ROW_METADATA,
    ContinuityError,
    _bounded_model_id,
    _canonical,
    _constraints,
    _identity,
    _iso,
    _record,
    _sources,
)
from ..core import INDEPENDENT_IDENTITIES, NotFound

def _payload(record, anchored_at, history=None, previous_anchor_id=None):
    """The anchor content: the identity payload, plus the append-only supersession record."""
    content = {
        "schema": ANCHOR_SCHEMA,
        "intelligence_id": str(record.get("intelligence_id")),
        "lineage_version": str(record.get("lineage_version")),
        "behavioral_constraints": _constraints(record),
        "lineage_sources": _sources(record),
        "identity_manifest_ref": record.get("identity_manifest_ref"),
        "authority_profile_ref": record.get("authority_profile_ref"),
        "anchored_at": anchored_at,
        "model_binding_history": list(history or []),
        "previous_anchor_id": previous_anchor_id,
    }
    # Schema keys must not carry None where the contract names a string, except the two refs,
    # which the identity files may legitimately omit.
    return content


def _content_bytes(anchor):
    """The canonical bytes of the stored anchor content, from the store's own string.

    `anchor_raw_content` is what the store returned, so comparing these is comparing stored
    bytes. Reconstructing from the dict would prove only that this module round-trips.
    """
    raw = anchor.get("anchor_raw_content")
    if isinstance(raw, str):
        return raw.encode("utf-8")
    payload = {k: v for k, v in anchor.items() if k not in ROW_METADATA}
    return _canonical(payload).encode("utf-8")


def _identity_payload_bytes(anchor):
    """Canonical bytes of the identity payload an anchor carries — the part that must not move.

    Excludes `anchored_at`, `model_binding_history` and `previous_anchor_id`: those are the
    supersession record, and a re-anchor or a binding change is SUPPOSED to move them. What
    must never move across a restart, a binding change or a re-anchor is the identity itself.
    """
    if not isinstance(anchor, dict):
        return b""
    payload = {key: anchor.get(key) for key in ANCHOR_FIELDS
               if key not in ("anchored_at", "schema")}
    payload["schema"] = anchor.get("schema")
    return _canonical(payload).encode("utf-8")


def _from_row(row, backend, identity):
    """Parse one store row into an anchor, or None when it is not this identity's anchor."""
    content = row.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or parsed.get("schema") != ANCHOR_SCHEMA:
        return None
    if str(parsed.get("intelligence_id")) != identity:
        return None
    parsed["anchor_row_id"] = row.get("id")
    parsed["anchor_backend"] = backend
    parsed["anchor_raw_content"] = content
    parsed["anchor_certified"] = row.get("certified")
    return parsed


def _anchor_rows(store, identity):
    """Every anchor row the store returns for the anchor query, newest last."""
    anchors = []
    seen = set()
    for row in store.recall(ANCHOR_QUERY, MAX_RECALL):
        if row.get("id") in seen:
            continue
        anchor = _from_row(row, store.backend, identity)
        if anchor is None:
            continue
        seen.add(row.get("id"))
        anchor["_row_valid_from"] = row.get("valid_from")
        anchors.append(anchor)
    anchors.sort(key=lambda a: (
        len(a.get("model_binding_history") or []),
        str(a.get("anchored_at") or ""),
        str(a.get("anchor_row_id") or ""),
    ))
    return anchors


def _head(anchors):
    """The chain's HEAD: the anchor no other anchor names as its predecessor.

    "Newest" is a property of the supersession graph, not of a timestamp or a history length.
    Ordering by `len(model_binding_history)` was wrong the moment a re-anchor happened: a fresh
    anchor with an empty history would lose to an older two-binding row, and the next bind would
    then append a duplicate ['model-A','model-B'] prefix onto that older row's history.

    A head is a row whose id appears in no recalled row's `previous_anchor_id`. If recall was
    truncated (limit) and the true head is absent, the pool of apparent heads still contains
    the newest recalled row, so the later `anchored_at` wins.
    """
    if not anchors:
        return None
    referenced = {str(a.get("previous_anchor_id"))
                  for a in anchors if a.get("previous_anchor_id")}
    heads = [a for a in anchors if str(a.get("anchor_row_id")) not in referenced]
    pool = heads or anchors
    pool = sorted(pool, key=lambda a: (str(a.get("anchored_at") or ""),
                                       str(a.get("anchor_row_id") or "")))
    return pool[-1]


def _newest(store, identity):
    anchor = _head(_anchor_rows(store, identity))
    if anchor is not None:
        anchor.pop("_row_valid_from", None)
    return anchor


def anchor_identity(base_url=None, token=None, root_dir=None, *, identity=DEFAULT_IDENTITY,
                    agent=DEFAULT_AGENT, principal=DEFAULT_PRINCIPAL, engine=None,
                    store=None, now=None):
    """Write the first identity anchor into the memory store and return it.

    The content is canonical JSON of the identity payload read from the on-disk lineage
    record. Nothing derives from a model, a session or a conversation: the anchor is written
    from bytes that already exist on disk, and every later read is a store read.

    On an EMPTY chain this is the root anchor: empty history, no predecessor. On a chain that
    already has a head, re-anchoring SUPERSEDES that head — the new row links to it and carries
    its binding history forward. Two independent roots in one store would make "the newest
    anchor" ambiguous, which is the one thing an anchor must never be.

    Repeating a re-anchor with byte-identical payload inside the same `anchored_at` second is
    absorbed by the store (mind_engine digests kind‖scope‖agent‖content): no second row.
    """
    _identity(identity)
    record = _record(identity, root_dir)
    backend = store if store is not None else _store(base_url, token, root_dir,
                                                    identity=identity, agent=agent,
                                                    principal=principal, engine=engine)
    current = _newest(backend, identity)
    if current is None:
        content = _payload(record, _iso(now), history=[], previous_anchor_id=None)
    else:
        content = _payload(record, _iso(now),
                          history=list(current.get("model_binding_history") or []),
                          previous_anchor_id=current.get("anchor_row_id"))
    text = _canonical(content)
    if len(text) > MAX_ANCHOR_CHARS:
        raise ContinuityError("anchor content is %d chars; the store cap is %d"
                              % (len(text), MAX_ANCHOR_CHARS), "too_large")
    row = backend.save(text)
    anchor = _from_row(row, backend.backend, identity)
    if anchor is None:
        raise ContinuityError("store returned a row that is not a %s anchor" % ANCHOR_SCHEMA,
                              "store_contract")
    anchor["anchor_gate_exit"] = row.get("gate_exit")
    anchor["anchor_duplicate"] = bool(row.get("duplicate"))
    return anchor


def load_anchor(base_url=None, token=None, root_dir=None, *, identity=DEFAULT_IDENTITY,
                agent=DEFAULT_AGENT, principal=DEFAULT_PRINCIPAL, engine=None, store=None):
    """Re-load the newest anchor from the store, in any process. None when none exists.

    Recall is lexical (`match` is surface overlap, never support), so the schema check — not
    the ranking — decides what is an anchor: a row counts only when its content parses to
    `schema == "dottie-identity-anchor/v1"` for this identity. Of those, the HEAD — the row no
    other anchor names as its `previous_anchor_id` — wins; that is what makes supersession
    readable without trusting a clock.
    """
    backend = store if store is not None else _store(base_url, token, root_dir,
                                                    identity=identity, agent=agent,
                                                    principal=principal, engine=engine)
    return _newest(backend, identity)


def verify(anchor, root_dir=None):
    """Mismatches between an anchor and the on-disk lineage record. Empty list == identical.

    Named, one per failed comparison, so a caller reports which field drifted rather than
    "verification failed": identity_id, lineage_version, constraints (set equality, plus a
    duplicate check), sources (set equality, plus a duplicate check), and the two refs.
    """
    if not isinstance(anchor, dict):
        return ["anchor: not a mapping"]
    mismatches = []
    if anchor.get("schema") != ANCHOR_SCHEMA:
        mismatches.append("schema: %r != %r" % (anchor.get("schema"), ANCHOR_SCHEMA))
    ident = anchor.get("intelligence_id", anchor.get("identity_id"))
    if ident not in INDEPENDENT_IDENTITIES:
        return mismatches + ["identity_id: anchor names unknown intelligence %r" % (ident,)]
    try:
        record = _record(ident, root_dir)
    except (NotFound, ValueError, OSError) as exc:
        return mismatches + ["lineage record unavailable: %s: %s"
                             % (type(exc).__name__, exc)]
    disk_version = record.get("lineage_version")
    if anchor.get("lineage_version") != disk_version:
        mismatches.append("lineage_version: %r != lineage %r"
                          % (anchor.get("lineage_version"), disk_version))
    anchor_constraints = anchor.get("behavioral_constraints")
    if not isinstance(anchor_constraints, list):
        mismatches.append("behavioral_constraints: not a list")
    else:
        disk = _constraints(record)
        stated = [str(c) for c in anchor_constraints]
        missing = sorted(set(disk) - set(stated))
        extra = sorted(set(stated) - set(disk))
        if missing or extra:
            mismatches.append("behavioral_constraints: missing=%s extra=%s"
                              % (missing, extra))
        elif len(stated) != len(set(stated)):
            mismatches.append("behavioral_constraints: duplicate entries in anchor")
    anchor_sources = anchor.get("lineage_sources")
    if not isinstance(anchor_sources, list):
        mismatches.append("lineage_sources: not a list")
    else:
        disk = _sources(record)
        stated = [str(s) for s in anchor_sources]
        missing = sorted(set(disk) - set(stated))
        extra = sorted(set(stated) - set(disk))
        if missing or extra:
            mismatches.append("lineage_sources: missing=%s extra=%s" % (missing, extra))
        elif len(stated) != len(set(stated)):
            mismatches.append("lineage_sources: duplicate entries in anchor")
    for field in ("identity_manifest_ref", "authority_profile_ref"):
        if anchor.get(field) != record.get(field):
            mismatches.append("%s: %r != lineage %r"
                              % (field, anchor.get(field), record.get(field)))
    return mismatches


def bind_model(anchor, model_id, base_url=None, token=None, root_dir=None, *,
               provider=None, identity=DEFAULT_IDENTITY, agent=DEFAULT_AGENT,
               principal=DEFAULT_PRINCIPAL, engine=None, store=None, now=None):
    """APPEND a model binding to the anchor by saving a NEW anchor row.

    The previous row is never mutated: the store is append-only, so supersession is recorded
    by linking `previous_anchor_id` to the row this binding succeeds. `model_binding_history`
    keeps its order, one entry per binding, and the identity payload is copied from the
    anchor — not re-derived and not touched — so the only thing a binding change moves is
    which compute is declared.

    Idempotent: binding the model that is already newest returns the anchor unchanged and
    writes nothing, so a retried bind cannot forge a history entry.
    """
    if not isinstance(anchor, dict):
        raise ContinuityError("bind_model needs an anchor dict", "invalid")
    model = _bounded_model_id(model_id)
    if provider is not None and (not isinstance(provider, str) or not provider.strip()
                                 or len(provider) > 120):
        raise ContinuityError("provider must be a non-empty bounded string (<=120 chars)",
                              "invalid")
    history = [dict(entry) for entry in (anchor.get("model_binding_history") or [])
               if isinstance(entry, dict)]
    if history:
        newest = history[-1]
        if newest.get("model_id") == model and newest.get("provider") == provider:
            return anchor
    stamp = _iso(now)
    history.append({
        "model_id": model,
        "provider": provider,
        "bound_at": stamp,
        "previous_model_id": history[-1].get("model_id") if history else None,
        "identity_unchanged": True,
    })
    content = {
        "schema": ANCHOR_SCHEMA,
        "intelligence_id": anchor.get("intelligence_id"),
        "lineage_version": anchor.get("lineage_version"),
        "behavioral_constraints": list(anchor.get("behavioral_constraints") or []),
        "lineage_sources": list(anchor.get("lineage_sources") or []),
        "identity_manifest_ref": anchor.get("identity_manifest_ref"),
        "authority_profile_ref": anchor.get("authority_profile_ref"),
        "anchored_at": stamp,
        "model_binding_history": history,
        "previous_anchor_id": anchor.get("anchor_row_id"),
    }
    text = _canonical(content)
    if len(text) > MAX_ANCHOR_CHARS:
        raise ContinuityError("anchor content is %d chars; the store cap is %d"
                              % (len(text), MAX_ANCHOR_CHARS), "too_large")
    backend = store if store is not None else _store(base_url, token, root_dir,
                                                    identity=anchor.get("intelligence_id")
                                                    or identity,
                                                    agent=agent, principal=principal,
                                                    engine=engine)
    # A binding appends to the chain, so it must start from the chain's HEAD. Binding from a
    # superseded anchor would fork the history into two rows that each name the same
    # predecessor, and "newest wins" would then be a coin toss. Refuse, and name the head.
    current = _newest(backend, str(anchor.get("intelligence_id") or identity))
    if (current is not None and anchor.get("anchor_row_id")
            and current.get("anchor_row_id") != anchor.get("anchor_row_id")):
        raise ContinuityError(
            "anchor %s is superseded by %s; bind from the newest anchor"
            % (anchor.get("anchor_row_id"), current.get("anchor_row_id")), "superseded")
    row = backend.save(text)
    bound = _from_row(row, backend.backend, str(anchor.get("intelligence_id") or identity))
    if bound is None:
        raise ContinuityError("store returned a row that is not a %s anchor" % ANCHOR_SCHEMA,
                              "store_contract")
    bound["anchor_gate_exit"] = row.get("gate_exit")
    bound["anchor_duplicate"] = bool(row.get("duplicate"))
    return bound


def reserved_field_state(root_dir=None, identity=DEFAULT_IDENTITY):
    """The reserved fields as they stand on disk. Read-only; this module never writes them."""
    record = _record(identity, root_dir)
    return {field: record.get(field) for field in RESERVED_FIELDS}


def reserved_fields_unset(root_dir=None, identity=DEFAULT_IDENTITY):
    """[(field, actual, expected)] for every reserved field that is NOT still unset."""
    state = reserved_field_state(root_dir, identity)
    return [(field, state.get(field), expected)
            for field, expected in RESERVED_FIELDS.items()
            if state.get(field) != expected]
