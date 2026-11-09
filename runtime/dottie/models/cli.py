"""`python3 -m dottie.models` — resolve, validate, probe, or bind the slot table.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I7 — --probe prints only observations, and "unknown" where nothing was observed.
# I9 — keys are redacted in every render path; only a boolean about resolution is printed.
# I2 — --validate refuses a cloud binding that does not name its credential variable.
# Mechanical move out of runtime/dottie/models.py — bodies are byte-identical.
# Re-exported by dottie/models/__init__.py so `from dottie import models as M` is unchanged.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import continuity
from .config import BINDING_PROVIDER, PROBE_TIMEOUT, SLOTS, ModelConfigError, credential
from .linking import bind_registry
from .registry import load
from .probe import probe


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
    package = Path(__file__).resolve().parents[1]
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
