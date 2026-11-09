"""The registry: load a config file, validate it, report where every field came from.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I2 — the five AEGIS slots are CLOUD-ONLY; no local model may serve an AEGIS agent.
# I5 — a cloud binding must NAME the env var holding the key, or validation fails.
# I9 — the config file carries a variable name, never a credential value.
# Mechanical move out of runtime/dottie/models.py — bodies are byte-identical.
# Re-exported by dottie/models/__init__.py so `from dottie import models as M` is unchanged.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit as urllib_parse

from ..adapters import BoundaryViolation, validate_origin
from .coercion import _coerce
from .config import (
    ALLOW_PLAINTEXT_ENV,
    CONFIG_ENV,
    FIELDS,
    PROVIDERS,
    REQUIRED,
    SLOTS,
    TIERS,
    Binding,
    ModelConfigError,
    default_path,
    defaults,
    env_lookup,
)

try:  # Python 3.11+ (the CT 115 runtime is 3.11.2). 3.10 gets a named error, not a guess.
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    _toml = None

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
