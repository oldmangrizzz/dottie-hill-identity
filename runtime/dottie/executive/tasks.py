"""The task model and the plan parser.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I11 — `escalations_used` is charged against ESCALATION_CEILING and never silently reset.
# I4 — to_record/from_record round-trip through the agent's append-only rows.
# I1 — parse_plan returns [] on an unusable plan; it never invents a step from prose.
# Mechanical move out of runtime/dottie/executive.py — bodies are byte-identical.
# Re-exported by dottie/executive/__init__.py so `from dottie import executive as E` is unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .common import OPEN
from .tiers import VALID_TIERS

# --------------------------------------------------------------------------- task model
@dataclass
class Step:
    index: int
    instruction: str
    tier: str                      # local | reflex | agent:<id> | reasoning
    state: str = OPEN
    outcome: dict | None = None
    action_kind: str = ""          # REPORT / ALERT / OBSERVE ... selects the confidence floor


@dataclass
class Task:
    task_id: str
    request: str                   # verbatim, never paraphrased
    steps: list = field(default_factory=list)
    state: str = OPEN
    created: float = 0.0
    row_id: str = ""
    revision: int = 0
    plan_note: str = ""
    escalations_used: int = 0      # charged against ESCALATION_CEILING; never silently reset

    def to_record(self) -> str:
        return json.dumps({
            "schema": "dottie-task/v1",
            "task_id": self.task_id,
            "request": self.request,
            "state": self.state,
            "created": self.created,
            "revision": self.revision,
            "plan_note": self.plan_note,
            "steps": [{"index": s.index, "instruction": s.instruction, "tier": s.tier,
                       "state": s.state, "outcome": s.outcome} for s in self.steps],
        }, ensure_ascii=False)

    @classmethod
    def from_record(cls, text: str):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or data.get("schema") != "dottie-task/v1":
            return None
        task = cls(task_id=data.get("task_id", ""), request=data.get("request", ""),
                   state=data.get("state", OPEN), created=data.get("created", 0.0),
                   revision=int(data.get("revision") or 0),
                   plan_note=str(data.get("plan_note") or ""))
        task.steps = [Step(s.get("index", 0), s.get("instruction", ""), s.get("tier", "local"),
                           s.get("state", OPEN), s.get("outcome"))
                      for s in data.get("steps", [])]
        return task

    def next_open(self):
        for step in self.steps:
            if step.state == OPEN:
                return step
        return None

PLANNER_PROMPT = """You are Dottie's planning function. Decompose the request into 2 to 5 steps.

Rules:
- Each step is one concrete instruction a language model or a judgement instrument can execute.
- Choose the tier per step:
    "local"     prose or synthesis on the local model
    "reflex"    a yes/no or scored judgement about a specific claim (fast, typed)
    "reasoning" ONLY when the step genuinely needs deep reasoning and a typed
                judgement cannot settle it. This tier is expensive and is
                charged against a per-task ceiling, so prefer the cheaper two.
- Do not invent facts about the request. If the request is one step, return one step.
- Never add steps that would require information you were not given.

Reply with ONLY a JSON array, exactly this shape:
[{"instruction": "...", "tier": "local"}, {"instruction": "...", "tier": "reflex"}]
"""


def parse_plan(text: str, max_steps: int = 5) -> list:
    """The model's plan as [(instruction, tier)], or [] if it is not usable.

    A planner that returns prose, a truncated array, or a step with no instruction is a failed
    plan, not something to guess around: the caller falls back to a single-step plan and records
    that the decomposition failed. Silently "repairing" a model's JSON would hide the failure
    the whole loop exists to make visible.
    """
    if not isinstance(text, str):
        return []
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    steps = []
    for item in data[:max_steps]:
        if not isinstance(item, dict):
            continue
        instruction = str(item.get("instruction") or "").strip()
        if not instruction:
            continue
        tier = str(item.get("tier") or "local").strip().lower()
        if tier not in VALID_TIERS and not tier.startswith("agent:"):
            tier = "local"
        steps.append((instruction, tier))
    return steps
