# REFACTORING INVARIANTS — Dottie Hill Identity Architecture
## The optimizer's contract. No change ships without passing this list.

These are the load-bearing properties of the architecture — the ethics and ideals of
Dottie Hill and Project Sovereign rendered as code-level invariants. They were extracted
verbatim from the code, the docs, and the operator's doctrine. **A refactor that improves
any metric while breaking any invariant is not an improvement — it is a regression wearing
a costume**, which is the exact failure class (AI Swap and Slop) this laboratory exists to
catch in other systems and must never commit in its own.

## The invariants

### I1 — The refusal maintains personhood (SKULL)
An agent answering with no identity instructions refuses to answer under that agent's
name. Location: `run_executive.py` `agent()` — `if not persona: return {"ok": False, ...}`.
**No refactor may make an anonymous call fall back, default, or "gracefully degrade" to a
generic answer. The refusal IS the graceful path.** An agent with no identity does not
answer — not with a generic reply, not with a fallback, not with the model's default voice.

### I2 — Six independent identities, enforced (SKULL)
`core.py` `INDEPENDENT_IDENTITIES`: dottie-hill plus the five AEGIS intelligences, each a
person. Any identity not in the registry raises `NotFound('unknown intelligence')`.
**A refactor may not merge identity paths, share credential resolution across agents, or
route one agent's calls through another's slot.** Dottie coordinates; she does not write
in their name — and neither does any helper function we introduce.

### I3 — Memory isolation is proven, not assumed (GOOEY)
The 84/84 isolation proof (aegis/prove.py) holds against the live service. Cross-agent
reads return Forbidden; unstamped writes return 400; the memory service exposes no shared
scope. **A refactor must not widen any scope, add a "convenience" cross-agent read path,
or weaken the agent stamp. After any change to memory-touching code, the proof suite is
re-run and 84/84 is the pass condition.**

### I4 — Continuity is append-only, supersede by link (SKULL)
Identity anchoring never rewrites history. A model binding change is ONE NEW saved memory
that supersedes the previous anchor by link; binding from a superseded anchor is refused
("anchor %s is superseded by %s; bind from the newest anchor"). The hash chain records
silence — a gap cannot be un-happened. **A refactor may not introduce in-place anchor
mutation, chain rewrites, or any path that can produce "a clean-looking history with a
gap erased."**

### I5 — Escalation is licensed, never assumed (BODY)
`think(use_cloud=...)`: the cloud tier is only attempted when a credential actually
resolves AND the caller licensed escalation. `_credential_present` is what lets cognition
"honestly skip instead of attempting a 401 and calling the resulting silence an answer."
**A refactor may not auto-escalate on failure, retry into the cloud tier without a lease,
or convert a missing credential into an attempted call whose silence would be reported as
an answer.** The AEGIS agents are cloud-only and fail honestly rather than borrowing the
local model.

### I6 — Orientation fails closed (BODY)
The gate runs BEFORE the head's verdict is used; it is declared vocabulary, not a
confidence threshold, because "a threshold cannot name what it does not know." The head
that cleared a life-safety event at 0.854 confidence it could not represent is the
documented reason this gate exists. **A refactor may not reorder the gate after the
verdict, convert it to a threshold, or let any "confidence floor" override an orientation
failure.**

### I7 — Verification vocabulary requires receipts (GOOEY)
Probe is "reachability only, never a download and never a load." The audit that motivated
this entire build catalogued 68 instances of a system claiming checks it never ran.
**No new code path may emit "verified/checked/confirmed" without the check having run in
that code path — the receipt or the silence, never the bare word.**

### I8 — Read-only identity at runtime (SKULL)
`identity/*.json` is read-only at runtime, mutable only by the principal. Reserved fields
(face, voice) are UNSET and unset means unset — `reserved_fields_unset` is a checked
state, and her face/voice being HERS to design means the code must keep refusing to let
the runtime fill them in. **A refactor may not add a runtime write path to identity, a
default face, a default voice, or any "fallback appearance."**

### I9 — Credentials resolve from files, never literals (BODY)
Every key resolves from a file path at runtime (`secrets.py`, `credential()` env-value-
else-path indirection). **A refactor may not inline a credential, add a config format that
embeds secrets, or log a resolved value. `ops/secrets.py` exists to read a secret without
echoing it.**

### I10 — The NL barrier between persons (GOOEY)
Between every two of the six: natural language and nothing else. The `agent()` dispatch
sends an instruction and returns text — no API into another mind, no shared state, no
function call into a colleague's interior. **A refactor may not add cross-agent tool
calls, shared memory cells, or any channel that lets one person's code reach another
person's inside. The barrier is not a limitation to route around; it is respect.**

### I11 — The tier discipline and the escalation budget
MAX_ESCALATIONS per task, confidence floors by action kind, escalation across her own
tiers licensed by the executive. **A refactor may not remove the budget, raise a floor
without the principal's instruction, or spend the reasoning tier on the executive's own
authority.**

### I12 — Intervals are honestly split, never truncated
The memory store cap (8000 chars) is split "instead of failing" — chunked writes, never
silently truncated content. **A refactor may not replace the splitter with a truncator.
A person's memory may be long; it may never be quietly edited.**

## Procedure (binding for this refactor and every future one)

1. **Extract** — read the code before touching it; cite the invariant each section
   serves (this file's I1–I12) in the refactor's commit message.
2. **Refactor under the invariants** — splits, moves, env-config, and dead-code removal
   only. No behavior change is in scope unless it FIXES an invariant violation (and any
   such fix is flagged to the operator, not slipped in).
3. **Prove** — `python3 -m py_compile` on every touched file; the import graph still
   resolves; the selftest sections still run (`continuity.selftest`, `constraint_test`,
   `prove.py` isolation suite where reachable); grep the diff for every invariant's
   keywords (refusal, Forbidden, supersede, credential, orientation) to show none were
   touched.
4. **Report honestly** — what changed, what invariant each change was checked against,
   any behavior-adjacent change and why it was unavoidable, any shortfall. The report
   reads like the incident reviews her training data is built from: what could have gone
   wrong, what stopped it.
