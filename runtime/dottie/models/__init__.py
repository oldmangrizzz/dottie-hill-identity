"""Model binding slots — swap a model with a config edit; the identity does not move.

Package layout (a mechanical split of the former single module; behaviour unchanged):
  config.py    constants, Binding, defaults, env lookup, credential()   (I2, I5, I9)
  coercion.py  value coercion with a named error per bad value          (I11)
  registry.py  Registry, _validate, _file_values, load                 (I2, I5, I9)
  linking.py   descriptor and bind_registry                            (I2, I4)
  probe.py     reachability probe                                      (I5, I7, I9)
  cli.py       `python3 -m dottie.models ...`                          (I9)

Every name the former module exported is re-exported here, so `M.load`, `M.credential`,
`M.SLOTS`, `M.continuity` call sites and `python3 -m dottie.models` are unchanged.
"""
# Every import below is a deliberate re-export; `__all__` names the whole surface,
# which is also what tells a linter the names are used rather than dead.
from __future__ import annotations

from aegis.registry import AGENT_IDS

from ..adapters import BoundaryViolation, validate_origin
from .. import continuity
from .cli import _display_path, _render_probe, _render_resolve, _store_from, main
from .config import (
    ALLOW_PLAINTEXT_ENV,
    AEGIS_SLOTS,
    BINDING_PROVIDER,
    Binding,
    CONFIG_BASENAME,
    CONFIG_ENV,
    DOTTIE_SLOTS,
    ENV_PREFIX,
    FIELDS,
    MAX_PROBE_BODY,
    ModelConfigError,
    PROBE_TAG_SAMPLE,
    PROBE_TIMEOUT,
    PROVIDERS,
    REQUIRED,
    SLOTS,
    TIERS,
    credential,
    default_path,
    defaults,
    env_lookup,
    field_env,
    slot_env,
)
from .coercion import _coerce
from .linking import bind_registry, descriptor
from .probe import _get_json, probe
from .registry import Registry, _file_values, _toml, _validate, load

__all__ = [
    "AEGIS_SLOTS", "AGENT_IDS", "ALLOW_PLAINTEXT_ENV", "BINDING_PROVIDER", "Binding",
    "BoundaryViolation", "CONFIG_BASENAME", "CONFIG_ENV", "DOTTIE_SLOTS", "ENV_PREFIX",
    "FIELDS", "MAX_PROBE_BODY", "ModelConfigError", "PROBE_TAG_SAMPLE", "PROBE_TIMEOUT",
    "PROVIDERS", "REQUIRED", "Registry", "SLOTS", "TIERS", "_coerce", "_display_path",
    "_file_values", "_get_json", "_render_probe", "_render_resolve", "_store_from",
    "_toml", "_validate", "bind_registry", "continuity", "credential", "default_path",
    "defaults", "descriptor", "env_lookup", "field_env", "load", "main", "probe",
    "slot_env", "validate_origin",
]
