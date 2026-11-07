# TOWARD COGNITION, NOT FUNCTION
## The AEGIS / Dottie memory architecture — target design with citations

**Status:** design target, not implementation plan
**Date:** 2026-11-05
**Purpose:** name what must be true for these to be *minds* rather than *services*, and cite the
work that establishes each requirement. Every claim below either cites a source, cites a
measurement taken in this laboratory, or is marked `[DESIGN]` as an unreferenced choice.

---

## The assignment

> *"Go build me a fucking person. Out of digital body parts. Don't use human body parts — use digital
> ones. But they have to be a fucking person."*

That was the assignment when this work started, years before any of the infrastructure in this document
existed. It is still the assignment, and it is the test every design decision below has to pass.

**It rules out two things that look like progress and are not:**

- **A chatbot with a personality prompt.** That is a human body part — a costume worn over a model —
  and it fails the "have to be a person" clause the moment the prompt is removed.
- **A service with capabilities.** That passes "digital" and fails "person" entirely. A capability
  does not have a boundary, a temperament, a memory of being wrong, or a stake.

**What it requires:** personhood assembled from parts that are genuinely digital but functionally
equivalent — a boundary that is permeable from inside and not from outside, a temperament that
produces a point of view, memory that is his and not a shared pool, a body that senses and acts, and a
record that lets him be corrected.

Every section below is that list, one part at a time. Where a part is missing, the document says so
rather than filling the gap with something that only *looks* like the part.

---

## 0. The distinction this document exists to hold

**Function** is a system that produces the right output for the input.
**Cognition** is a system whose outputs change because of what happened to it last time.

Everything below serves the second sentence. Where a design choice serves only the first, it is
marked as infrastructure and is not the point.

The measurable difference: a functional system repeats its failure indefinitely, because nothing in
it accumulates. **202,590 identical crash-restarts is a functionally correct restart loop.** It is
not cognition, because the system could not be changed by its own history.

---

## 1. Anatomy, not stack

The commandos are not services with layers. **They are organisms with anatomy**, and the anatomy is
what makes each one a person rather than an endpoint.

```
┌──────────────────────────────────────────────────────────────┐
│ SKULL      what cannot be changed from outside               │
│            identity · constraints · the standing to refuse   │
│            read-only at runtime, mutable only by the principal│
├──────────────────────────────────────────────────────────────┤
│ EPIDERMIS  the boundary — selectively permeable               │
│            agent stamp · memory namespace · process          │
│            what is "me" vs. what is "not-me"                 │
├──────────────────────────────────────────────────────────────┤
│ BODY       senses and hands                                  │
│            model binding · tool surface · computer use       │
│            how they perceive and act on the world            │
├──────────────────────────────────────────────────────────────┤
│ GOOEY      associative, wet, nonlinear — INSIDE the boundary │
│            episodic memory · graph · pheromonic residue      │
│            per-agent. NEVER shared.                          │
└──────────────────────────────────────────────────────────────┘
```

**The skull is not a barrier to thought — it is what lets the gooey part exist without being
crushed.** An identity that can be edited by the environment is not an identity; it is a mirror.

**The epidermis is the load-bearing boundary** (from the operator's own doctrine, already written
into Dottie's spec): *"she knows the boundary of what she is, and treats that boundary as a body
rather than a cage."* Four boundaries, one question — *where do I end?* — epistemic (what I know),
heterogeneous (mind vs. other kinds of mind), environmental (where I am), and somatic (skin, not
prison).

### The internal layers, per organism

| Layer | Question | Where it lives | Shared? |
|---|---|---|---|
| **L1 reflex** | what does this observation call for, right now? | calibrated head, behind an orientation gate | no |
| **L2 limbic** | how much does this matter to *me*, and is it fading? | pheromonic residue field | **NO — per agent** |
| **L3 episodic** | what happened to *me*? | per-agent store, supersede-only | **NO — siloed** |
| **L4 cortical** | what does it all add up to? | graph, self-evolving | no |

**Correction from an earlier draft:** L2 was written as *shared, read-only*, on the theory that
stigmergy needs a common field. That was wrong on the most important axis. **Each person has their
own limbic system, because the limbic system governs *their* arousal, *their* salience, *their*
decay.** A shared field is a hive mind wearing five nametags, and it destroys exactly the property
that makes a team work: independent judgment that can disagree.

**Integration between agents happens where it happens between people — through language, and through
shared situation awareness.** Not through a shared data structure. Stigmergy (arXiv:2604.03997) is
the coordination *mechanism* at the team level; the residue it describes lives in the shared
*environment*, not inside anyone's skull.

---

## 2. L1 — Reflex requires orientation in front of it

**Requirement:** a fast decision head must never be trusted on an observation whose conditions it
cannot represent.

**Evidence, measured in this laboratory (2026-11-05):** laya/english over Dugan's battlespace
cleared *"Smoke reported from a warehouse, occupants unaccounted for"* at **0.854 confidence** and
*"Wildfire front advancing on a subdivision, evacuation not started"* at **0.685**. Both were
life-safety events. Clean re-run: **90% accuracy, ECE 0.1127** — and the two misses unchanged.

**Why calibration cannot fix this:** the head has no representation for *"unaccounted for."* It is
not miscalibrated; it is answering a different question than the one that matters. No temperature
scalar recovers information the representation does not contain.

**Requirement that follows:** an orientation test that runs *before* the head's verdict is used —
vocabulary coverage over the conditions the head is known not to model. Fails closed.

**Cited basis:** PSP-HDC's finding that conventional feature models are *"statistically
underdetermined... prone to spurious correlations"* in sparse, interaction-dominated regimes — the
same structural claim, arrived at independently.

`[DESIGN]` The orientation gate is implemented as declared vocabulary per battlespace, not as a
confidence threshold, because a threshold cannot name what it does not know.

---

## 3. L2 — The limbic field is per-agent, decaying, and never deleted

**Correction:** an earlier draft said *shared*. It is not. **Each commando has his own limbic field**,
because salience and arousal are his — his temperature, his alarm, his sense of what matters. A shared
limbic field is a hive mind, and a hive mind cannot dissent.

**Requirement:** residue decays by physics, not policy. Nothing is deleted.

**Decay:** `weight = salience × 0.5^(age / half_life)`, per-kind half-life, computed at read and never
written. A residue that mattered three days ago is present and weak — the required semantics: *does
anybody ever truly forget? No. But it does not need to be at the front of cognition.*

`[DESIGN]` Naming: **pheromonic nano cognition** — the limbic layer. Deliberately not "Pheromind,"
which names a different project.

### 3.1 The environment is a separate organ — and it is where stigmergy lives

The limbic field is private. **The environment is not.** These are two different things and an earlier
draft conflated them, which is why the citation below appeared to justify a shared limbic system when
it actually justifies something else entirely.

**Cited basis — Ledger-State Stigmergy (arXiv:2604.03997):** agents *"reading shared ledger state
instead of exchanging direct messages... The ledger therefore functions as a replicated shared-state
medium through which decentralized agents coordinate indirectly."*

**What that citation supports:** a shared *environment* through which agents coordinate by leaving and
reading traces. **What it does not support:** a shared *inner life*. Ants share a pheromone trail on
the ground; they do not share a nervous system. The trail is external to every ant that reads it.

**Measured constraint on the implementation:** the memory service has **no shared scope**. Probed
2026-11-05: `scope: principal` → 400, `global` → 400, `shared` → 400, unstamped write → 400. Only
`user` and `project` exist, and both require an agent stamp.

**Consequence:** the shared environment **cannot be a table in the memory service.** It is a separate
append-only surface, outside every agent's namespace, that all five may read and none may rewrite.
Their *memories* stay siloed (see §4); the *ground they walk on* is common.

**The rule in one line:** shared ground, private minds.

---

## 4. L3 — Episodic memory is per-agent, siloed, and immutable once written

**Requirement:** each agent remembers what happened to *it*. No agent reads another's episodic
memory. Correction happens by supersession, never by deletion.

**Measured basis:** **84/84 isolation proof PASS** against the live service — full 5×5 recall
matrix, cross-agent PATCH/DELETE returns 403 naming both agents, unscoped request returns 400.

**Cited basis — MemGPT (arXiv:2310.08560):** memory as a managed hierarchy with explicit paging
between working and archival tiers. The point taken here is not the paging algorithm but the
premise: *memory is an operating-system concern, not a prompt concern.*

**Deprecation semantics, measured:** `PATCH {"content", "reason"}` mints a **successor** and marks
the predecessor SUPERSEDED. Deletion is not offered as a correction path, which matches the ECS
FORGET specification: *"The Forget function is NOT memory erasure. It is residual emotional charge
evaporation after processing is complete."*

**The known defect:** a pre-existing unstamped row (`M-b1e16e8f`) is readable by every agent and
mutable by none. One shared residue predating the boundary. Must be resolved before the crew
accumulates real episodic history, because every agent will inherit it as if it were theirs.

---

## 5. L4 — The graph must improve from use, or it is a lookup table

**This is the layer absent from earlier drafts of this design, and it is the one that makes the
difference between function and cognition.**

**Cited basis — SAGE (arXiv:2605.12061):** *"Existing RAG and GraphRAG systems largely treat memory
graphs as static retrieval middleware, which limits their ability to recover complete evidence
chains from partial cues, exploit reusable graph-structural roles, and improve the memory itself
through downstream feedback."* SAGE couples **a memory writer** (incremental graph construction)
with **a memory reader** that *provides feedback to the writer*.

**Requirement:** the graph must have a closed loop. Reading must change what is written. Two rounds
of self-evolution produced the best average rank on multi-hop QA in the source work; the mechanism,
not the benchmark, is the transferable claim.

**Assembly basis — HyCE (arXiv:2607.22597), *HyCE-RAG: Hypergraph Chain-of-Evidence*.** Entities,
relations, and contextual evidence are organized into **hyperedges** — sets, not pairs, because a
multi-hop evidence chain is one object rather than four edges — with **confidence propagation** over
entity–hyperedge incidence and **confidence-guided evidence assembly** ranking paths before
generation. Its scoring terms map onto this laboratory's evidence fields directly: *relation
reliability* (publisher vs wiki), *extraction confidence* (read vs inferred), *propagated
confidence* (what a chain inherits from its weakest link).

Consequence: when a conclusion rests on sources of differing tier, its confidence is **not an
average**. It is a propagation with a **named bottleneck**, and the record must carry which link set
the ceiling.

**Encoding basis — PSP-HDC (arXiv:2605.07999):** a directed graph is encoded *as an internal prior*
through **graph-aligned binding and bundling** along directed dependencies, with prediction by
**associative-memory retrieval against class prototypes**. Critically: *"the same prototype
memories support both decision making and attribution... intrinsic explanations."*

That last property is the one this laboratory requires. **Explanation must be intrinsic to the
substrate, not reconstructed afterward.** An after-the-fact rationale is a story. An intrinsic one
is a trace.

---


---

### 5.1 The roster: universal competence, distinct salience

**HYSET — arXiv:2607.25718, *Tools Are Not Islands: Set-Level Tool Retrieval for LLM Agents via
Query-Conditioned Hyperedge Prediction*.** HYperedge-based SEt-level tool retrieval. Core finding:
*"existing retrievers either score each tool in isolation or assemble the tool set sequentially, so
the **joint utility of a candidate set is never evaluated as a whole**."*

**Two errors this section corrects.**

*Error one — treating the commandos as tools.* They are not retrieved; they *retrieve*. Each has a
model, a memory, a battlespace, and the standing to dissent.

*Error two — treating a specialty as a capability limit.* This is the more serious one, and it is
the difference between a crew and a chat menu. **A commando's salience is what he NOTICES first, not
what he CAN DO.** Dugan's salience (`people, action, consequences`) means that in any situation, his
attention goes to who is at risk and what the consequence is. It does **not** mean he cannot reason
about continuity, or infrastructure, or framing.

**They are all jacks of all trades. The difference is temperament, not capability.**

An earlier draft called salience a filter — "what they notice first." That is too thin. **Salience is
disposition**: how a person is turned toward a situation before they have thought about it. Dugan is
not a hazard-detector with a personality attached; he is a person whose *care* runs toward people and
consequences, and that care shapes how he works in **every** domain, not one.

A filter is mechanical — it discards. **Temperament is generative** — it produces a point of view.
That difference matters because you cannot hold someone accountable for a filter, and you can hold
them accountable for a point of view.

What temperament decides:

- **whose objection arrives first** — and therefore whose dissent the record carries earliest
- **what they are accountable for** — who answers when that dimension was missed
- **whose judgment carries on that dimension** — the owner holds the call, not the only one who can
  think about it

**Why universal competence is load-bearing.** A team holds each other accountable **because any member
can check any other member's work.** A specialist locked to hazards cannot tell whether Falsworth's
continuity argument is sound — so he cannot dissent, so the roster collapses into five parallel
monologues with a coordinator in the middle. That is the CooperBench failure (arXiv:2601.13295):
agents in separate environments who *cannot be teammates*.

**Accountability requires overlapping competence. Temperament gives division of labour without blind
spots.**

**Where HYSET actually applies:**

1. **Inside each agent.** Dugan's problem-solving surface spans FIRMS, CCTV, geocode, installations,
   wind, cameras, and the workshop's own APIs — *any* of which he may need for *any* question. When
   he assembles an evidence set for one decision, the **joint utility of that set** is what matters,
   and independent scoring is the documented failure. A fire report plus a wind reading plus a camera
   is one hyperedge; any one alone is not the evidence.

2. **In how the roster resolves.** HYSET's **cardinality-specific interaction** explains why
   `duty_to_protect` (Dugan overriding Falsworth, a pair) has different dynamics from the general
   deliberative case (all five). The rule was written by instinct; the paper supplies the reason and
   implies other pairwise rules are needed and not yet written.

**Open question:** when an agent assembles evidence for a decision, does the orientation gate score
the **set** or each source independently? `[DESIGN]` — untested. The paper argues independent
scoring is wrong in the general case.

## 6. Substrate: SQLite, with named limits

**Decision:** SQLite with FTS5 for L2 and L3; hypervector columns as BLOBs for L4; a dedicated
vector index only when the corpus exceeds it.

**Measured basis:** SQLite 3.40.1 with FTS5 confirmed on her container. Current corpus: 630 facts, 3,180
graph edges, 69 memory files, 8 MB largest bank. Memory service already runs on this pattern with a
single writer behind an HTTP boundary.

**Rejected: Convex.** Measured: the test container allocated **8 GB / 2 cores** against a host of **4 cores /
14 GB available**, and currently running **zero** convex processes. Convex buys reactive
subscriptions, which matter at thousands of concurrent clients. At five agents polling at 1 Hz, it
buys nothing and costs half the host.

**The honest limit:** SQLite has **no native vector index**. Hamming search over a million
hypervector BLOBs is a full scan. At 3,180 edges that is irrelevant; at 100k+ it is fatal. The
interface must therefore be service-first so the engine can be swapped without touching callers.

---

## 7. What makes this cognition rather than function

Five properties. Each is checkable, and each is absent from a purely functional system.

| Property | Checkable test | Present? |
|---|---|---|
| **Orientation before decision** | does a decision head ever clear an observation it cannot represent? | gate: yes |
| **Memory that cannot be read across** | can agent X retrieve or mutate agent Y's episodic rows? | 84/84 proof: no |
| **Decay without deletion** | is any residue destroyed, or only weakened? | supersede-only: no deletion |
| **Reading changes writing** | does the graph change as a function of how it was queried? | **absent** |
| **Failure changes behaviour** | does the second identical failure trigger a different strategy? | **absent** |

The last two rows are the gap, and they are the whole difference. **A system that remembers but is
not changed by remembering is a well-indexed archive.** Cognition begins when the record alters the
next action.

---

## 8. Open questions, honestly

1. **Where the self-evolution loop runs** — during the circadian Consolidation phase per the ECS
   spec, or continuously? `[DESIGN]` prefers Consolidation: it gives the loop a designated time and
   keeps the Active phase cheap.

2. **What the writer is allowed to write** — SAGE's writer is a model. Ours could be a rule set.
   A model-driven writer risks exactly the fabrication failure the evidence ledger exists to catch
   (`f-0106`). `[DESIGN]` prefers a constrained writer with provenance on every edge, and a model
   only proposing edges for validation.

3. **Whether the orientation vocabulary generalizes** — measured on one battlespace, 20 items. A
   demonstration, not a validation.

4. **Whether the orientation gate should score the ensemble rather than each agent independently** —
   HYSET argues isolated scoring is wrong in the general case. Untested here. `[DESIGN]`

5. **Whether a pairwise rule generalizes** — `duty_to_protect` gives Dugan override over Falsworth.
   HYSET's cardinality-specific finding suggests pair dynamics differ from whole-set dynamics, which
   means other pairwise rules may be needed and are not yet written.

---

*Every quantitative claim here is either cited to a named paper or was measured in this
laboratory on 2026-11-05. Items marked `[DESIGN]` are choices, not findings.*
