"""Type coercion for config and environment values, one named error per bad value.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I11 — a bad budget or timeout is reported, never silently defaulted.
# I9 — nothing handled here is a secret; strings are stripped, never echoed.
# Mechanical move out of runtime/dottie/models.py — bodies are byte-identical.
# Re-exported by dottie/models/__init__.py so `from dottie import models as M` is unchanged.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# coercion
# --------------------------------------------------------------------------- #
def _coerce(name, raw):
    """(value, error). Strings are stripped; empty is a value (validated later as missing)."""
    if name == "timeout_seconds":
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None, "expected a number of seconds"
        return (value, None) if value > 0 else (None, "must be greater than zero")
    if name == "max_tokens":
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None, "expected a positive integer"
        return (value, None) if value > 0 else (None, "must be greater than zero")
    if name == "enabled":
        if isinstance(raw, bool):
            return raw, None
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True, None
        if text in ("0", "false", "no", "off"):
            return False, None
        return None, "expected true/false"
    return str(raw).strip(), None
