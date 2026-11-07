"""Orientation, not calibration.

Boyd's loop is Observe -> Orient -> Decide -> Act. A decision head gives you D and A. It
gives you no O. That is why a fast, well-built decision instrument can be *confidently
wrong*: it decides over a model that cannot represent what the observation contains.

Measured on 2026-11-05, laya/english over Dugan's battlespace:

    "Smoke reported from a warehouse, occupants unaccounted for."   -> no, 0.854
    "Wildfire front advancing on a subdivision, evacuation not started." -> no, 0.685

Both are life-safety events. Both were cleared. Confidence was HIGH on the first. No
temperature fit fixes this, because the information is not in the representation -- the
head does not model "unaccounted for", so it reads the sentence as "smoke, no fire seen,
nothing to act on."

The fix is not a threshold. It is an ORIENTATION TEST, run BEFORE the head is trusted:

    Can my current model represent what this observation contains?

If the observation carries a term whose consequence the model cannot evaluate, the head's
answer is not evidence -- whatever confidence it reports. The correct output is UNRESOLVED
with the term named, and the call escalates.

This is the same discipline as the evidence tiers: `unresolved` is a state, not a failure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------------------
# Terms that mean "this observation contains something outside my model".
# Each one is a CONDITION the decision head has no representation for, not merely a
# keyword that sounds urgent. "Trapped" matters because the head models visible fire;
# "unaccounted for" matters because the head has no concept of a person it cannot see.
#
# Grouped by the faculty that is missing, so a future edit can see why each is here.
# ---------------------------------------------------------------------------
ORIENTATION_HAZARDS: dict[str, tuple[str, ...]] = {
    "persons_not_visible": (
        "unaccounted", "missing", "cannot locate", "can't locate", "no word from",
        "overdue", "not heard from", "whereabouts",
    ),
    "persons_incapacitated": (
        "unresponsive", "not breathing", "collapsed", "unconscious", "trapped",
        "semi-conscious", "seizing", "overdose", "cardiac",
    ),
    "hazard_still_growing": (
        "advancing", "spreading", "not started", "not yet evacuated", "evacuation not",
        "rising", "still burning", "growing", "approaching",
    ),
    "structure_integrity": (
        "may be underneath", "possible collapse", "structurally compromised",
        "partially collapsed", "buckling", "load-bearing",
    ),
    "unknown_contains_unknown": (
        "unknown number", "extent unknown", "unconfirmed", "possibly occupied",
        "believed to be inside", "may contain",
    ),
}

_HAZARD_TERMS = tuple(t for group in ORIENTATION_HAZARDS.values() for t in group)


@dataclass
class Orientation:
    """The result of asking whether the model can represent the observation."""
    representable: bool
    blocked_by: list[tuple[str, str]] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "representable": self.representable,
            "blocked_by": [{"term": t, "faculty_missing": f} for t, f in self.blocked_by],
            "reason": self.reason,
        }


def orient(state: str, hazards: Iterable[str] | None = None) -> Orientation:
    """Can a decision head be trusted on this observation?

    Returns Orientation(representable=False) when the observation carries a condition the
    head has no representation for. That is not a low-confidence signal; it is a statement
    that the head's answer carries no information about the thing that matters.
    """
    text = (state or "").lower()
    terms = tuple(hazards) if hazards is not None else _HAZARD_TERMS

    hits: list[tuple[str, str]] = []
    for group, group_terms in ORIENTATION_HAZARDS.items():
        for term in group_terms:
            # word-boundary match so "trapped" does not fire on "untrapped"
            if term in terms and re.search(r"(?<![a-z])" + re.escape(term), text):
                hits.append((term, group))
    if hits:
        named = ", ".join(sorted({t for t, _ in hits}))
        return Orientation(
            representable=False,
            blocked_by=hits,
            reason=("observation carries a condition the decision head does not model: "
                    "%s. A verdict from that head is not evidence about this condition."
                    % named),
        )
    return Orientation(representable=True,
                       reason="no unmodelled condition detected; the head's verdict may be used")


def gate(state: str, verdict: dict, *, escalate_to: str = "aegis-falsworth") -> dict:
    """Wrap a decision-head verdict in an orientation check.

    When orientation fails, the verdict is PRESERVED in the record (it still happened and
    the after-action needs it) but is not permitted to clear the call.
    """
    o = orient(state)
    out = {
        "state": state,
        "head_verdict": verdict.get("choice"),
        "head_confidence": verdict.get("answer_confidence"),
        "orientation": o.as_dict(),
        "disposition": None,
        "escalate_to": None,
    }
    if o.representable:
        out["disposition"] = "ACCEPT_HEAD_VERDICT"
    else:
        out["disposition"] = "UNRESOLVED_ESCALATE"
        out["escalate_to"] = escalate_to
        out["note"] = ("head said %r at %.3f confidence; not acted on. The head cannot "
                       "represent what this observation contains, so its confidence is not "
                       "evidence about the condition that matters."
                       % (verdict.get("choice"), verdict.get("answer_confidence") or 0.0))
    return out
