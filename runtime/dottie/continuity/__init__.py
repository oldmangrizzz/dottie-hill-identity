"""Identity continuity: the anchor that lets an identity outlive the model computing it.

Package layout (a mechanical split of the former single module; behaviour unchanged):
  common.py    constants, ContinuityError, small helpers           (I2, I4, I8)
  backends.py  RestStore / EngineStore and the store selector      (I9, I12)
  anchor.py    the anchor: payload, write, load, verify, bind       (I4, I8)
  selftest.py  the restart / binding-change / reserved-field proof  (I4, I8, I12)
  cli.py       `python3 -m dottie.continuity ...`                   (I4, I9)

Every name the former module exported is re-exported here, so `continuity.anchor_identity`,
`continuity._store`, `continuity.ContinuityError`, `continuity.DEFAULT_URL` and
`python3 -m dottie.continuity` keep resolving exactly as before.
"""
# Every import below is a deliberate re-export; `__all__` names the whole surface,
# which is also what tells a linter the names are used rather than dead.
from __future__ import annotations

from .anchor import (
    _anchor_rows,
    _content_bytes,
    _from_row,
    _head,
    _identity_payload_bytes,
    _newest,
    _payload,
    anchor_identity,
    bind_model,
    load_anchor,
    reserved_field_state,
    reserved_fields_unset,
    verify,
)
from .backends import EngineStore, RestStore, _store
from .cli import _resolve_store, _token_from, main
from .common import (
    ANCHOR_FIELDS,
    ANCHOR_KIND,
    ANCHOR_QUERY,
    ANCHOR_SCHEMA,
    ANCHOR_SCOPE,
    DEFAULT_AGENT,
    DEFAULT_IDENTITY,
    DEFAULT_PRINCIPAL,
    DEFAULT_URL,
    MAX_ANCHOR_CHARS,
    MAX_RECALL,
    RESERVED_FIELDS,
    ROW_METADATA,
    ContinuityError,
    _bounded_model_id,
    _canonical,
    _constraints,
    _find_engine,
    _identity,
    _iso,
    _record,
    _sha256,
    _sources,
)
from ..core import INDEPENDENT_IDENTITIES, NotFound
from ..lineage import compute_instructions, lineage
from .selftest import _child_anchor, _line, _run_tag, _store_flags, selftest

__all__ = [
    "ANCHOR_FIELDS", "ANCHOR_KIND", "ANCHOR_QUERY", "ANCHOR_SCHEMA", "ANCHOR_SCOPE",
    "ContinuityError", "DEFAULT_AGENT", "DEFAULT_IDENTITY", "DEFAULT_PRINCIPAL",
    "DEFAULT_URL", "EngineStore", "INDEPENDENT_IDENTITIES", "MAX_ANCHOR_CHARS",
    "MAX_RECALL", "NotFound", "RESERVED_FIELDS", "ROW_METADATA", "RestStore",
    "_anchor_rows", "_bounded_model_id", "_canonical", "_child_anchor", "_constraints",
    "_content_bytes", "_find_engine", "_from_row", "_head", "_identity",
    "_identity_payload_bytes", "_iso", "_line", "_newest", "_payload", "_record",
    "_resolve_store", "_run_tag", "_sha256", "_sources", "_store", "_store_flags",
    "_token_from", "anchor_identity", "bind_model", "compute_instructions", "lineage",
    "load_anchor", "main", "reserved_field_state", "reserved_fields_unset", "selftest",
    "verify",
]
