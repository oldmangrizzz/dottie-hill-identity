"""Identity linkage: the compute descriptor and its append to the identity anchor.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I4 — a binding is ONE NEW anchor row that supersedes by link; nothing is rewritten.
# I2 — only Dottie's three tiers participate; the AEGIS slots are separate intelligences.
# I5 — the descriptor names compute, never a credential.
# Mechanical move out of runtime/dottie/models.py — bodies are byte-identical.
# Re-exported by dottie/models/__init__.py so `from dottie import models as M` is unchanged.
"""
from __future__ import annotations

from .. import continuity
from .config import BINDING_PROVIDER, DOTTIE_SLOTS

# --------------------------------------------------------------------------- #
# identity linkage
# --------------------------------------------------------------------------- #
def descriptor(registry):
    """The deterministic compute descriptor the anchor records for Dottie.

        s1=ollama-local:lfm3|s2=laya:laya-sidecar|s3=ollama-cloud:deepseek-v4.1-flash

    Only Dottie's three tiers participate. The AEGIS agents are separate intelligences with
    their own slots and their own anchors; folding them in would claim Dottie's identity runs
    on the agents' compute, which is exactly the coupling this layer removes.
    """
    parts = []
    for slot, label in zip(DOTTIE_SLOTS, ("s1", "s2", "s3")):
        binding = registry.resolve(slot)
        parts.append("%s=%s:%s" % (label, binding.provider, binding.model))
    return "|".join(parts)


def bind_registry(registry, continuity_module=continuity, *, anchor=None, store=None,
                  identity="dottie-hill", now=None):
    """Append the registry's descriptor to the identity anchor. Returns (anchor, descriptor).

    `continuity.bind_model` is called with the composite descriptor as the model id and this
    module's label as the provider, so ONE new anchor row records the whole tier map. The
    identity payload is copied forward unchanged by `continuity`, which is why swapping a
    model cannot move `compute_instructions`. Raises whatever `continuity` raises: a missing
    anchor, a superseded anchor, or a descriptor over the store's bound.
    """
    module = continuity_module or continuity
    text = descriptor(registry)
    if anchor is None:
        anchor = module.load_anchor(store=store, identity=identity)
    if anchor is None:
        raise ValueError("no identity anchor in the store: run "
                         "`python3 -m dottie.continuity --anchor` first")
    bound = module.bind_model(anchor, text, provider=BINDING_PROVIDER, store=store,
                              identity=identity, now=now)
    return bound, text
