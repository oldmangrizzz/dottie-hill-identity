"""One store interface, two transports: the deployed REST service and the local engine.

# Invariants served (docs/REFACTORING_INVARIANTS.md):
# I9 — the bearer token is a value passed in, never a literal here.
# I12 — a row over the cap is split by the store, never truncated here.
# I4 — rows are appended only; nothing in this file mutates a stored row.
# Mechanical move out of runtime/dottie/continuity.py — bodies are byte-identical.
# Re-exported by dottie/continuity/__init__.py so `from dottie import continuity` is unchanged.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .common import (
    ANCHOR_KIND,
    ANCHOR_SCOPE,
    DEFAULT_AGENT,
    DEFAULT_IDENTITY,
    DEFAULT_PRINCIPAL,
    DEFAULT_URL,
    MAX_RECALL,
    ContinuityError,
    _canonical,
    _find_engine,
)

class RestStore:
    """The deployed memory service: `POST /api/memories`, `/api/memories/recall`, `/info`."""

    backend = "rest"

    def __init__(self, base_url, token, principal=DEFAULT_PRINCIPAL, agent=DEFAULT_AGENT,
                 identity=DEFAULT_IDENTITY, timeout=10.0):
        if not isinstance(base_url, str) or not base_url.strip():
            raise ContinuityError("rest backend needs a base_url", "invalid")
        if not isinstance(token, str) or not token.strip():
            raise ContinuityError("rest backend needs a bearer token", "unauthorized")
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.principal = principal
        self.agent = agent
        self.identity = identity
        self.timeout = timeout
        # No proxy: a local control plane must not be routed through an ambient HTTP_PROXY.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # -- transport ------------------------------------------------------- #
    def _request(self, method, path, body=None, auth=True):
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = _canonical(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = "Bearer " + self.token
            headers["X-Cpki-User-Id"] = self.principal
            headers["X-Dottie-Agent"] = self.agent
        request = urllib.request.Request(self.base_url + path, data=data,
                                        headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise ContinuityError("%s %s -> HTTP %s: %s" % (method, path, exc.code, raw[:400]),
                                  "http_%s" % exc.code) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ContinuityError("%s %s unreachable: %s: %s"
                                  % (method, path, type(exc).__name__, exc), "unreachable") from exc

    # -- interface ------------------------------------------------------- #
    def info(self):
        _, payload = self._request("GET", "/info", auth=False)
        audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
        return {"backend": self.backend, "gate_exit": audit.get("gate_exit"),
                "failing": audit.get("failing"), "server": payload.get("server"),
                "mount": payload.get("mount")}

    def reachable(self):
        try:
            self.info()
            return True
        except ContinuityError:
            return False

    def save(self, content):
        status, payload = self._request("POST", "/api/memories",
                                        {"content": content, "kind": ANCHOR_KIND,
                                         "scope": ANCHOR_SCOPE})
        row = self._row(payload)
        audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
        row["duplicate"] = bool(payload.get("duplicate"))
        row["gate_exit"] = audit.get("gate_exit")
        row["status"] = status
        return row

    def recall(self, query, limit=MAX_RECALL):
        _, payload = self._request("POST", "/api/memories/recall",
                                   {"query": query, "limit": limit})
        rows = payload.get("memories") if isinstance(payload.get("memories"), list) else []
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(payload):
        row = payload if isinstance(payload, dict) else {}
        return {
            "id": str(row.get("id") or ""),
            "content": str(row.get("content") or ""),
            "agent": row.get("agent"),
            "valid_from": row.get("valid_from"),
            "state": row.get("state"),
            "certified": row.get("certified"),
        }


class EngineStore:
    """The mind_engine engine over a local directory store — `python3 mind.py save/recall`."""

    backend = "engine"

    def __init__(self, root_dir, engine=None, principal=DEFAULT_PRINCIPAL,
                 agent=DEFAULT_AGENT, identity=DEFAULT_IDENTITY, timeout=120.0):
        if not root_dir:
            raise ContinuityError("engine backend needs a mount root", "invalid")
        self.root = Path(root_dir).expanduser().resolve()
        self.engine = Path(engine).expanduser() if engine else _find_engine()
        if self.engine is None or not Path(self.engine).is_file():
            raise ContinuityError("engine backend needs mind.py (set --mind-engine or "
                                  "DOTTIE_MIND_ENGINE)", "unavailable")
        self.principal = principal
        self.agent = agent
        self.identity = identity
        self.timeout = timeout

    # -- transport ------------------------------------------------------- #
    def _run(self, *args):
        command = [sys.executable, str(self.engine)] + [str(a) for a in args]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContinuityError("mind.py %s failed to start: %s"
                                  % (args[0] if args else "", type(exc).__name__),
                                  "unavailable") from exc
        if proc.returncode not in (0, 1):
            raise ContinuityError("mind.py %s exited %s: %s"
                                  % (args[0] if args else "", proc.returncode,
                                     (proc.stderr or proc.stdout).strip()[:400]),
                                  "engine_%s" % proc.returncode)
        return proc

    # -- interface ------------------------------------------------------- #
    def init(self, force=False):
        args = ["init", str(self.root)]
        if force:
            args.append("--force")
        return self._run(*args)

    def info(self):
        proc = self._run("audit", str(self.root))
        gates = ""
        found = re.search(r"GATES:\s*(\d+)/(\d+)", proc.stdout)
        if found:
            gates = found.group(1) + "/" + found.group(2)
        failing = len(re.findall(r"^FAIL ", proc.stdout, re.MULTILINE))
        return {"backend": self.backend, "gate_exit": proc.returncode, "failing": failing,
                "gates": gates, "mount": str(self.root)}

    def reachable(self):
        return (self.root / ".mind" / "ledger.jsonl").is_file()

    def save(self, content):
        proc = self._run("save", content, "--kind", ANCHOR_KIND, "--scope", ANCHOR_SCOPE,
                         "--principal", self.principal, "--consent", "granted",
                         "--agent", self.agent, str(self.root))
        found = re.search(r"^RECEIPT\s+(M-\S+)", proc.stdout, re.MULTILINE)
        if not found:
            raise ContinuityError("mind.py save returned no receipt id", "engine_output")
        row_id = found.group(1)
        stored = self._rows().get(row_id, {})
        return {
            "id": row_id,
            "content": stored.get("content", content),
            "agent": stored.get("agent", self.agent),
            "valid_from": stored.get("valid_from"),
            "state": stored.get("state"),
            "certified": None,
            "duplicate": "absorbed:   DUPLICATE" in proc.stdout,
            "gate_exit": None,
            "status": 0,
        }

    def recall(self, query, limit=MAX_RECALL):
        proc = self._run("recall", query, "--limit", str(min(limit, MAX_RECALL)),
                         "--agent", self.agent, str(self.root))
        ids = re.findall(r"^\s+(M-[0-9a-fA-F]+)\s+match=", proc.stdout, re.MULTILINE)
        rows = self._rows()
        return [rows[i] for i in ids if i in rows]

    def _rows(self):
        """Every personal-memory row in the mount, content from the memory file it names.

        The deployed server serves a row's content from the memory file (`_memory_body`), not
        from the ledger's `claim`; this reads the same bytes in the same order, so a tampered
        memory file is what `verify` sees — exactly as it would over REST.
        """
        ledger = self.root / ".mind" / "ledger.jsonl"
        rows = {}
        if not ledger.is_file():
            return rows
        for line in ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if str(record.get("source_type") or "") != "PERSONAL_MEMORY":
                continue
            rid = str(record.get("evidence_id") or "")
            if not rid:
                continue
            content = str(record.get("claim") or "")
            block = record.get("promotion") if isinstance(record.get("promotion"), dict) else {}
            digest = str(block.get("digest") or "")
            path = self.root / ".mind" / "memories" / (digest + ".json") if digest else None
            if path is not None and path.is_file():
                try:
                    body = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    body = {}
                if isinstance(body, dict) and isinstance(body.get("content"), str):
                    content = body["content"]
            agent = record.get("agent")
            if not isinstance(agent, str) or not agent.strip():
                agent = block.get("agent")
            rows[rid] = {
                "id": rid,
                "content": content,
                "agent": agent if isinstance(agent, str) and agent.strip() else None,
                "valid_from": record.get("valid_from"),
                "state": record.get("evidence_state"),
                "certified": None,
            }
        return rows


def _store(base_url=None, token=None, root_dir=None, *, identity=DEFAULT_IDENTITY,
           agent=DEFAULT_AGENT, principal=DEFAULT_PRINCIPAL, engine=None, timeout=None):
    """Pick a backend: explicit root_dir is the engine, explicit base_url is REST, else probe."""
    if root_dir:
        return EngineStore(root_dir, engine=engine, principal=principal, agent=agent,
                           identity=identity,
                           **({"timeout": timeout} if timeout else {}))
    if base_url:
        return RestStore(base_url, token, principal=principal, agent=agent, identity=identity,
                         **({"timeout": timeout} if timeout else {}))
    url = os.environ.get("DOTTIE_MEMORY_URL", "").strip() or DEFAULT_URL
    token = token or os.environ.get("DOTTIE_MEMORY_TOKEN", "").strip()
    if token:
        candidate = RestStore(url, token, principal=principal, agent=agent, identity=identity)
        if candidate.reachable():
            return candidate
    engine_path = engine or _find_engine()
    mount = os.environ.get("DOTTIE_MEMORY_MOUNT", "").strip()
    if mount and engine_path:
        return EngineStore(mount, engine=engine_path, principal=principal, agent=agent,
                           identity=identity)
    raise ContinuityError("no memory store: give --base-url with a token, or --mount with "
                          "mind.py (env DOTTIE_MEMORY_URL/DOTTIE_MEMORY_TOKEN/"
                          "DOTTIE_MEMORY_MOUNT)", "unavailable")
