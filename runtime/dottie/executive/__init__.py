"""Dottie's executive loop: intake, decompose, dispatch, record, resume.

Package layout (a mechanical split of the former single module; behaviour unchanged):
  common.py   status vocabulary, task-id counter                        (I4, I11)
  store.py    Store: append-only rows, split-never-truncate            (I4, I12, I9)
  tiers.py    confidence floors, escalation budget, verdict confidence (I5, I6, I11)
  tasks.py    Step, Task, parse_plan                                   (I1, I4, I11)
  helpers.py  verdict_text, task_summary                              (I1, I7)
  loop.py     Executive: plan, escalate, run, resume                   (I1, I5, I10, I11)

Every name the former module exported is re-exported here, so `E.Store`, `E.Executive`,
`E.verdict_text`, `E.task_summary`, `E.PLANNER_PROMPT` call sites are unchanged.
"""
# Every import below is a deliberate re-export; `__all__` names the whole surface,
# which is also what tells a linter the names are used rather than dead.
from __future__ import annotations

from .common import (
    ACTIVE,
    AGENT,
    BLOCKED,
    DONE,
    FAILED,
    KIND,
    OPEN,
    SCOPE,
    _TASK_SEQ,
    _next_task_id,
)
from .helpers import ANSWER_KEYS, task_summary, verdict_text
from .loop import Executive
from .store import Store
from .tasks import PLANNER_PROMPT, Step, Task, parse_plan
from .tiers import (
    CONFIDENCE_FLOOR,
    DEFAULT_CONFIDENCE_FLOOR,
    ESCALATION_CEILING,
    VALID_TIERS,
    _verdict_confidence,
    confidence_floor,
)

__all__ = [
    "ACTIVE", "AGENT", "ANSWER_KEYS", "BLOCKED", "CONFIDENCE_FLOOR",
    "DEFAULT_CONFIDENCE_FLOOR", "DONE", "ESCALATION_CEILING", "Executive", "FAILED",
    "KIND", "OPEN", "PLANNER_PROMPT", "SCOPE", "Step", "Store", "Task", "VALID_TIERS",
    "_TASK_SEQ", "_next_task_id", "_verdict_confidence", "confidence_floor",
    "parse_plan", "task_summary", "verdict_text",
]
