"""Model binding slots — swap a model with a config edit; the identity does not move.

THE TIER MAP (the principal's architecture, implemented, not reinterpreted):

  tier 1  `dottie.system1`    a LOCAL model, provider `ollama-local`   (plain completion)
  tier 2  `dottie.system2`    LAYA, provider `laya`, tier `reflex`      (typed verdicts, no authority)
  tier 3  `dottie.reasoning`  cloud reasoning, provider `ollama-cloud` (tapped when wanted)

Tier 1 and tier 2 are the local System-1/System-2 pair. Tier 3 is the reasoning model that
is asked only when the local pair is not enough. All cloud models — tier 3 and every AEGIS
agent — ride ONE Ollama Cloud account (`https://ollama.com`) behind ONE credential, so a
slot names an environment variable and never a secret.

THE FIVE AEGIS INTELLIGENCES (`aegis.dugan`, `aegis.jones`, `aegis.morita`, `aegis.dernier`,
`aegis.falsworth`) each get their OWN slot, and every one of those slots is CLOUD-ONLY. The
principal's requirement is enforced here as a hard validation error (`aegis_cloud_only`) and
proven by `tests/test_model_slots.py`, not merely stated in prose: an `aegis.*` binding whose
`tier` is not `cloud` cannot be resolved into a validated registry. The ids come from
`aegis.registry.AGENT_IDS` — this module does not retype the roster.

EVERY SLOT NAMES A REAL MODEL ID. The five agents share tier 3's model id because the principal
named one cloud model on one shared account, not five; per-agent differentiation is a one-line
config edit he can make later. The only `PLACEHOLDER` left in the config is an ARTIFACT note on
`dottie.system1` — LFM 3 is the named model and its tag is not yet installed — not an open
decision.

PRECEDENCE (highest first), per field:

  1. process environment   `DOTTIE_MODEL_<SLOT>`            -> `model`
                           `DOTTIE_MODEL_<SLOT>_<FIELD>`    -> that one field
  2. the config file       `dottie/model_slots.toml` (or `--config` / `DOTTIE_MODEL_CONFIG`)
  3. `defaults()`          the table in this file

`<SLOT>` is the slot id with `.` replaced by `_`, uppercased: `dottie.system1` ->
`DOTTIE_MODEL_DOTTIE_SYSTEM1`, `aegis.dugan` -> `DOTTIE_MODEL_AEGIS_DUGAN`. `<FIELD>` is one
of `PROVIDER MODEL BASE_URL API_KEY_ENV TIMEOUT_SECONDS MAX_TOKENS TIER ENABLED`. An empty
environment value is treated as unset, so a cleared variable cannot blank a field. Every
binding carries `sources` (field -> `env` | `file` | `default`) so `--resolve` can say which
layer won instead of printing a number with no provenance.

WHAT THIS MODULE NEVER DOES: it never downloads a model, never loads one, never calls a paid
API to answer a question, and never prints a credential. `--probe` issues `GET /api/tags`
(ollama) or `GET /info` (laya) and reports what answered — a configured model id is never
reported as available because the config says so.

CLI:

    python3 -m dottie.models --resolve [--config PATH] [--json]
    python3 -m dottie.models --validate
    python3 -m dottie.models --probe [--config PATH] [--json]
    python3 -m dottie.models --bind  [--mount DIR | --base-url URL --token-file F]

`--bind` appends ONE anchor row whose `model_id` is the composite descriptor naming the tier
map (see `descriptor`), leaving `dottie.lineage.compute_instructions` byte-identical.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit as urllib_parse
from urllib.request import ProxyHandler, Request, build_opener

from aegis.registry import AGENT_IDS

from . import continuity
from .adapters import BoundaryViolation, validate_origin

try:  # Python 3.11+ (the CT 115 runtime is 3.11.2). 3.10 gets a named error, not a guess.
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    _toml = None

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
    return Path(__file__).resolve().parent / CONFIG_BASENAME


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


# --------------------------------------------------------------------------- #
# coercion
# --------------------------------------------------------------------------- #
def _coerce(name, raw):
    """(value, error). Strings are stripped; empty is a value (validated later as missing)."""
    if name == "timeout_seconds":
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None, "expected a number of seconds"
        return (value, None) if value > 0 else (None, "must be greater than zero")
    if name == "max_tokens":
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None, "expected a positive integer"
        return (value, None) if value > 0 else (None, "must be greater than zero")
    if name == "enabled":
        if isinstance(raw, bool):
            return raw, None
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True, None
        if text in ("0", "false", "no", "off"):
            return False, None
        return None, "expected true/false"
    return str(raw).strip(), None


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
@dataclass
class Registry:
    """Every slot's effective binding, plus where each field came from."""
    bindings: dict
    config_path: str | None = None
    problems: list = field(default_factory=list)
    config_slots: int = 0
    config_changed: int = 0

    def resolve(self, slot):
        """One slot's Binding. An unknown slot is an error, never a guessed neighbour."""
        if slot not in self.bindings:
            raise KeyError("%r is not a model slot (slots: %s)" % (slot, ", ".join(SLOTS)))
        return self.bindings[slot]

    def effective(self):
        """slot -> Binding for all eight slots, in canonical order."""
        return {slot: self.bindings[slot] for slot in SLOTS}

    def errors(self):
        """Every validation problem as dicts, without raising."""
        return _validate(self)

    def validate(self):
        """Raise ModelConfigError unless every slot is deployable. Returns the slots passed."""
        problems = _validate(self)
        if problems:
            raise ModelConfigError(problems)
        return list(SLOTS)


def _validate(registry):
    problems = list(registry.problems)
    for slot in SLOTS:
        binding = registry.resolve(slot)
        if binding.provider not in PROVIDERS:
            problems.append(dict(
                slot=slot, field="provider", code="unknown_provider",
                message="provider %r is not one of %s"
                        % (binding.provider, ", ".join(sorted(PROVIDERS)))))
        for name in REQUIRED:
            if not str(getattr(binding, name) or "").strip():
                problems.append(dict(
                    slot=slot, field=name, code="missing_field",
                    message="%s is required and is empty" % name))
        if binding.tier not in TIERS:
            problems.append(dict(
                slot=slot, field="tier", code="invalid_tier",
                message="tier %r is not one of %s" % (binding.tier, ", ".join(TIERS))))
        elif binding.provider in PROVIDERS and \
                binding.tier not in PROVIDERS[binding.provider]:
            problems.append(dict(
                slot=slot, field="tier", code="tier_mismatch",
                message="provider %r can only be tier %s, not %r"
                        % (binding.provider,
                           "|".join(PROVIDERS[binding.provider]), binding.tier)))
        # THE PRINCIPAL'S REQUIREMENT, in code: the five Howling Commandos run cloud models.
        if slot.startswith("aegis.") and binding.tier != "cloud":
            problems.append(dict(
                slot=slot, field="tier", code="aegis_cloud_only",
                message="an aegis.* slot is CLOUD-ONLY by the principal's order; tier %r "
                        "is rejected (no local model may serve an AEGIS agent)"
                        % (binding.tier,)))
        if binding.tier == "cloud" and not (binding.api_key_env or "").strip():
            problems.append(dict(
                slot=slot, field="api_key_env", code="missing_credential_env",
                message="a cloud binding must name the environment variable holding the "
                        "shared Ollama Cloud key (e.g. OLLAMA_API_KEY)"))
        if "/Users/" in binding.base_url or "/home/" in binding.base_url:
            problems.append(dict(
                slot=slot, field="base_url", code="workstation_path",
                message="base_url %r names a workstation path; resolved bindings must be "
                        "valid where the process runs (CT 115), not on a laptop"
                        % (binding.base_url,)))
        # Origin policy: an API origin must be loopback, or TLS, or a host the operator has
        # explicitly allow-listed for this deployment (`DOTTIE_MODEL_ALLOW_PLAINTEXT`, a
        # comma-separated host list). The inference box on the private LAN is exactly such a
        # host, and a blanket refusal would either force TLS on a bridge segment or push the
        # model back onto the workstation — both worse than a named exception.
        try:
            validate_origin(binding.base_url)
        except BoundaryViolation as exc:
            host = urllib_parse(binding.base_url).hostname or ""
            allow = {h.strip() for h in
                     (os.environ.get(ALLOW_PLAINTEXT_ENV) or "").split(",") if h.strip()}
            if host and host in allow:
                pass  # declared in the environment; the reason lives in the env, not here
            else:
                problems.append(dict(
                    slot=slot, field="base_url", code="bad_base_url",
                    message="%r is not an acceptable API origin: %s (a private-LAN peer must "
                            "be declared in %s)" % (binding.base_url, exc,
                                                    ALLOW_PLAINTEXT_ENV)))
    return problems


def _file_values(data, problems):
    """The `[slot."<id>"]` tables, split into per-slot dicts. Unknown keys are reported."""
    table = {}
    if not isinstance(data, dict) or not data:
        return table
    for key in data:
        if key != "slot":
            problems.append(dict(slot="*", field=str(key), code="unknown_field",
                                 message="unexpected top-level key %r; only [slot.\"<id>\"] "
                                         "tables belong in this file" % (key,)))
    slots = data.get("slot")
    if slots is None:
        return table
    if not isinstance(slots, dict):
        problems.append(dict(slot="*", field="slot", code="unknown_field",
                             message="[slot] must be a table keyed by slot id"))
        return table
    for slot, values in slots.items():
        if slot not in SLOTS:
            problems.append(dict(slot=slot, field="slot", code="unknown_slot",
                                 message="%r is not a model slot (slots: %s)"
                                         % (slot, ", ".join(SLOTS))))
            continue
        if not isinstance(values, dict):
            problems.append(dict(slot=slot, field="slot", code="unknown_field",
                                 message="a slot entry must be a table of fields"))
            continue
        clean = {}
        for name, raw in values.items():
            if name not in FIELDS:
                problems.append(dict(slot=slot, field=name, code="unknown_field",
                                     message="%r is not a binding field (fields: %s)"
                                             % (name, ", ".join(FIELDS))))
                continue
            value, error = _coerce(name, raw)
            if error:
                problems.append(dict(slot=slot, field=name, code="invalid_value",
                                     message="config value %r: %s" % (raw, error)))
                continue
            clean[name] = value
        table[slot] = clean
    return table


def load(path=None):
    """Resolve every slot: env over file over defaults. See the module docstring."""
    problems = []
    # `--config` wins over `DOTTIE_MODEL_CONFIG`; with neither, the file shipped beside this
    # module. A named-but-absent file is an error; an absent DEFAULT file is not (defaults()
    # covers every slot), so a host without the file still resolves all eight.
    named = path is not None
    if not named:
        path = (os.environ.get(CONFIG_ENV) or "").strip() or None
        named = bool(path)
    candidate = Path(path).expanduser() if path else default_path()
    config_path = None
    data = {}
    if candidate.is_file():
        config_path = str(candidate.resolve())
        if _toml is None:
            raise ModelConfigError([dict(
                slot="*", field="config", code="toml_unavailable",
                message="%s is TOML and needs Python 3.11+ (tomllib); this interpreter is "
                        "%d.%d" % (candidate, sys.version_info[0], sys.version_info[1]))])
        try:
            data = _toml.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ModelConfigError([dict(
                slot="*", field="config", code="config_unreadable",
                message="%s: %s" % (candidate, exc))]) from exc
    elif named:
        raise ModelConfigError([dict(
            slot="*", field="config", code="config_missing",
            message="no such config file: %s" % candidate)])

    file_values = _file_values(data, problems)
    base = defaults()
    bindings = {}
    for slot in SLOTS:
        values = dict(base[slot])
        sources = {name: "default" for name in FIELDS}
        # A file that restates the default has decided nothing, so the field keeps reporting
        # `default`. Only a value the file actually CHANGES is sourced to the file — otherwise
        # every field would read "file" and `--resolve` would stop distinguishing anything.
        for name, value in file_values.get(slot, {}).items():
            # A file that names a field DECLARES it: the file is the operator's table, and
            # `defaults()` exists only for a host that has no file. Reporting "file" even
            # when the value equals the built-in default is the difference between "the file
            # is decorative" (a lie an operator would discover the hard way) and "this is
            # where the value comes from". The drift test still holds the two in agreement.
            sources[name] = "file"
            values[name] = value
        for name in FIELDS:
            variable, raw = env_lookup(slot, name)
            if variable is None:
                continue
            value, error = _coerce(name, raw)
            if error:
                problems.append(dict(
                    slot=slot, field=name, code="invalid_value",
                    message="environment %s=%r: %s (kept %r)"
                            % (variable, raw, error, values[name])))
                continue
            values[name] = value
            sources[name] = "env"
        bindings[slot] = Binding(slot=slot, sources=sources, **values)
    from_env = sum(1 for b in bindings.values() for s in b.sources.values() if s == "env")
    from_file = sum(1 for b in bindings.values() for s in b.sources.values() if s == "file")
    return Registry(bindings=bindings, config_path=config_path, problems=problems,
                    config_slots=len(file_values), config_changed=from_env + from_file)


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


# --------------------------------------------------------------------------- #
# probe — reachability only, never a download and never a load
# --------------------------------------------------------------------------- #
def _get_json(url, headers, timeout):
    """(payload, error). No model bytes are fetched: a metadata route is all this asks."""
    opener = build_opener(ProxyHandler({}))  # never route a control plane through a proxy
    request = Request(url, method="GET", headers=headers)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_PROBE_BODY + 1)
        if len(raw) > MAX_PROBE_BODY:
            return None, "response exceeded %d bytes" % MAX_PROBE_BODY
        payload = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            return None, "response was not a JSON object"
        return payload, None
    except HTTPError as exc:
        return None, "HTTP %s from %s" % (exc.code, url)
    except (URLError, OSError) as exc:
        return None, "%s: %s" % (type(exc).__name__, getattr(exc, "reason", exc))
    except ValueError as exc:
        return None, "unparseable response: %s" % (exc,)


def probe(registry, timeout=PROBE_TIMEOUT, environ=None):
    """One observation per slot. Nothing is downloaded, loaded, or inferred from config.

    ollama (local and cloud): `GET {base_url}/api/tags` lists the tags that exist, and the
    configured `model` is compared against them — that is the "ready to drop in" signal. That
    route is metadata and is PUBLIC on ollama.com (measured from CT 115, 2026-11-03: `GET
    https://ollama.com/api/tags` -> 200 without a key), so a cloud slot's tag presence is
    observable without the credential; each entry reports `auth` so a reader never mistakes a
    public tag list for an authenticated judgment. The key is still required to judge.
    laya: `GET {base_url}/info`, whose payload names the model the sidecar is serving; a
    typed-verdict sidecar publishes no tag list, so `present` is `unknown` and the reported
    model is quoted instead of being asserted as a match.
    """
    results = []
    for slot in SLOTS:
        binding = registry.resolve(slot)
        entry = {"slot": slot, "provider": binding.provider, "model": binding.model,
                 "base_url": binding.base_url}
        if not binding.enabled:
            entry.update(status="SKIPPED", reason="disabled in config (enabled = false)",
                         present="unknown")
            results.append(entry)
            continue
        if binding.provider not in PROVIDERS:
            entry.update(status="SKIPPED", present="unknown",
                         reason="unknown provider %r; nothing to ask" % (binding.provider,))
            results.append(entry)
            continue
        if binding.provider == "openai":
            # Any OpenAI-wire runtime: list models if the vendor exposes /v1/models, else say
            # the endpoint answered and presence is unknown. Never assume a tag exists.
            secret = credential(binding, environ)
            headers = {"Accept": "application/json"}
            if secret is not None:
                headers["Authorization"] = "Bearer " + secret
            entry["auth"] = "bearer" if secret is not None else "none"
            payload, error = _get_json(binding.base_url.rstrip("/") + "/v1/models",
                                       headers, timeout)
            if error is not None:
                entry.update(status="UNREACHABLE", present="unknown", reason=error)
            else:
                data = payload.get("data")
                names = [str(m.get("id") or "") for m in data
                         if isinstance(m, dict)] if isinstance(data, list) else []
                names = [n for n in names if n]
                repo = binding.model.split(":")[0]
                same_repo = [n for n in names if n.split(":")[0] == repo]
                entry.update(status="REACHABLE", tags=len(names),
                             tag_sample=names[:PROBE_TAG_SAMPLE],
                             present="yes" if (binding.model in names or same_repo) else "no")
                if same_repo and binding.model not in names:
                    entry["matched_tag"] = same_repo[0]
            results.append(entry)
            continue
        if binding.provider in ("ollama-local", "ollama-cloud"):
            headers = {"Accept": "application/json"}
            # `/api/tags` is a METADATA route. On ollama.com it answers anonymously (measured
            # 2026-11-03 from CT 115: `GET https://ollama.com/api/tags` -> 200), so a cloud
            # slot's tag presence CAN be observed without the key. The key is still required
            # for anything that actually judges; the auth state is reported, never assumed.
            secret = credential(binding, environ) if binding.provider == "ollama-cloud" else None
            if secret is not None:
                headers["Authorization"] = "Bearer " + secret
            entry["auth"] = ("bearer" if secret is not None else
                             "none (tag list is public; a judgment call is not)")
            payload, error = _get_json(binding.base_url.rstrip("/") + "/api/tags",
                                       headers, timeout)
            if error is not None:
                entry.update(status="UNREACHABLE", present="unknown", reason=error)
                results.append(entry)
                continue
            models = payload.get("models")
            names = [str(m.get("name") or m.get("model") or "")
                     for m in models if isinstance(m, dict)] if isinstance(models, list) else []
            names = [n for n in names if n]
            exact = binding.model in names
            # Compare on the REPO part of the id: `lfm3:1.7b-q4_K_M` and a server advertising
            # bare `lfm3` are the same family, and an operator needs to be told that the tag
            # they configured shares a repo with one that exists — not left to infer it from
            # an empty sample list.
            repo = binding.model.split(":")[0]
            same_repo = [n for n in names if n.split(":")[0] == repo]
            entry.update(status="REACHABLE", tags=len(names),
                         tag_sample=names[:PROBE_TAG_SAMPLE],
                         present="yes" if (exact or same_repo) else "no")
            if same_repo and not exact:
                entry["matched_tag"] = same_repo[0]
            results.append(entry)
            continue
        # laya: an HTTP sidecar on the same host as Dottie, never a workstation path.
        payload, error = _get_json(binding.base_url.rstrip("/") + "/info",
                                   {"Accept": "application/json"}, timeout)
        if error is not None:
            entry.update(status="UNREACHABLE", present="unknown", reason=error)
        else:
            entry.update(status="REACHABLE", present="unknown",
                         reported_model=str(payload.get("model") or ""),
                         loaded=payload.get("loaded"), backend=payload.get("backend"),
                         note="typed verdicts; /info publishes no tag list, so presence is "
                              "reported as unknown, never asserted")
        results.append(entry)
    return results


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _display_path(path):
    """The config path as an operator should see it: package-relative when it is ours.

    `--resolve` output must carry NO workstation path, not even in a banner: an operator
    reading it on CT 115 must not be shown a `/Users/...` line and have to work out whether
    some slot depends on a laptop. The shipped file therefore prints as
    `dottie/model_slots.toml`; an explicitly chosen file outside the package prints as the
    path that was chosen, which is the one fact a banner exists to convey.
    """
    candidate = Path(path).expanduser().resolve()
    package = Path(__file__).resolve().parent
    try:
        return str(candidate.relative_to(package.parent))
    except ValueError:
        return str(candidate)


def _render_resolve(registry, stream):
    print("config: %s" % (_display_path(registry.config_path)
                          if registry.config_path
                          else "<none> — every field from defaults() and the environment>"),
          file=stream)
    if registry.config_path:
        # `declared` counts the slots the file mentions; `changed` counts the FIELDS it or the
        # environment actually moved off defaults(). A file that restates the defaults has
        # decided nothing, and this is the line that says so instead of implying otherwise.
        print("slots declared in file: %d   fields sourced from file+env: %d"
              % (registry.config_slots, registry.config_changed), file=stream)
    for slot in SLOTS:
        binding = registry.resolve(slot)
        print("", file=stream)
        print("%s  tier=%s  enabled=%s" % (slot, binding.tier,
                                          "true" if binding.enabled else "false"),
              file=stream)
        print("  binding: %s:%s" % (binding.provider, binding.model), file=stream)
        print("  base_url: %s" % binding.base_url, file=stream)
        print("  api_key_env: %s   api_key_configured: %s"
              % (binding.api_key_env or "(none)",
                 "yes" if credential(binding) is not None else "no"), file=stream)
        print("  timeout_seconds: %g   max_tokens: %d"
              % (binding.timeout_seconds, binding.max_tokens), file=stream)
        overrides = ["%s=%s" % (name, source)
                     for name, source in binding.sources.items() if source != "default"]
        print("  overrides: %s" % (", ".join(overrides) or "none (all defaults)"),
              file=stream)


def _render_probe(results, stream):
    for entry in results:
        print("%s  [%s]  %s" % (entry["slot"], entry["provider"], entry["base_url"]),
              file=stream)
        print("  status: %s" % entry["status"], file=stream)
        if entry.get("reason"):
            print("  reason: %s" % entry["reason"], file=stream)
        if entry.get("auth"):
            print("  auth: %s" % entry["auth"], file=stream)
        if entry["status"] == "REACHABLE":
            if "tags" in entry:
                print("  tags: %d observed, sample: %s"
                      % (entry["tags"], ", ".join(entry["tag_sample"]) or "(none)"),
                      file=stream)
            if entry.get("reported_model"):
                print("  reported_model: %s" % entry["reported_model"], file=stream)
            if entry.get("note"):
                print("  note: %s" % entry["note"], file=stream)
        print("  present: %s" % entry.get("present", "unknown"), file=stream)
        if entry.get("matched_tag"):
            print("  matched_tag: %s" % entry["matched_tag"], file=stream)
        print("", file=stream)


def _store_from(args):
    """The continuity store selector, reused rather than reimplemented."""
    token = args.token
    if not token and args.token_file:
        token = Path(args.token_file).expanduser().read_text().strip()
    return continuity._store(args.base_url, token, args.mount,
                             identity=args.identity, engine=args.mind_engine)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m dottie.models",
        description="Model binding slots: swap a model with a config edit; identity stays.")
    parser.add_argument("--resolve", action="store_true",
                        help="print the effective binding per slot, keys redacted")
    parser.add_argument("--validate", action="store_true",
                        help="run Registry.validate() and print PASS per slot")
    parser.add_argument("--probe", action="store_true",
                        help="reachability only: GET /api/tags (ollama) or /info (laya)")
    parser.add_argument("--bind", action="store_true",
                        help="append this registry's tier map to the identity anchor")
    parser.add_argument("--config", default=None, help="config path (default: beside module)")
    parser.add_argument("--timeout", type=float, default=PROBE_TIMEOUT,
                        help="per-request probe timeout in seconds")
    parser.add_argument("--identity", default="dottie-hill")
    parser.add_argument("--mount", default=None, help="continuity engine mount (for --bind)")
    parser.add_argument("--mind-engine", default=None, help="path to mind.py (for --bind)")
    parser.add_argument("--base-url", default=None, help="continuity REST base (for --bind)")
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if not any((args.resolve, args.validate, args.probe, args.bind)):
        parser.print_help()
        return 2
    try:
        registry = load(args.config)
    except ModelConfigError as exc:
        for problem in exc.problems:
            print("FAIL  %s %s [%s] %s" % (problem.get("slot"), problem.get("field"),
                                          problem.get("code"), problem.get("message")),
                  file=sys.stderr)
        return 2

    for problem in registry.problems:
        print("PROBLEM  %s %s [%s] %s" % (problem.get("slot"), problem.get("field"),
                                         problem.get("code"), problem.get("message")),
              file=sys.stderr)

    if args.resolve:
        if args.json:
            print(json.dumps({slot: registry.resolve(slot).redacted() for slot in SLOTS},
                             indent=2, sort_keys=True))
        else:
            _render_resolve(registry, sys.stdout)
        return 0

    if args.validate:
        try:
            passed = registry.validate()
        except ModelConfigError as exc:
            for problem in exc.problems:
                print("FAIL  %s %s [%s] %s" % (problem.get("slot"), problem.get("field"),
                                              problem.get("code"), problem.get("message")))
            print("VALIDATE: FAIL (%d problems)" % len(exc.problems))
            return 1
        for slot in passed:
            print("PASS  %s  %s:%s  tier=%s" % (slot, registry.resolve(slot).provider,
                                               registry.resolve(slot).model,
                                               registry.resolve(slot).tier))
        print("VALIDATE: PASS (%d slots)" % len(passed))
        return 0

    if args.probe:
        results = probe(registry, timeout=args.timeout)
        if args.json:
            print(json.dumps(results, indent=2, sort_keys=True))
        else:
            _render_probe(results, sys.stdout)
        return 0

    # --bind
    try:
        anchor, text = bind_registry(registry, continuity, store=_store_from(args),
                                     identity=args.identity)
    except (ValueError, OSError, continuity.ContinuityError) as exc:
        # A store that is absent, unreachable, or holding a superseded anchor is an operator
        # condition, not a crash: name what to do instead of unwinding a traceback.
        print("bind: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        print("bind: the anchor needs a store — pass --mount DIR with --mind-engine PATH "
              "(local engine) or --base-url URL with --token/--token-file (deployed "
              "service), and run `python3 -m dottie.continuity --anchor` first if the store "
              "has no anchor yet.", file=sys.stderr)
        return 2
    payload = {"descriptor": text, "anchor_row_id": anchor.get("anchor_row_id"),
               "previous_anchor_id": anchor.get("previous_anchor_id"),
               "binding_provider": BINDING_PROVIDER,
               "model_binding_history": anchor.get("model_binding_history")}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
