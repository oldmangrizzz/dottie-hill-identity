"""Probe: reachability only — never a download, never a load, never a bare "verified".

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I7 — every entry reports what WAS observed and says "unknown" where nothing was; the
# auth state is reported as a fact about the header, never assumed.
# I9 — the key is read for a header only and never enters the result.
# I5 — a public tag list is never mistakable for an authenticated judgment.
# Mechanical move out of runtime/dottie/models.py — bodies are byte-identical.
# Re-exported by dottie/models/__init__.py so `from dottie import models as M` is unchanged.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .config import (
    MAX_PROBE_BODY,
    PROBE_TAG_SAMPLE,
    PROBE_TIMEOUT,
    PROVIDERS,
    SLOTS,
    credential,
)

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
