"""Dottie's executive loop: intake, decompose, dispatch, record, resume.

This is the layer that makes the pieces one system. It does not invent a planner framework
and it does not own a model — it OWNS THE SEQUENCE, and every step of that sequence is a
durable row in the memory store so an interrupted task resumes from bytes rather than from a
conversation.

THE LOOP

  1. INTAKE      a request arrives as text. The ORIGINAL wording is saved verbatim — never
                 paraphrased — because the principal must not have to restate himself after
                 an interruption.
  2. DECOMPOSE   the local tier proposes steps. The proposal is saved as a row BEFORE any step
                 runs, so a crash leaves a plan on disk, not in RAM.
  3. DISPATCH    each step names the tier that should answer:
                   local     — the model that serves `dottie.system1`
                   reflex    — LAYA, when the step is a typed judgement
                   agent     — one of the five AEGIS intelligences, by id
                   reasoning — the cloud tier, LICENSED (see ESCALATION below)

                 ESCALATION. `reasoning` is not a free upgrade. Reaching for it requires a
                 stated reason and an authority lease, both non-blank, and it is charged
                 against a per-task ceiling. When the ceiling is reached the step does NOT
                 proceed: it halts as AWAITING_OPERATOR. That is a values choice, not a
                 technical one — being slow and honest is preferred to being fast and
                 over-confident about something the reflex tier could not settle.
                 Tier choice is the PLAN's, recorded with the step; it is not re-decided at
                 run time, so a resumed task takes the same path it would have taken.
  4. RECORD      every step outcome — answer, verdict, refusal, error — is appended. A step
                 that fails is recorded as failed with its reason; it is never skipped silently
                 and never retried behind the principal's back.
  5. RESUME      on start, the loop loads every task whose state is ACTIVE and continues at the
                 first step without an outcome. It does not re-plan and it does not re-ask.

WHAT THIS MODULE REFUSES TO DO

  * It never claims a step succeeded because a model returned 200: a refusal, an empty answer
    and an error are three distinct recorded outcomes.
  * It never promotes a retrieval into memory. What the loop writes is OPERATIONAL memory
    (task state, step outcomes) under its own agent stamp; evidence about the world enters
    through the evidence path, not through this one.
  * It never retries. A failed step stays failed until a human or a later plan says otherwise.

Storage is the deployed mind_engine REST service. One row per artefact of the loop, agent
stamp `dottie-executive`, so the loop's own history is separable from every other writer's.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

AGENT = "dottie-executive"
KIND = "operational"
SCOPE = "project"

OPEN = "OPEN"
ACTIVE = "ACTIVE"
DONE = "DONE"
FAILED = "FAILED"
BLOCKED = "BLOCKED"

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Monotonic task-id counter. A timestamp alone collides when two tasks are planned inside the
# same millisecond — which the live test proved by creating two tasks with one id, so resume
# could not tell them apart. The counter makes the id unique per process; the timestamp keeps
# it sortable and human-readable.
_TASK_SEQ = 0


def _next_task_id() -> str:
    global _TASK_SEQ
    _TASK_SEQ += 1
    return "T-%d-%03d" % (int(time.time() * 1000), _TASK_SEQ)


# --------------------------------------------------------------------------- store
@dataclass
class Store:
    """The deployed memory service, spoken to over its REST wire."""
    base_url: str
    token: str
    principal: str = "grizz"
    agent: str = AGENT
    timeout: float = 30.0

    def _call(self, method: str, path: str, payload: dict | None = None) -> tuple[int | None, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + self.token,
                   "X-Cpki-User-Id": self.principal,
                   "X-Dottie-Agent": self.agent}
        request = urllib.request.Request(self.base_url.rstrip("/") + path, data=body,
                                        method=method, headers=headers)
        try:
            with _opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                return exc.code, {"error": raw[:200]}
        except (urllib.error.URLError, OSError) as exc:
            return None, {"error": "%s: %s" % (type(exc).__name__, exc)}

    MAX_CHARS = 8000  # memory store cap is 8192; stay under it and split instead of failing

    def save(self, content: str, kind: str = KIND, scope: str = SCOPE) -> dict:
        # The model can emit more than the store accepts. A hard 422 lost the whole step,
        # so the output is split on line boundaries and saved as ordered parts.
        text = content.rstrip()
        if len(text) <= self.MAX_CHARS:
            return self._save_one(text, kind, scope)
        parts, buf = [], ""
        for line in text.splitlines(True):
            if len(buf) + len(line) > self.MAX_CHARS and buf:
                parts.append(buf); buf = ""
            while len(line) > self.MAX_CHARS:
                parts.append(line[:self.MAX_CHARS]); line = line[self.MAX_CHARS:]
            buf += line
        if buf: parts.append(buf)
        first = None
        for i, part in enumerate(parts, 1):
            header = "part %d/%d\n" % (i, len(parts))
            payload = self._save_one(header + part, kind, scope)
            if first is None: first = payload
        payload = dict(first or {})
        payload["parts"] = len(parts)
        return payload

    def _save_one(self, content, kind, scope):
        status, payload = self._call("POST", "/api/memories",
                                     {"content": content, "kind": kind, "scope": scope})
        if status not in (200, 201):
            raise RuntimeError("save failed (%s): %s" % (status, payload.get("error")))
        return payload

    def recall(self, query: str, limit: int = 20) -> list:
        status, payload = self._call("POST", "/api/memories/recall",
                                     {"query": query, "limit": limit,
                                      "includeInvalidated": False})
        if status != 200:
            raise RuntimeError("recall failed (%s): %s" % (status, payload.get("error")))
        return payload.get("memories") or []


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


# --------------------------------------------------------------------------- the loop
class Executive:
    """The sequence. `think` answers prose; `judge` gives a typed verdict; `agent` dispatches."""

    def __init__(self, store: Store, think, judge=None, agent=None, recorder=None,
                 lease_for=None, slots=None):
        self.store = store
        self._think = think
        self._judge = judge
        self._agent = agent
        self._record = recorder
        # `lease_for(action_kind, instruction) -> lease_ref | None` supplies the
        # authority lease under which an escalation is permitted. Without it a
        # `reasoning` step cannot run at all: it halts AWAITING_OPERATOR rather than
        # reaching for the cloud tier unlicensed.
        self._lease_for = lease_for
        self._slots = slots or {}

    # -- persistence -------------------------------------------------------
    def _persist(self, task: "Task") -> str:
        task.revision += 1                      # every write is a new, higher revision
        row = self.store.save(task.to_record())
        task.row_id = row.get("id", "")
        return task.row_id

    def load_open(self) -> list:
        """One entry per task_id, newest revision wins, and only if that revision is open.

        The store is append-only: every `_persist` writes a NEW row for the same task, so a
        recall returns every revision of every task. Two things must therefore be true of this
        method or resume is wrong in a way that is invisible until it matters:

          * ONE task per `task_id` — the newest revision, because that is the current state;
          * only tasks whose NEWEST revision is still open — a task whose last row says DONE
            must not be re-run because an earlier revision said ACTIVE.

        The two passes below are not redundant: `recall` returns newest-first, so the first row
        seen for an id is its current state. Collecting ids in a dict in one pass is enough
        only if the iteration is guaranteed newest-first; doing it explicitly makes the
        ordering assumption visible and testable instead of implicit.
        """
        # Ordering by RECALL ORDER is not safe: the store sorts by valid_from, and rows written
        # on the same day share it, so "first row seen" is not "newest revision". The record
        # carries its own `revision`; the highest revision per task_id IS the current state.
        # This is why a retired task kept coming back: the retirement row was not the one the
        # ordering happened to surface.
        rows = self.store.recall("dottie-task/v1", limit=100)
        newest: dict[str, Task] = {}
        order: list[str] = []
        for row in rows:
            task = Task.from_record(row.get("content") or "")
            if task is None or not task.task_id:
                continue
            current = newest.get(task.task_id)
            if current is None:
                order.append(task.task_id)
            if current is None or task.revision >= current.revision:
                task.row_id = row.get("id", "")
                newest[task.task_id] = task
        open_tasks = []
        for tid in order:
            task = newest[tid]
            if task.state not in (ACTIVE, OPEN):
                continue
            # A task with no open step is NOT work waiting to happen: every step already has an
            # outcome, only the terminal write was lost. Returning it made the runner re-plan
            # nothing and re-persist forever; a restart loop with no progress. Treat it as done.
            pending = [s for s in task.steps if s.state in (ACTIVE, OPEN)]
            if not pending:
                continue
            open_tasks.append(task)
        return open_tasks

    # -- the three verbs ---------------------------------------------------
    def plan_with_model(self, request: str, planner) -> "Task":
        """Ask the model to decompose the request, then persist whatever came back.

        `planner(request) -> text` is the local tier's answer to `PLANNER_PROMPT`. A plan that
        does not parse becomes ONE step containing the request itself — the honest fallback,
        recorded as such, so an interrupted task still resumes and the principal is never asked
        to restate anything. The decomposition outcome is written into the first step's tier
        label (`local` vs `local+fallback`) so a reader can tell which happened.
        """
        text = ""
        error = None
        try:
            result = planner(request)
            text = result.get("answer") if isinstance(result, dict) else str(result)
            if isinstance(result, dict) and not result.get("ok", True):
                error = result.get("error") or "planner failed"
        except Exception as exc:  # a planner crash is a fallback, not a task failure
            error = "%s: %s" % (type(exc).__name__, exc)
        steps = parse_plan(text or "")
        if not steps and error is None:
            # ONE retry with a larger budget. Measured failure mode: under concurrent load the
            # engine serializes and truncates the JSON array mid-element, so parse_plan refuses
            # a plan that would have been valid with more room. The retry is bounded (one) and
            # recorded, so a model that simply cannot plan is not asked twice forever.
            try:
                retry = planner(request + "\n\n(Return ONLY the JSON array.)")
                text = retry.get("answer") if isinstance(retry, dict) else str(retry)
                steps = parse_plan(text or "")
                if steps:
                    error = None
            except Exception as exc:
                error = "%s: %s" % (type(exc).__name__, exc)
        if not steps:
            steps = [(request, "local")]
            note = error or "planner returned no usable JSON array (retried once)"
            task = self.plan(request, steps)
            task.plan_note = "fallback: " + note
            self._persist(task)
            return task
        task = self.plan(request, steps)
        task.plan_note = "model-authored"
        self._persist(task)
        return task

    def plan(self, request: str, steps: list) -> "Task":
        """Decompose and persist the plan BEFORE running anything.

        `steps` is a list of (instruction, tier). Decomposition is the caller's (the local
        model's) job; this records the decision so an interrupted run resumes on the same path.
        """
        task = Task(task_id=_next_task_id(), request=request, created=time.time())
        task.steps = [Step(i, text, tier) for i, (text, tier) in enumerate(steps)]
        task.state = ACTIVE
        self._persist(task)
        return task

    # -- licensed escalation -------------------------------------------------
    def escalate(self, instruction: str, *, action_kind: str, reason: str,
                 lease_ref: str, state: dict | None = None, questions: dict | None = None,
                 tier: str = "reasoning") -> dict:
        """Reach for the reasoning tier ON THE RECORD. Refuses if it is not licensed.

        Four things are required, and each one is required for a reason:

          reason     why this step needs the deeper tier. An input, not a log line -- a blank
                     reason is a refusal, because "it seemed hard" is not an argument.
          lease_ref  the GrizOS authority lease under which escalation is permitted. Escalation
                     is a consented act with an expiry and a rollback, not a function argument.
          floor      the per-action-kind confidence bar. An ALERT must clear 0.95; a REPORT 0.70.
          ceiling    a per-task budget. When it is spent, this returns AWAITING_OPERATOR instead
                     of proceeding. Being slow and honest beats being fast and wrong.

        Both answers are retained when both exist, so a later reader can see whether the expensive
        tier DISAGREED -- which is the only measurement that ever tells you if it earns its keep.
        """
        if not str(reason or "").strip():
            raise ValueError("escalation requires a non-blank reason")
        if not str(lease_ref or "").strip():
            raise ValueError("escalation requires a non-blank lease_ref")

        kind = str(action_kind or "").upper()
        floor = confidence_floor(kind)
        out: dict = {
            "escalation": True,
            "action_kind": kind,
            "confidence_floor": floor,
            "reason": str(reason).strip(),
            "lease_ref": str(lease_ref).strip(),
            "requested_tier": tier,
        }

        # 1. What did the cheap tier say, and how sure was it?
        verdict = None
        if self._judge is not None:
            try:
                verdict = self._judge(instruction)
            except Exception as exc:
                out["reflex_error"] = "%s: %s" % (type(exc).__name__, exc)
        out["reflex_verdict"] = verdict
        conf = _verdict_confidence(verdict) if verdict is not None else None
        out["reflex_confidence"] = conf
        if conf is None:
            # Do not invent a number. Record that we could not tell, and treat it as not cleared.
            out["escalation_trigger"] = "unparseable_confidence" if verdict is not None else "no_verdict"
        elif conf < floor:
            out["escalation_trigger"] = "uncertainty"
        else:
            out["escalation_trigger"] = "cleared"   # the cheap tier was good enough
            out["cleared_by_reflex"] = True
            return out

        # 2. Is there budget left? If not, STOP AND ASK. Never spend silently.
        used = getattr(state or {}, "escalations_used", None)
        if isinstance(used, int) and used >= ESCALATION_CEILING:
            return {
                "ok": False,
                "state": "AWAITING_OPERATOR",
                "ceiling": ESCALATION_CEILING,
                "error": ("escalation ceiling reached: %d escalations used for this task; "
                          "operator decision required" % used),
                "reflex_verdict": verdict,
                "reflex_confidence": conf,
                "confidence_floor": floor,
                "reason": str(reason).strip(),
                "lease_ref": str(lease_ref).strip(),
            }

        # 3. Ask the reasoning tier. If it cannot run, say so and keep the reflex answer.
        try:
            result = self._think(instruction, state=state if isinstance(state, dict) else {},
                                 questions=questions, use_cloud=True)
        except TypeError as exc:
            # Only blame the signature when the signature is the problem. A blanket
            # except TypeError previously reported "does not accept use_cloud" for a TypeError
            # raised INSIDE the caller's think(), which sent me debugging the wrong file.
            msg = str(exc)
            if "use_cloud" in msg or "unexpected keyword" in msg:
                try:
                    result = dict(self._think(instruction) or {})
                    result["evidence"] = dict(result.get("evidence") or {})
                    result["evidence"]["cloud_skipped"] = (
                        "think() does not accept use_cloud; only the local tier ran")
                except Exception as inner:
                    result = {"ok": False, "tier_used": "local",
                              "evidence": {"cloud_skipped":
                                           "think() rejected use_cloud and the fallback failed: "
                                           "%s: %s" % (type(inner).__name__, inner)}}
            else:
                result = {"ok": False, "tier_used": "local",
                          "evidence": {"cloud_skipped": "think() raised inside escalate: %s: %s"
                                                        % (type(exc).__name__, msg)}}

        evidence = (result or {}).get("evidence") or {}
        out["tier_used"] = (result or {}).get("tier_used")
        # The reasoning answer is ONLY the reasoning answer. When the cloud tier did not answer,
        # think()'s `answer` field holds the LOCAL model's prose -- crediting that to tier 3 is
        # exactly the false success this system exists to prevent. I made that mistake once; the
        # field is now sourced from evidence["reasoning_answer"], which is None on failure.
        out["reasoning_answer"] = evidence.get("reasoning_answer")
        if evidence.get("cloud_skipped"):
            out["cloud_skipped"] = evidence["cloud_skipped"]
            out["ok"] = False
            out["error"] = "reasoning tier unavailable: %s" % evidence["cloud_skipped"]
        else:
            out["ok"] = bool((result or {}).get("tier_used") == "reasoning"
                             and (evidence.get("reasoning_answer") or "").strip())

        # 4. Did the two tiers agree? Disagreement is the datum, not a problem to smooth over.
        ref_pick = None
        if isinstance(verdict, dict):
            for k in ("choice", "answer", "value"):
                if verdict.get(k) is not None:
                    ref_pick = str(verdict[k]).strip().lower()
                    break
        rs = out.get("reasoning_answer")
        out["agreed"] = (ref_pick == str(rs).strip().lower()) if (ref_pick and rs) else None
        return out

    def run(self, task: "Task") -> "Task":
        """Execute from the first un-outcome'd step. Never re-plans, never re-asks."""
        while True:
            step = task.next_open()
            if step is None:
                break
            step.state = ACTIVE
            try:
                if step.tier.startswith("agent:"):
                    agent_id = step.tier.split(":", 1)[1]
                    outcome = self._agent(agent_id, step.instruction) if self._agent else \
                        {"ok": False, "error": "no agent dispatcher configured"}
                elif step.tier == "reflex" and self._judge is not None:
                    outcome = self._judge(step.instruction)
                elif step.tier == "reasoning":
                    # LICENSED escalation only. The lease is asked for, not assumed;
                    # without one the step halts for a human instead of spending the
                    # cloud tier on its own authority.
                    lease = None
                    if self._lease_for is not None:
                        try:
                            lease = self._lease_for(step.action_kind or "REASON", step.instruction)
                        except Exception as exc:
                            lease = None
                            outcome = {"ok": False, "state": "AWAITING_OPERATOR",
                                       "error": "lease lookup failed: %s: %s" % (type(exc).__name__, exc)}
                    if lease:
                        outcome = self.escalate(step.instruction, action_kind=step.action_kind or "REASON",
                                                reason=(step.instruction or "reasoning step")[:400],
                                                lease_ref=lease, state=task)
                        task.escalations_used = int(getattr(task, "escalations_used", 0) or 0) + 1
                    elif lease is None and not (isinstance(locals().get('outcome'), dict)):
                        outcome = {"ok": False, "state": "AWAITING_OPERATOR",
                                   "error": "reasoning tier requires an authority lease; none configured"}
                else:
                    outcome = self._think(step.instruction)
            except Exception as exc:  # a step failure is data, not a crash
                outcome = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
            step.outcome = outcome if isinstance(outcome, dict) else {"ok": True,
                                                                      "answer": str(outcome)}
            # A 200 is not success: an empty answer is recorded as an empty answer.
            ok = step.outcome.get("ok")
            if ok:
                # A 200 is not success. An answer that is missing, None, or blank is a refusal
                # in substance, and the loop records it as one instead of counting a transport
                # code as work done. (The judge path can legitimately return no text — but then
                # it must carry a verdict or a reason, not an empty answer.)
                answer = step.outcome.get("answer")
                has_text = bool(str(answer).strip()) if answer is not None else False
                if not has_text:
                    ok = False
                    step.outcome = dict(step.outcome)
                    step.outcome["error"] = ("empty answer: the step returned no content and no "
                                             "verdict (a 200 is not success)")
            if ok is None:
                ok = bool((step.outcome.get("answer") or "").strip())
            step.state = DONE if ok else FAILED
            if not ok:
                task.state = BLOCKED
            self._persist(task)
            if self._record:
                self._record(task, step)
            if step.state == FAILED:
                break          # stop at the first failure; do not silently continue
        if task.state == ACTIVE and task.next_open() is None:
            task.state = DONE
            self._persist(task)
        return task

    def resume_all(self) -> list:
        """Start-up path: continue every open task from bytes."""
        return [self.run(task) for task in self.load_open()]


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
