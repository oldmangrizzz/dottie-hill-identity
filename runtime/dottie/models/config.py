"""Slot constants, the Binding record, defaults, env lookup, and credential indirection.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I9 — `credential()` resolves a value from an env var or a FILE path; no literal, never logged.
# I2 — each AEGIS agent has its own slot; the roster is asserted, not inferred.
# I5 — `api_key_configured` reports whether resolution succeeded, never that a key works.
# Mechanical move out of runtime/dottie/models.py — bodies are byte-identical.
# Re-exported by dottie/models/__init__.py so `from dottie import models as M` is unchanged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from aegis.registry import AGENT_IDS

# provider -> the tiers that provider can honestly be. A cloud vendor is not a local model
# and a typed reflex is neither; a binding that claims otherwise is a mislabel.
#
# `openai` is the escape hatch that keeps this layer from being a vendor lock: ANY runtime
# that speaks the OpenAI wire is a valid slot without touching this file again — vLLM,
# llama.cpp's server, LM Studio, TGI, DeepSeek's own API, OpenAI, Together, a second Ollama
# behind a proxy. It may be tier `local` (a box you own) or `cloud` (somebody else's), which
# is why its allowed set has two members: the operator declares which, and the AEGIS rule
# still applies to it exactly as it does to `ollama-cloud`.
PROVIDERS = {
    "ollama-local": ("local",),
    "laya": ("reflex",),
    "ollama-cloud": ("cloud",),
    "openai": ("local", "cloud"),
}
TIERS = ("local", "cloud", "reflex")

DOTTIE_SLOTS = ("dottie.system1", "dottie.system2", "dottie.reasoning")
AEGIS_SLOTS = tuple("aegis." + agent for agent in AGENT_IDS)
SLOTS = DOTTIE_SLOTS + AEGIS_SLOTS

# `slot` is the identity of the binding, not a field to be configured.
FIELDS = ("provider", "model", "base_url", "api_key_env", "timeout_seconds",
          "max_tokens", "tier", "enabled")
REQUIRED = ("provider", "model", "base_url", "tier")

CONFIG_BASENAME = "model_slots.toml"
CONFIG_ENV = "DOTTIE_MODEL_CONFIG"
ENV_PREFIX = "DOTTIE_MODEL_"
# Hosts the operator has declared safe to reach over plaintext HTTP for model inference
# (a private bridge peer, a loopback alias). Empty by default: plaintext is not assumed.
ALLOW_PLAINTEXT_ENV = "DOTTIE_MODEL_ALLOW_PLAINTEXT"
PROBE_TIMEOUT = 4.0
MAX_PROBE_BODY = 1_000_000
PROBE_TAG_SAMPLE = 12
# The label written into the continuity history. Not a vendor: it names the registry that
# produced the descriptor, so a reader of the anchor knows where the compute map came from.
BINDING_PROVIDER = "dottie.model-slots/v1"


class ModelConfigError(ValueError):
    """One or more bindings are not deployable. `.problems` names every one of them.

    The list is complete rather than first-failure: an operator fixing a config at 2 a.m.
    should see all eight slots' faults in one run, each with a distinct `code`.
    """

    def __init__(self, problems):
        self.problems = [dict(p) for p in problems]
        super().__init__("invalid model slots: " + " | ".join(
            "%s %s [%s] %s" % (p.get("slot"), p.get("field"), p.get("code"), p.get("message"))
            for p in self.problems))


@dataclass(frozen=True)
class Binding:
    """One slot's resolved compute. `sources` is bookkeeping, never a config field."""
    slot: str
    provider: str
    model: str
    base_url: str
    api_key_env: str = ""
    timeout_seconds: float = 120.0
    max_tokens: int = 4096
    tier: str = "local"
    enabled: bool = True
    sources: dict = field(default_factory=dict)

    def redacted(self):
        """The binding as a dict. A credential VALUE can never appear here.

        `api_key_env` is the NAME of the environment variable, and `api_key_configured` is a
        boolean about whether that indirection currently resolves. The value itself is only
        ever read inside `credential()` and only ever handed to an Authorization header.
        """
        return {
            "slot": self.slot,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "api_key_configured": credential(self) is not None,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "tier": self.tier,
            "enabled": self.enabled,
            "sources": dict(self.sources),
        }


def default_path():
    """The config file shipped beside this module."""
    return Path(__file__).resolve().parents[1] / CONFIG_BASENAME


def defaults():
    """The three tiers and the five agents. EVERY slot names a real model id.

    These are the SAME values `dottie/model_slots.toml` carries, so a host with no config file
    still resolves every slot identically (`test_the_shipped_file_does_not_drift_from_the
    _built_in_defaults` asserts it field by field).

    The five AEGIS agents share tier 3's model id on purpose: the principal named the cloud
    model and the shared account, not five distinct agent models. Per-agent differentiation is
    a one-line edit he can make later; inventing five ids he never named would be a decision
    this module has no authority to take.
    """
    # Tier 1 = llama.cpp's Vulkan server (measured, 220 ms warm, device node held open).
    # `defaults()` must equal the shipped file or a host without the file binds other compute.
    local = {"provider": "openai", "model": "lfm3",
             "base_url": "http://127.0.0.1:8488", "api_key_env": "",
             "timeout_seconds": 120.0, "max_tokens": 4096,
             "tier": "local", "enabled": True}
    reflex = {"provider": "laya", "model": "laya-sidecar",
              "base_url": "http://127.0.0.1:8473", "api_key_env": "",
              "timeout_seconds": 30.0, "max_tokens": 64,
              "tier": "reflex", "enabled": True}
    cloud = {"provider": "ollama-cloud", "model": "deepseek-v4.1-flash",
             "base_url": "https://ollama.com", "api_key_env": "OLLAMA_API_KEY",
             "timeout_seconds": 180.0, "max_tokens": 4096,
             "tier": "cloud", "enabled": True}
    table = {"dottie.system1": dict(local), "dottie.system2": dict(reflex),
             "dottie.reasoning": dict(cloud)}
    agent = dict(cloud)
    for agent_id in AEGIS_SLOTS:
        table[agent_id] = dict(agent)
    return table


def slot_env(slot):
    """The environment stem for one slot: `dottie.system1` -> `DOTTIE_MODEL_DOTTIE_SYSTEM1`."""
    return ENV_PREFIX + slot.replace(".", "_").upper()


def field_env(slot, name):
    """The environment variable(s) that override ONE field of one slot, highest first.

    The model has two accepted spellings because it is the field an operator most often
    changes: `DOTTIE_MODEL_DOTTIE_SYSTEM1` (bare) and `DOTTIE_MODEL_DOTTIE_SYSTEM1_MODEL`.
    When neither is set the field falls through to the file and then the default.
    """
    stem = slot_env(slot)
    if name == "model":
        return (stem, stem + "_MODEL")
    return (stem + "_" + name.upper(),)


def env_lookup(slot, name, environ=None):
    """(variable, raw) for the first set spelling of a field, or (None, None)."""
    env = os.environ if environ is None else environ
    for variable in field_env(slot, name):
        raw = env.get(variable)
        if raw is not None and raw.strip() != "":
            return variable, raw
    return None, None


# --------------------------------------------------------------------------- #
# credential indirection — env value, else a file path, else nothing
# --------------------------------------------------------------------------- #
def credential(binding, environ=None):
    """The secret VALUE for this binding, or None. Never printed, never stored.

    Mirrors `dottie.config._secret`: the environment variable named by `api_key_env` first,
    then `<that name>_FILE` as a path to a key file. The value is stripped and must be
    non-empty; a set-but-blank variable counts as absent so a cleared shell cannot silently
    downgrade a cloud slot into an unauthenticated one.
    """
    env = os.environ if environ is None else environ
    name = (binding.api_key_env or "").strip()
    if not name:
        return None
    value = (env.get(name) or "").strip()
    if value:
        return value
    path = (env.get(name + "_FILE") or "").strip()
    if not path:
        return None
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None
