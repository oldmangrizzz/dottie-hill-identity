"""Continuity constants, the error type, and the small helpers every part shares.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I2 — `_identity` gates every call to the six-identity registry.
# I4 — canonical bytes are what the hash chain is made of.
# I8 — the reserved fields are READ here, never written.
# I9 — the engine path is env-resolved (`DOTTIE_MIND_ENGINE`).
# Mechanical move out of runtime/dottie/continuity.py — bodies are byte-identical.
# Re-exported by dottie/continuity/__init__.py so `from dottie import continuity` is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from ..core import INDEPENDENT_IDENTITIES, NotFound
from ..lineage import lineage

ANCHOR_SCHEMA = "dottie-identity-anchor/v1"
DEFAULT_IDENTITY = "dottie-hill"
DEFAULT_AGENT = "dottie-hill"
DEFAULT_PRINCIPAL = "grizz"
DEFAULT_URL = "http://127.0.0.1:8471"
ANCHOR_KIND = "operational"
ANCHOR_SCOPE = "project"
# One query, used for both save-adjacent recall and load. Exact words the anchor's own
# content carries, so the lexical recall always has candidates to rank.
ANCHOR_QUERY = "dottie identity anchor schema continuity"
MAX_RECALL = 20
# mind.MEMORY_MAX_CHARS — the engine's cap, restated here so an over-long anchor fails with a
# name instead of a 422 from the server.
MAX_ANCHOR_CHARS = 8192
# The reserved keys are READ here only to assert they are still unset. Nothing in this module
# writes them, and the anchor does not carry them into the store.
RESERVED_FIELDS = {
    "memory_namespace_ref": "UNSET_DEPLOYED_NAMESPACE",
    "relationship_state_ref": "UNSET_DEPLOYED_RELATIONSHIP_STATE",
    "lineage_version": "UNSET_SOURCE_DERIVED",
}
# The identity payload of an anchor: exactly the fields the contract names. The binding history
# and the supersession link ride along because append-only supersession needs them.
ANCHOR_FIELDS = (
    "schema",
    "intelligence_id",
    "lineage_version",
    "behavioral_constraints",
    "lineage_sources",
    "identity_manifest_ref",
    "authority_profile_ref",
    "anchored_at",
)
# Row metadata attached to a loaded anchor. Stripped before any content comparison, so the
# byte-identity proof is about the stored bytes and not about this module's bookkeeping.
ROW_METADATA = ("anchor_row_id", "anchor_backend", "anchor_raw_content", "anchor_certified")


class ContinuityError(RuntimeError):
    """A store or contract failure. Carries a code so a caller can report it, not guess."""

    def __init__(self, message, code="unavailable"):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _canonical(value):
    """The same canonical JSON the gateway uses (`dottie.core.canonical`), restated."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _identity(ident):
    if ident not in INDEPENDENT_IDENTITIES:
        raise NotFound("unknown intelligence")
    return ident


def _record(ident, root_dir=None):
    """The on-disk lineage record. Delegates to `dottie.lineage.lineage` when possible."""
    _identity(ident)
    if root_dir is None:
        return lineage(ident)
    doc = Path(root_dir).expanduser() / "lineage_data" / (ident + ".json")
    if not doc.is_file():
        raise ValueError("GMRI identity lineage template absent: " + ident)
    data = json.loads(doc.read_text())
    if data.get("intelligence_id") != ident:
        raise ValueError("lineage identity mismatch")
    return data


def _constraints(record):
    """Constraints, tolerating the two spellings the identity files use."""
    for key in ("behavioral_constraints", "constraints"):
        value = record.get(key)
        if isinstance(value, list) and value:
            return [str(v) for v in value]
    return []


def _sources(record):
    value = record.get("lineage_sources")
    return [str(v) for v in value] if isinstance(value, list) else []


def _bounded_model_id(model_id):
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 150 or "\x00" in model_id:
        raise ContinuityError("model_id must be a non-empty bounded string (<=150 chars)", "invalid")
    return model_id.strip()


def _find_engine():
    """Locate `mind.py`. Explicit env wins; then the deployed copy; then a local checkout."""
    explicit = os.environ.get("DOTTIE_MIND_ENGINE", "").strip()
    candidates = [explicit] if explicit else []
    candidates += [
        "/opt/dottie-memory/mind.py",
        str(Path.home() / "Desktop" / "mind_engine" / "mind.py"),
        str(Path.cwd() / "mind_engine" / "mind.py"),
    ]
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None
