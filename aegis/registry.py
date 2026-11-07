"""The five AEGIS intelligences, and their memory clients.

Canonical roster, the principal-specified five: `dugan`, `jones`, `morita`, `dernier`,
`falsworth`. Bucky Barnes is EXCLUDED — deliberate, not an oversight: the exclusion is
asserted in the registry itself (see `_assert_roster`) so a later edit cannot silently add
a sixth record back.

`dottie-hill` is a DIFFERENT writer: an agent stamp belonging to the identity-continuity
workstream, present on the same mount. It is not part of this roster and nothing here
creates, edits, or measures it as if it were.
"""
from __future__ import annotations

import json
from pathlib import Path

from .client import MemoryClient

# Identity records live with the rest of the runtime's lineage data; read-only here.
RUNTIME_ROOT = Path(__file__).resolve().parent.parent
IDENTITY_DIR = RUNTIME_ROOT / "dottie" / "lineage_data"

# id -> display name. Display names are the records' own `display_name`; asserting them
# here means a drifted record is a test failure rather than a silent rename.
AEGIS = {
    "dugan": "Timothy \u201cDum Dum\u201d Dugan",
    "jones": "Gabe Jones",
    "morita": "Jim Morita",
    "dernier": "Jacques Dernier",
    "falsworth": "James Montgomery Falsworth",
}

# The exclusion, written down where it is checked rather than only in prose.
EXCLUDED = ("bucky", "barnes", "bucky_barnes")

AGENT_IDS: tuple[str, ...] = tuple(AEGIS)


def _assert_roster() -> None:
    if len(AGENT_IDS) != 5:
        raise AssertionError("AEGIS roster must be exactly five ids, got %r" % (AGENT_IDS,))
    for forbidden in EXCLUDED:
        for agent_id in AGENT_IDS:
            if forbidden in agent_id.lower():
                raise AssertionError("%r is excluded from the AEGIS roster" % agent_id)
        for name in AEGIS.values():
            if forbidden in name.lower():
                raise AssertionError("roster display name %r names an excluded identity" % name)


def identity_path(agent_id: str) -> Path:
    """The read-only identity record for one agent."""
    if agent_id not in AEGIS:
        raise KeyError("%r is not an AEGIS agent id (roster: %s)"
                       % (agent_id, ", ".join(AGENT_IDS)))
    return IDENTITY_DIR / ("aegis-%s.json" % agent_id)


def identity(agent_id: str) -> dict:
    """Parse one agent's identity record. Missing/invalid is an error, never a default."""
    path = identity_path(agent_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise KeyError("identity record for %r is unreadable at %s: %s"
                       % (agent_id, path, exc)) from exc


def clients(base_url: str, token: str, principal: str = "grizz") -> dict[str, MemoryClient]:
    """One client per AEGIS agent, keyed by agent id. Order is the canonical roster."""
    _assert_roster()
    return {agent_id: MemoryClient(base_url, token, agent_id, principal=principal)
            for agent_id in AGENT_IDS}


_assert_roster()
