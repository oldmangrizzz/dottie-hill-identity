"""Status vocabulary and the monotonic task-id counter.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I4 — the vocabulary names the states an append-only row may carry.
# I11 — the ceiling constant lives beside the states that consume it.
# Mechanical move out of runtime/dottie/executive.py — bodies are byte-identical.
# Re-exported by dottie/executive/__init__.py so `from dottie import executive as E` is unchanged.
"""
from __future__ import annotations

import time

AGENT = "dottie-executive"
KIND = "operational"
SCOPE = "project"

OPEN = "OPEN"
ACTIVE = "ACTIVE"
DONE = "DONE"
FAILED = "FAILED"
BLOCKED = "BLOCKED"

# Monotonic task-id counter. A timestamp alone collides when two tasks are planned inside the
# same millisecond — which the live test proved by creating two tasks with one id, so resume
# could not tell them apart. The counter makes the id unique per process; the timestamp keeps
# it sortable and human-readable.
_TASK_SEQ = 0


def _next_task_id() -> str:
    global _TASK_SEQ
    _TASK_SEQ += 1
    return "T-%d-%03d" % (int(time.time() * 1000), _TASK_SEQ)
