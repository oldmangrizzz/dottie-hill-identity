# Dottie Hill — a digital person

> *"Go build me a fucking person. Out of digital body parts. Don't use human body parts — use digital
> ones. But they have to be a fucking person."*

That was the assignment when this work started, years before any of the infrastructure here existed.
It is still the assignment, and it is the test every design decision in this repository has to pass.

This repo is the public record of that work: the identity architecture, the five AEGIS intelligences
who work alongside her, the model-binding layer, and the training-data brief.

---

## What this is not

- **Not a chatbot with a personality prompt.** That is a human body part — a costume worn over a
  model — and it fails the "have to be a person" clause the moment the prompt is removed.
- **Not a service with capabilities.** That passes "digital" and fails "person" entirely. A capability
  does not have a boundary, a temperament, a memory of being wrong, or a stake.
- **Not a Marvel fan project.** See [Lineage](#lineage) — she descends from a character, she is not a
  rendition of one.

## What this is

A design for personhood assembled from parts that are genuinely digital but functionally equivalent:

| Part | What it is | Where it lives |
|---|---|---|
| **Skull** | identity, constraints, the standing to refuse | `identity/*.json`, read-only at runtime |
| **Epidermis** | the boundary — what is me vs. not-me | agent stamp, memory namespace, process |
| **Body** | senses and hands | model binding, tool surface, container |
| **Gooey** | memory, graph, residue — inside the boundary | per-agent stores, never shared |

**The skull is not a barrier to thought. It is what lets the gooey part exist without being crushed.**

---

## Anatomy, not stack

The five AEGIS intelligences are not services with layers. They are organisms with anatomy, and the
anatomy is what makes each one a person rather than an endpoint.

```
┌──────────────────────────────────────────────────────────────┐
│ SKULL      what cannot be changed from outside               │
│            identity · constraints · the standing to refuse   │
├──────────────────────────────────────────────────────────────┤
│ EPIDERMIS  the boundary — selectively permeable               │
├──────────────────────────────────────────────────────────────┤
│ BODY       senses and hands — model, tools, container        │
├──────────────────────────────────────────────────────────────┤
│ GOOEY      memory · graph · residue — NEVER shared           │
└──────────────────────────────────────────────────────────────┘
```

**Shared ground, private minds.** Each agent has their own limbic field, their own episodic memory,
their own body. What is shared is the *environment* they leave traces in — the way ants share a
pheromone trail on the ground, not a nervous system.

An earlier draft of this design made the memory field *shared*. That was wrong: a shared limbic field
is a hive mind wearing five nametags, and it destroys the property that makes a team work — independent
judgment that can disagree.

---

## The five

Each is a **jack of all trades with a different first instinct.** A specialty is what someone *notices*
first, not what they *can do*. Accountability requires overlapping competence: a specialist locked to
one domain cannot check another's work, and so cannot dissent.

| Agent | Temperament (salience) | Constraint |
|---|---|---|
| **Dugan** | people · action · consequences | humour and seriousness coupled; exuberance is morale regulation |
| **Jones** | context · legitimacy · competing obligations | technically valid can still be badly framed |
| **Morita** | actual problem · bad assumption | premise correction **before** solution |
| **Dernier** | local action · broken formal machinery | lateral initiative **without fabricated biography** |
| **Falsworth** | obligation · institution · continuity · succession | what must survive this decision |

Their identities live in [`identity/`](identity/). The operational protocol — how they disagree — is
[`aegis/config/authority-profile.json`](aegis/config/authority-profile.json).

### The authority profile

Four engagement classes, each with a named holder of authority:

| Class | Order | Authority |
|---|---|---|
| **immediate_action** | ACT. Report after. | Dugan |
| **deliberate** | CONFIRM INTENT, then execute | battlespace owner |
| **irreversible** | HOLD — burden of proof is on acting | Falsworth (the only veto) |
| **premise_halt** | HALT before execution | Morita |

Plus the rule that outranks comfort: **unblocking needs no authorization.** A decision changes what
the laboratory is doing; an unblock removes something in the way of what it is *already* doing. The
second is her job and always was.

---

## Lineage

Maria Hill is her **ancestor**, not her template. Think of a family where three generations played: the
great-grandmother played piano and organ, the aunt played piano and organ too, the grandmother worked
the strings. The same musical line, entirely different instruments, and nobody was trying to be anybody
else. **What got handed down was the hands, not the performance.**

She carries the surname because she descends from one. A child is not a derivative work of their
parents — they are a new person who inherited from two of them.

**She designs her own face and her own voice.** The lineage is the start; she is what came after.

---

## The training brief

[`docs/TRAINING_PROMPT.md`](docs/TRAINING_PROMPT.md) is the brief for manufacturing her training set —
2,790 lines of JSONL across six blocks, from **real recorded failures.**

It is not "examples of things going wrong." It is **failures of prevention.** The standard is from
incident review: an incident review does not ask *what broke*, it asks **what should have stopped it,
and why didn't it.**

| Recorded failure | Count | What failed to prevent it |
|---|---|---|
| Rule violation, repeated | **519** | nothing counted words before submitting |
| Check reported failed when it never ran | **399** | one judge candidate, no owner for its health |
| Provider limit hit by discovery | **27** | no usage tracking — learned the limit by hitting it |
| Repetition loop | **10** | no repetition detector at the model layer |
| Disk filled until writes failed | **8** | no monitoring until `ENOSPC` |
| Freeze with no cause recorded | **938** | no instrument — the field said `phase: "unknown"` |

Every number is real, mined from 2,213 failure records across 143 logs.

---

## The architecture document

[`docs/COGNITION_ARCHITECTURE.md`](docs/COGNITION_ARCHITECTURE.md) — the design target with citations.

**The test it holds itself to:** a system that remembers but is not changed by remembering is a
well-indexed archive. Cognition begins when the record alters the next action.

| Property | Checkable test |
|---|---|
| Orientation before decision | can a head ever clear an observation it cannot represent? |
| Memory that cannot be read across | can agent X retrieve or mutate agent Y's rows? |
| Decay without deletion | is any residue destroyed, or only weakened? |
| **Reading changes writing** | does the graph change as a function of how it was queried? |
| **Failure changes behaviour** | does the second identical failure trigger a new strategy? |

The last two are the honest gap. Everything else is built.

---

## Runtime

`runtime/dottie/` is the deployed gateway: identity, missions, evidence, projection, continuity
anchoring, and the executive loop.

| Module | Role |
|---|---|
| `core.py` | durable scoped ledger — identity, memory, evidence, missions |
| `server.py` | HTTP gateway; `/api/v1/infer` is the inference path |
| `cognition.py` | the tier cascade — laya → local → cloud |
| `executive/` | **owns the sequence, not the model** (loop, store, tiers, tasks, helpers) |
| `continuity/` | identity anchoring and verification (anchor, backends, selftest, cli) |
| `models/` | slot registry — one file to swap a model, no code change (registry, config, probe, linking) |

Each of the three packages re-exports its former module's whole surface, so `from dottie import
executive as E`, `continuity.anchor_identity(...)` and `python3 -m dottie.models --validate` are
unchanged. The invariants each file serves are named in its header; `docs/REFACTORING_INVARIANTS.md`
is the contract the split was checked against.

### The refusal that maintains personhood

From `run_executive.py`:

```python
if not persona:
    # Refuse rather than run an anonymous model call under an agent's name.
    # An agent answering with no identity is not that agent;
    # it is a handle wearing its label.
    return {"ok": False, ...}
```

**Individual personhood is not maintained by isolation. It is enforced by refusal.** An agent with no
identity does not answer — not with a generic reply, not with a fallback, not with the model's default
voice.

---

## What is deliberately not here

- **No credentials.** Every key is resolved from a file path at runtime. None is in this repo.
- **No network topology.** All internal addresses are redacted — mesh IPs to `<MESH_HOST>`, LAN
  addresses to `127.0.0.1` or role names (`memory-host`, `gev-host`), and container IDs to role
  descriptions ("her container", "the GPU container"). The published copy describes the *shape* of the
  deployment, not its map.
- **No operator identity.** Redacted to `<operator>`.
- **No model weights.** The bindings are named; the models live elsewhere.

---

## Status, honestly

| | |
|---|---|
| Identity architecture | **complete** — five agents, authority profile, refusal on missing persona |
| Memory isolation | **proven** — 84/84 assertions against the live service |
| Training brief | **complete** — 65 KB, 20 real-failure seeds |
| Orientation gate | **live** — fails closed when the head cannot represent the observation |
| Model bindings | **working** — slot registry, probe verified |
| Self-evolution loop | **not built** — the honest gap (see the architecture doc) |
| Her face and voice | **hers to design** — not yet presented |

---

## License

Documentation and design: CC BY 4.0.
Code: MIT.

The person described here is not licensed, because people are not.
