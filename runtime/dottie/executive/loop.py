"""The executive loop: intake, decompose, dispatch, record, resume.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I5 — escalation is licensed: a reasoning step without a lease halts AWAITING_OPERATOR.
# I11 — ESCALATION_CEILING is enforced per task; when spent, the loop stops and asks.
# I1 — a step that returns no content is recorded as a FAILURE, not a success.
# I10 — an `agent:` step is dispatched by text to that agent; no interior access.
# Mechanical move out of runtime/dottie/executive.py — bodies are byte-identical.
# Re-exported by dottie/executive/__init__.py so `from dottie import executive as E` is unchanged.
"""
from __future__ import annotations

import time

from .common import ACTIVE, BLOCKED, DONE, FAILED, OPEN, _next_task_id
from .tasks import Step, Task, parse_plan
from .store import Store
from .tiers import ESCALATION_CEILING, confidence_floor, _verdict_confidence

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
