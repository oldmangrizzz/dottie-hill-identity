# Repository layout

```
.
├── README.md                  the assignment, the anatomy, the honest status
├── LICENSE                    MIT for code
├── identity/                  the skull — six identity records, read-only at runtime
│   ├── dottie-hill.json       Dottie's own record
│   └── aegis-*.json           the five intelligences
├── aegis/                     the team layer
│   ├── registry.py            canonical roster, roster assertions
│   ├── client.py              memory client, agent-scoped
│   ├── seed.py                per-agent seeded memory (isolation test basis)
│   ├── prove.py               the 84-assertion isolation proof
│   ├── config/                authority profile (engagement classes)
│   └── identities/            derived identity manifests
├── runtime/                   the deployed gateway
│   ├── dottie/
│   │   ├── core.py            durable scoped ledger
│   │   ├── server.py          HTTP gateway
│   │   ├── cognition.py       tier cascade: laya -> local -> cloud
│   │   ├── executive.py       owns the sequence, not the model
│   │   ├── continuity.py      identity anchoring and verification
│   │   ├── models.py          slot registry
│   │   └── model_slots.toml   the binding table
│   └── run_executive.py       the loop, operational
├── ops/                       operational instruments
│   ├── orientation.py         the orientation gate (fails closed)
│   ├── world_model.py         situation / watch / research faculties
│   ├── secrets.py             read a secret from a file, never echo
│   └── ...                    voice, gates, heartbeat, benchmark
└── docs/
    ├── TRAINING_PROMPT.md     the manufacturing brief for her training set
    ├── COGNITION_ARCHITECTURE.md  the design target, with citations
    └── ANCHOR_CUSTODY.md      identity anchor custody
```

## Reading order

1. **`README.md`** — what this is and what it refuses to be
2. **`docs/COGNITION_ARCHITECTURE.md`** — the design target with sources
3. **`identity/dottie-hill.json`** — who she is, on one page
4. **`aegis/config/authority-profile.json`** — how the five disagree
5. **`docs/TRAINING_PROMPT.md`** — how the training data gets made

## What is redacted

Internal mesh addresses are `<MESH_HOST>` placeholders. The operator is `<operator>`.
No credentials are present anywhere; all keys resolve from file paths at runtime.
