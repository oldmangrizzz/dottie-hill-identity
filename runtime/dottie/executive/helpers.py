"""Read-outs for the operator: verdict text and the task summary.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I1 — verdict_text returns "" when there is genuinely nothing, so the loop's empty-answer
# #      guard can fire honestly instead of crediting a transport code as work done.
# I7 — task_summary reports only what ran; no invented progress.
# I11 — an escalation that could not be read is reported, not smoothed over.
# Mechanical move out of runtime/dottie/executive.py — bodies are byte-identical.
# Re-exported by dottie/executive/__init__.py so `from dottie import executive as E` is unchanged.
"""
from __future__ import annotations

import json

from .common import OPEN
from .tasks import Task

ANSWER_KEYS = ("choice", "bool", "score", "noul", "answer", "value")


def verdict_text(verdict: dict | None) -> str:
    """The readable value out of a LAYA answer, whatever `type` it declared.

    LAYA answers with a typed payload: a `choice` string, a `bool`, a `score`, or — when the
    question was not typeable — a `noul` probability. Reading only `choice` (as an earlier
    adapter did) turned a real `noul` answer into an empty string and the loop recorded a
    success with no content. This reads the value that is actually there and returns "" when
    there is genuinely nothing, so the loop's empty-answer guard can fire honestly.
    """
    if not isinstance(verdict, dict):
        return ""
    for key in ANSWER_KEYS:
        if key in verdict and verdict[key] is not None:
            value = verdict[key]
            return value if isinstance(value, str) else json.dumps(value)
    return ""



def task_summary(task: "Task") -> dict:
    """The operator's view: what was asked, what ran, what is still pending. No invented progress."""
    return {
        "task_id": task.task_id,
        "request": task.request,
        "state": task.state,
        "steps": [{"index": s.index, "tier": s.tier, "state": s.state,
                   "instruction": s.instruction,
                   "answer": ((s.outcome or {}).get("answer") or "")[:200]}
                  for s in task.steps],
        "pending": [s.instruction for s in task.steps if s.state == OPEN],
        "plan": task.plan_note,
    }
