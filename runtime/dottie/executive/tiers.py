"""Tier discipline: per-action-kind confidence floors, the escalation budget, verdict confidence.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I11 — the budget and the floors are unchanged constants; nothing here may raise a floor or
# #      spend the reasoning tier on the executive's own authority.
# I6 — a verdict's confidence is READ; this module never decides orientation.
# I5 — an unreadable verdict returns None ("could not tell"), never an invented number.
# Mechanical move out of runtime/dottie/executive.py — bodies are byte-identical.
# Re-exported by dottie/executive/__init__.py so `from dottie import executive as E` is unchanged.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- tier discipline

# Per-action-kind confidence floors.
#
# A REPORT may proceed on moderate confidence; an ALERT must not. This is the laboratory motto
# ("due regard") in executable form: the higher the stake of acting, the higher the bar for
# acting, and the bar belongs BEFORE the action rather than in a review afterwards.
#
# The vocabulary mirrors AUTHORIZED_READ_KINDS in core.py. It is mirrored rather than imported so
# the executive does not take a dependency on the gateway's module graph.
CONFIDENCE_FLOOR = {
    "OBSERVE": 0.60,
    "RETRIEVE": 0.60,
    "COMPARE": 0.70,
    "REASON": 0.75,
    "INVESTIGATE": 0.75,
    "COLLECT_EVIDENCE": 0.80,
    "REPORT": 0.70,
    "ALERT": 0.95,
}
DEFAULT_CONFIDENCE_FLOOR = 0.75

# How many escalations one task may spend before it must stop and ask a human.
ESCALATION_CEILING = 2

VALID_TIERS = ("local", "reflex", "reasoning")


def confidence_floor(action_kind: str) -> float:
    """The bar a verdict must clear for this kind of action. Unknown kinds get the default."""
    return CONFIDENCE_FLOOR.get(str(action_kind or "").upper(), DEFAULT_CONFIDENCE_FLOOR)


def _verdict_confidence(verdict):
    """Extract a calibrated confidence from any LAYA verdict shape, or None.

    LAYA returns typed answers:
      noul   -> {"type": "noul",   "noul": 0.874}
      choice -> {"type": "choice", "choice": "affirm", "probabilities": {...}, "confidence": x}
      score  -> {"type": "score",  "score": 3.596, "probabilities": {...}}
    Three shapes, one question: how sure is it? Never guess -- an unreadable verdict returns None
    and the caller records that it could not tell, rather than inventing a number.
    """
    if not isinstance(verdict, dict):
        return None
    ans = verdict.get("answers")
    if isinstance(ans, dict) and len(ans) == 1:
        verdict = list(ans.values())[0]
    if not isinstance(verdict, dict):
        return None
    for key in ("confidence", "noul"):
        v = verdict.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    probs = verdict.get("probabilities")
    if isinstance(probs, dict) and probs:
        vals = [float(x) for x in probs.values() if isinstance(x, (int, float))]
        if vals:
            return max(vals)
    return None
