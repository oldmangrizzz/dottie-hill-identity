"""The deployed memory service, spoken to over its REST wire.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I4 — append-only: `save` only ever POSTs a NEW row.
# I12 — content over MAX_CHARS is split into ordered parts, never silently truncated.
# I9 — the token arrives as a value from a file; no literal and no logging here.
# Mechanical move out of runtime/dottie/executive.py — bodies are byte-identical.
# Re-exported by dottie/executive/__init__.py so `from dottie import executive as E` is unchanged.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .common import AGENT, KIND, SCOPE

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

@dataclass
class Store:
    """The deployed memory service, spoken to over its REST wire."""
    base_url: str
    token: str
    principal: str = "grizz"
    agent: str = AGENT
    timeout: float = 30.0

    def _call(self, method: str, path: str, payload: dict | None = None) -> tuple[int | None, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + self.token,
                   "X-Cpki-User-Id": self.principal,
                   "X-Dottie-Agent": self.agent}
        request = urllib.request.Request(self.base_url.rstrip("/") + path, data=body,
                                        method=method, headers=headers)
        try:
            with _opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                return exc.code, {"error": raw[:200]}
        except (urllib.error.URLError, OSError) as exc:
            return None, {"error": "%s: %s" % (type(exc).__name__, exc)}

    MAX_CHARS = 8000  # memory store cap is 8192; stay under it and split instead of failing

    def save(self, content: str, kind: str = KIND, scope: str = SCOPE) -> dict:
        # The model can emit more than the store accepts. A hard 422 lost the whole step,
        # so the output is split on line boundaries and saved as ordered parts.
        text = content.rstrip()
        if len(text) <= self.MAX_CHARS:
            return self._save_one(text, kind, scope)
        parts, buf = [], ""
        for line in text.splitlines(True):
            if len(buf) + len(line) > self.MAX_CHARS and buf:
                parts.append(buf); buf = ""
            while len(line) > self.MAX_CHARS:
                parts.append(line[:self.MAX_CHARS]); line = line[self.MAX_CHARS:]
            buf += line
        if buf: parts.append(buf)
        first = None
        for i, part in enumerate(parts, 1):
            header = "part %d/%d\n" % (i, len(parts))
            payload = self._save_one(header + part, kind, scope)
            if first is None: first = payload
        payload = dict(first or {})
        payload["parts"] = len(parts)
        return payload

    def _save_one(self, content, kind, scope):
        status, payload = self._call("POST", "/api/memories",
                                     {"content": content, "kind": kind, "scope": scope})
        if status not in (200, 201):
            raise RuntimeError("save failed (%s): %s" % (status, payload.get("error")))
        return payload

    def recall(self, query: str, limit: int = 20) -> list:
        status, payload = self._call("POST", "/api/memories/recall",
                                     {"query": query, "limit": limit,
                                      "includeInvalidated": False})
        if status != 200:
            raise RuntimeError("recall failed (%s): %s" % (status, payload.get("error")))
        return payload.get("memories") or []
