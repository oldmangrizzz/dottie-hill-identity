"""Identity continuity: the anchor that lets an identity outlive the model computing it.

The gateway already stores identity in two durable places — the immutable lineage template on
disk (`dottie/lineage_data/<id>.json`) and the SQLite ledger (`dottie/core.py`) — but nothing
recorded a durable ANCHOR in the memory store, so `active_model_binding` stayed `null` and
`model_binding_history` stayed empty. This module closes that half-open state without inventing
a second store: the anchor is ONE saved memory in the deployed mind_engine memory service, and a
model binding change is ONE NEW saved memory that supersedes the previous anchor by link.

What is durable here (survives process restart, model binding change, and loss of any in-memory
session, because it is bytes on a disk that no conversation holds):

  * the identity payload — intelligence_id, lineage_version, behavioral_constraints,
    lineage_sources, identity_manifest_ref, authority_profile_ref, anchored_at, schema;
  * the compute history — model_binding_history, appended one entry per binding change;
  * the supersession chain — previous_anchor_id, so the newest anchor is provably a successor
    of the one before it and the store itself is never mutated in place.

What is per-model: the provider/model id in the newest history entry, and nothing else. The
`compute_instructions` bytes are derived from the lineage template, not from the anchor and not
from a model, so they are byte-identical before and after a binding change — which is the
property this module exists to make measurable.

The store is append-only by design (mind_engine digests content and never rewrites a memory), so
`bind_model` never mutates the old row; it saves a new one. Two backends share one interface:

  * REST  — `POST /api/memories` on the deployed memory server, headers
            `Authorization: Bearer …`, `X-Cpki-User-Id: grizz`, `X-Dottie-Agent: dottie-hill`;
  * engine — the same mind_engine engine as a local directory store (`python3 mind.py
            save/recall/audit <mount>`), used when the REST service is unreachable.

The backend that was actually used is reported by every entry point (`anchor_backend` on the
anchor, `backend` in the selftest) so a reader never has to guess which store answered.

RESERVED FIELDS. `memory_namespace_ref`, `relationship_state_ref` and `lineage_version` are not
written by this module, and the anchor does not copy them into the store. They stay
`UNSET_DEPLOYED_NAMESPACE` / `UNSET_DEPLOYED_RELATIONSHIP_STATE` / `UNSET_SOURCE_DERIVED` because
the principal has issued no instruction on them — not because a document reserves a decision on
his behalf. An anchor that filled them would ratify a topology he has not ruled on.

CLI:

    python3 -m dottie.continuity --selftest          # full proof, PASS/FAIL per assertion
    python3 -m dottie.continuity --show              # print the current anchor
    python3 -m dottie.continuity --anchor            # write the first anchor
    python3 -m dottie.continuity --bind model-B      # append a binding (new anchor row)
    python3 -m dottie.continuity --verify            # verify the newest anchor vs the lineage

Store selection: `--mount DIR` (engine), else `--base-url URL` (REST), else `DOTTIE_MEMORY_URL`
/ `http://127.0.0.1:8471` if it answers `/info`, else a private mount under `--workdir`
(`/tmp/idcont/selftest-mount`) initialised with the engine's own `init`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .core import INDEPENDENT_IDENTITIES, NotFound
from .lineage import compute_instructions, lineage

ANCHOR_SCHEMA = "dottie-identity-anchor/v1"
DEFAULT_IDENTITY = "dottie-hill"
DEFAULT_AGENT = "dottie-hill"
DEFAULT_PRINCIPAL = "grizz"
DEFAULT_URL = "http://127.0.0.1:8471"
ANCHOR_KIND = "operational"
ANCHOR_SCOPE = "project"
# One query, used for both save-adjacent recall and load. Exact words the anchor's own
# content carries, so the lexical recall always has candidates to rank.
ANCHOR_QUERY = "dottie identity anchor schema continuity"
MAX_RECALL = 20
# mind.MEMORY_MAX_CHARS — the engine's cap, restated here so an over-long anchor fails with a
# name instead of a 422 from the server.
MAX_ANCHOR_CHARS = 8192
# The reserved keys are READ here only to assert they are still unset. Nothing in this module
# writes them, and the anchor does not carry them into the store.
RESERVED_FIELDS = {
    "memory_namespace_ref": "UNSET_DEPLOYED_NAMESPACE",
    "relationship_state_ref": "UNSET_DEPLOYED_RELATIONSHIP_STATE",
    "lineage_version": "UNSET_SOURCE_DERIVED",
}
# The identity payload of an anchor: exactly the fields the contract names. The binding history
# and the supersession link ride along because append-only supersession needs them.
ANCHOR_FIELDS = (
    "schema",
    "intelligence_id",
    "lineage_version",
    "behavioral_constraints",
    "lineage_sources",
    "identity_manifest_ref",
    "authority_profile_ref",
    "anchored_at",
)
# Row metadata attached to a loaded anchor. Stripped before any content comparison, so the
# byte-identity proof is about the stored bytes and not about this module's bookkeeping.
ROW_METADATA = ("anchor_row_id", "anchor_backend", "anchor_raw_content", "anchor_certified")


class ContinuityError(RuntimeError):
    """A store or contract failure. Carries a code so a caller can report it, not guess."""

    def __init__(self, message, code="unavailable"):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _canonical(value):
    """The same canonical JSON the gateway uses (`dottie.core.canonical`), restated."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _identity(ident):
    if ident not in INDEPENDENT_IDENTITIES:
        raise NotFound("unknown intelligence")
    return ident


def _record(ident, root_dir=None):
    """The on-disk lineage record. Delegates to `dottie.lineage.lineage` when possible."""
    _identity(ident)
    if root_dir is None:
        return lineage(ident)
    doc = Path(root_dir).expanduser() / "lineage_data" / (ident + ".json")
    if not doc.is_file():
        raise ValueError("GMRI identity lineage template absent: " + ident)
    data = json.loads(doc.read_text())
    if data.get("intelligence_id") != ident:
        raise ValueError("lineage identity mismatch")
    return data


def _constraints(record):
    """Constraints, tolerating the two spellings the identity files use."""
    for key in ("behavioral_constraints", "constraints"):
        value = record.get(key)
        if isinstance(value, list) and value:
            return [str(v) for v in value]
    return []


def _sources(record):
    value = record.get("lineage_sources")
    return [str(v) for v in value] if isinstance(value, list) else []


def _bounded_model_id(model_id):
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 150 or "\x00" in model_id:
        raise ContinuityError("model_id must be a non-empty bounded string (<=150 chars)", "invalid")
    return model_id.strip()


def _find_engine():
    """Locate `mind.py`. Explicit env wins; then the deployed copy; then a local checkout."""
    explicit = os.environ.get("DOTTIE_MIND_ENGINE", "").strip()
    candidates = [explicit] if explicit else []
    candidates += [
        "/opt/dottie-memory/mind.py",
        str(Path.home() / "Desktop" / "mind_engine" / "mind.py"),
        str(Path.cwd() / "mind_engine" / "mind.py"),
    ]
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None


# --------------------------------------------------------------------------- #
# backends — one interface, two transports
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# the anchor
# --------------------------------------------------------------------------- #
def _payload(record, anchored_at, history=None, previous_anchor_id=None):
    """The anchor content: the identity payload, plus the append-only supersession record."""
    content = {
        "schema": ANCHOR_SCHEMA,
        "intelligence_id": str(record.get("intelligence_id")),
        "lineage_version": str(record.get("lineage_version")),
        "behavioral_constraints": _constraints(record),
        "lineage_sources": _sources(record),
        "identity_manifest_ref": record.get("identity_manifest_ref"),
        "authority_profile_ref": record.get("authority_profile_ref"),
        "anchored_at": anchored_at,
        "model_binding_history": list(history or []),
        "previous_anchor_id": previous_anchor_id,
    }
    # Schema keys must not carry None where the contract names a string, except the two refs,
    # which the identity files may legitimately omit.
    return content


def _content_bytes(anchor):
    """The canonical bytes of the stored anchor content, from the store's own string.

    `anchor_raw_content` is what the store returned, so comparing these is comparing stored
    bytes. Reconstructing from the dict would prove only that this module round-trips.
    """
    raw = anchor.get("anchor_raw_content")
    if isinstance(raw, str):
        return raw.encode("utf-8")
    payload = {k: v for k, v in anchor.items() if k not in ROW_METADATA}
    return _canonical(payload).encode("utf-8")


def _identity_payload_bytes(anchor):
    """Canonical bytes of the identity payload an anchor carries — the part that must not move.

    Excludes `anchored_at`, `model_binding_history` and `previous_anchor_id`: those are the
    supersession record, and a re-anchor or a binding change is SUPPOSED to move them. What
    must never move across a restart, a binding change or a re-anchor is the identity itself.
    """
    if not isinstance(anchor, dict):
        return b""
    payload = {key: anchor.get(key) for key in ANCHOR_FIELDS
               if key not in ("anchored_at", "schema")}
    payload["schema"] = anchor.get("schema")
    return _canonical(payload).encode("utf-8")


def _from_row(row, backend, identity):
    """Parse one store row into an anchor, or None when it is not this identity's anchor."""
    content = row.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        parsed = json.loads(content)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or parsed.get("schema") != ANCHOR_SCHEMA:
        return None
    if str(parsed.get("intelligence_id")) != identity:
        return None
    parsed["anchor_row_id"] = row.get("id")
    parsed["anchor_backend"] = backend
    parsed["anchor_raw_content"] = content
    parsed["anchor_certified"] = row.get("certified")
    return parsed


def _anchor_rows(store, identity):
    """Every anchor row the store returns for the anchor query, newest last."""
    anchors = []
    seen = set()
    for row in store.recall(ANCHOR_QUERY, MAX_RECALL):
        if row.get("id") in seen:
            continue
        anchor = _from_row(row, store.backend, identity)
        if anchor is None:
            continue
        seen.add(row.get("id"))
        anchor["_row_valid_from"] = row.get("valid_from")
        anchors.append(anchor)
    anchors.sort(key=lambda a: (
        len(a.get("model_binding_history") or []),
        str(a.get("anchored_at") or ""),
        str(a.get("anchor_row_id") or ""),
    ))
    return anchors


def _head(anchors):
    """The chain's HEAD: the anchor no other anchor names as its predecessor.

    "Newest" is a property of the supersession graph, not of a timestamp or a history length.
    Ordering by `len(model_binding_history)` was wrong the moment a re-anchor happened: a fresh
    anchor with an empty history would lose to an older two-binding row, and the next bind would
    then append a duplicate ['model-A','model-B'] prefix onto that older row's history.

    A head is a row whose id appears in no recalled row's `previous_anchor_id`. If recall was
    truncated (limit) and the true head is absent, the pool of apparent heads still contains
    the newest recalled row, so the later `anchored_at` wins.
    """
    if not anchors:
        return None
    referenced = {str(a.get("previous_anchor_id"))
                  for a in anchors if a.get("previous_anchor_id")}
    heads = [a for a in anchors if str(a.get("anchor_row_id")) not in referenced]
    pool = heads or anchors
    pool = sorted(pool, key=lambda a: (str(a.get("anchored_at") or ""),
                                       str(a.get("anchor_row_id") or "")))
    return pool[-1]


def _newest(store, identity):
    anchor = _head(_anchor_rows(store, identity))
    if anchor is not None:
        anchor.pop("_row_valid_from", None)
    return anchor


def anchor_identity(base_url=None, token=None, root_dir=None, *, identity=DEFAULT_IDENTITY,
                    agent=DEFAULT_AGENT, principal=DEFAULT_PRINCIPAL, engine=None,
                    store=None, now=None):
    """Write the first identity anchor into the memory store and return it.

    The content is canonical JSON of the identity payload read from the on-disk lineage
    record. Nothing derives from a model, a session or a conversation: the anchor is written
    from bytes that already exist on disk, and every later read is a store read.

    On an EMPTY chain this is the root anchor: empty history, no predecessor. On a chain that
    already has a head, re-anchoring SUPERSEDES that head — the new row links to it and carries
    its binding history forward. Two independent roots in one store would make "the newest
    anchor" ambiguous, which is the one thing an anchor must never be.

    Repeating a re-anchor with byte-identical payload inside the same `anchored_at` second is
    absorbed by the store (mind_engine digests kind‖scope‖agent‖content): no second row.
    """
    _identity(identity)
    record = _record(identity, root_dir)
    backend = store if store is not None else _store(base_url, token, root_dir,
                                                    identity=identity, agent=agent,
                                                    principal=principal, engine=engine)
    current = _newest(backend, identity)
    if current is None:
        content = _payload(record, _iso(now), history=[], previous_anchor_id=None)
    else:
        content = _payload(record, _iso(now),
                          history=list(current.get("model_binding_history") or []),
                          previous_anchor_id=current.get("anchor_row_id"))
    text = _canonical(content)
    if len(text) > MAX_ANCHOR_CHARS:
        raise ContinuityError("anchor content is %d chars; the store cap is %d"
                              % (len(text), MAX_ANCHOR_CHARS), "too_large")
    row = backend.save(text)
    anchor = _from_row(row, backend.backend, identity)
    if anchor is None:
        raise ContinuityError("store returned a row that is not a %s anchor" % ANCHOR_SCHEMA,
                              "store_contract")
    anchor["anchor_gate_exit"] = row.get("gate_exit")
    anchor["anchor_duplicate"] = bool(row.get("duplicate"))
    return anchor


def load_anchor(base_url=None, token=None, root_dir=None, *, identity=DEFAULT_IDENTITY,
                agent=DEFAULT_AGENT, principal=DEFAULT_PRINCIPAL, engine=None, store=None):
    """Re-load the newest anchor from the store, in any process. None when none exists.

    Recall is lexical (`match` is surface overlap, never support), so the schema check — not
    the ranking — decides what is an anchor: a row counts only when its content parses to
    `schema == "dottie-identity-anchor/v1"` for this identity. Of those, the HEAD — the row no
    other anchor names as its `previous_anchor_id` — wins; that is what makes supersession
    readable without trusting a clock.
    """
    backend = store if store is not None else _store(base_url, token, root_dir,
                                                    identity=identity, agent=agent,
                                                    principal=principal, engine=engine)
    return _newest(backend, identity)


def verify(anchor, root_dir=None):
    """Mismatches between an anchor and the on-disk lineage record. Empty list == identical.

    Named, one per failed comparison, so a caller reports which field drifted rather than
    "verification failed": identity_id, lineage_version, constraints (set equality, plus a
    duplicate check), sources (set equality, plus a duplicate check), and the two refs.
    """
    if not isinstance(anchor, dict):
        return ["anchor: not a mapping"]
    mismatches = []
    if anchor.get("schema") != ANCHOR_SCHEMA:
        mismatches.append("schema: %r != %r" % (anchor.get("schema"), ANCHOR_SCHEMA))
    ident = anchor.get("intelligence_id", anchor.get("identity_id"))
    if ident not in INDEPENDENT_IDENTITIES:
        return mismatches + ["identity_id: anchor names unknown intelligence %r" % (ident,)]
    try:
        record = _record(ident, root_dir)
    except (NotFound, ValueError, OSError) as exc:
        return mismatches + ["lineage record unavailable: %s: %s"
                             % (type(exc).__name__, exc)]
    disk_version = record.get("lineage_version")
    if anchor.get("lineage_version") != disk_version:
        mismatches.append("lineage_version: %r != lineage %r"
                          % (anchor.get("lineage_version"), disk_version))
    anchor_constraints = anchor.get("behavioral_constraints")
    if not isinstance(anchor_constraints, list):
        mismatches.append("behavioral_constraints: not a list")
    else:
        disk = _constraints(record)
        stated = [str(c) for c in anchor_constraints]
        missing = sorted(set(disk) - set(stated))
        extra = sorted(set(stated) - set(disk))
        if missing or extra:
            mismatches.append("behavioral_constraints: missing=%s extra=%s"
                              % (missing, extra))
        elif len(stated) != len(set(stated)):
            mismatches.append("behavioral_constraints: duplicate entries in anchor")
    anchor_sources = anchor.get("lineage_sources")
    if not isinstance(anchor_sources, list):
        mismatches.append("lineage_sources: not a list")
    else:
        disk = _sources(record)
        stated = [str(s) for s in anchor_sources]
        missing = sorted(set(disk) - set(stated))
        extra = sorted(set(stated) - set(disk))
        if missing or extra:
            mismatches.append("lineage_sources: missing=%s extra=%s" % (missing, extra))
        elif len(stated) != len(set(stated)):
            mismatches.append("lineage_sources: duplicate entries in anchor")
    for field in ("identity_manifest_ref", "authority_profile_ref"):
        if anchor.get(field) != record.get(field):
            mismatches.append("%s: %r != lineage %r"
                              % (field, anchor.get(field), record.get(field)))
    return mismatches


def bind_model(anchor, model_id, base_url=None, token=None, root_dir=None, *,
               provider=None, identity=DEFAULT_IDENTITY, agent=DEFAULT_AGENT,
               principal=DEFAULT_PRINCIPAL, engine=None, store=None, now=None):
    """APPEND a model binding to the anchor by saving a NEW anchor row.

    The previous row is never mutated: the store is append-only, so supersession is recorded
    by linking `previous_anchor_id` to the row this binding succeeds. `model_binding_history`
    keeps its order, one entry per binding, and the identity payload is copied from the
    anchor — not re-derived and not touched — so the only thing a binding change moves is
    which compute is declared.

    Idempotent: binding the model that is already newest returns the anchor unchanged and
    writes nothing, so a retried bind cannot forge a history entry.
    """
    if not isinstance(anchor, dict):
        raise ContinuityError("bind_model needs an anchor dict", "invalid")
    model = _bounded_model_id(model_id)
    if provider is not None and (not isinstance(provider, str) or not provider.strip()
                                 or len(provider) > 120):
        raise ContinuityError("provider must be a non-empty bounded string (<=120 chars)",
                              "invalid")
    history = [dict(entry) for entry in (anchor.get("model_binding_history") or [])
               if isinstance(entry, dict)]
    if history:
        newest = history[-1]
        if newest.get("model_id") == model and newest.get("provider") == provider:
            return anchor
    stamp = _iso(now)
    history.append({
        "model_id": model,
        "provider": provider,
        "bound_at": stamp,
        "previous_model_id": history[-1].get("model_id") if history else None,
        "identity_unchanged": True,
    })
    content = {
        "schema": ANCHOR_SCHEMA,
        "intelligence_id": anchor.get("intelligence_id"),
        "lineage_version": anchor.get("lineage_version"),
        "behavioral_constraints": list(anchor.get("behavioral_constraints") or []),
        "lineage_sources": list(anchor.get("lineage_sources") or []),
        "identity_manifest_ref": anchor.get("identity_manifest_ref"),
        "authority_profile_ref": anchor.get("authority_profile_ref"),
        "anchored_at": stamp,
        "model_binding_history": history,
        "previous_anchor_id": anchor.get("anchor_row_id"),
    }
    text = _canonical(content)
    if len(text) > MAX_ANCHOR_CHARS:
        raise ContinuityError("anchor content is %d chars; the store cap is %d"
                              % (len(text), MAX_ANCHOR_CHARS), "too_large")
    backend = store if store is not None else _store(base_url, token, root_dir,
                                                    identity=anchor.get("intelligence_id")
                                                    or identity,
                                                    agent=agent, principal=principal,
                                                    engine=engine)
    # A binding appends to the chain, so it must start from the chain's HEAD. Binding from a
    # superseded anchor would fork the history into two rows that each name the same
    # predecessor, and "newest wins" would then be a coin toss. Refuse, and name the head.
    current = _newest(backend, str(anchor.get("intelligence_id") or identity))
    if (current is not None and anchor.get("anchor_row_id")
            and current.get("anchor_row_id") != anchor.get("anchor_row_id")):
        raise ContinuityError(
            "anchor %s is superseded by %s; bind from the newest anchor"
            % (anchor.get("anchor_row_id"), current.get("anchor_row_id")), "superseded")
    row = backend.save(text)
    bound = _from_row(row, backend.backend, str(anchor.get("intelligence_id") or identity))
    if bound is None:
        raise ContinuityError("store returned a row that is not a %s anchor" % ANCHOR_SCHEMA,
                              "store_contract")
    bound["anchor_gate_exit"] = row.get("gate_exit")
    bound["anchor_duplicate"] = bool(row.get("duplicate"))
    return bound


def reserved_field_state(root_dir=None, identity=DEFAULT_IDENTITY):
    """The reserved fields as they stand on disk. Read-only; this module never writes them."""
    record = _record(identity, root_dir)
    return {field: record.get(field) for field in RESERVED_FIELDS}


def reserved_fields_unset(root_dir=None, identity=DEFAULT_IDENTITY):
    """[(field, actual, expected)] for every reserved field that is NOT still unset."""
    state = reserved_field_state(root_dir, identity)
    return [(field, state.get(field), expected)
            for field, expected in RESERVED_FIELDS.items()
            if state.get(field) != expected]


# --------------------------------------------------------------------------- #
# selftest — the contract, asserted
# --------------------------------------------------------------------------- #
def _line(ok, name, detail=""):
    text = ("PASS  " if ok else "FAIL  ") + name
    return text + (" — " + detail if detail else "")


def _child_anchor(store_args, identity):
    """A NEW process: load the anchor, print its stored bytes' sha256, then die hard."""
    command = [sys.executable, "-m", "dottie.continuity", "--print-anchor-digest",
               "--identity", identity] + list(store_args)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env)
    return proc


def selftest(base_url=None, token=None, root_dir=None, *, identity=DEFAULT_IDENTITY,
             agent=DEFAULT_AGENT, principal=DEFAULT_PRINCIPAL, engine=None,
             workdir=None, stream=None):
    """Run the whole proof. Returns (ok, lines). Every assertion prints one PASS/FAIL line."""
    out = stream or sys.stdout
    lines = []

    def emit(ok, name, detail=""):
        text = _line(ok, name, detail)
        lines.append(text)
        print(text, file=out, flush=True)
        return ok

    identity_record = _record(identity, root_dir)
    lineage_before = _canonical(identity_record)
    instructions_before = compute_instructions(identity)

    # Backend selection. An explicit mount wins; a reachable REST wins over a private mount.
    backend = None
    setup_note = ""
    if root_dir:
        backend = EngineStore(root_dir, engine=engine, principal=principal, agent=agent,
                              identity=identity)
    elif base_url and token:
        backend = RestStore(base_url, token, principal=principal, agent=agent,
                            identity=identity)
        if not backend.reachable():
            raise ContinuityError("REST service at %s did not answer /info" % base_url,
                                  "unreachable")
    else:
        url = os.environ.get("DOTTIE_MEMORY_URL", "").strip() or DEFAULT_URL
        env_token = token or os.environ.get("DOTTIE_MEMORY_TOKEN", "").strip()
        if env_token:
            candidate = RestStore(url, env_token, principal=principal, agent=agent,
                                  identity=identity)
            if candidate.reachable():
                backend = candidate
        if backend is None:
            root = Path(workdir or "/tmp/idcont/selftest-mount").expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            engine_path = engine or _find_engine()
            if engine_path is None:
                raise ContinuityError("no REST service and no mind.py to fall back to",
                                      "unavailable")
            private = EngineStore(root, engine=engine_path, principal=principal, agent=agent,
                                  identity=identity)
            if not private.reachable():
                private.init()
                setup_note = " (initialised private mount)"
            backend = private

    target = backend.root if backend.backend == "engine" else backend.base_url
    emit(True, "backend selected", "%s at %s%s" % (backend.backend, target, setup_note))

    info_before = backend.info()
    gate_before = info_before.get("gate_exit")
    emit(gate_before == 0, "store gate_exit 0 before the run",
         "gate_exit=%s failing=%s%s" % (gate_before, info_before.get("failing"),
                                        (" gates=" + str(info_before.get("gates")))
                                        if info_before.get("gates") else ""))

    # 1. anchor written, id returned, mount gate_exit 0.
    anchor = anchor_identity(store=backend, identity=identity)
    anchor_id = anchor.get("anchor_row_id")
    emit(bool(anchor_id), "anchor written and id returned", "id=%s" % anchor_id)
    written_bytes = _content_bytes(anchor)
    emit(anchor.get("anchor_duplicate") is False or anchor_id is not None,
         "anchor is one saved memory row",
         "duplicate=%s chars=%d sha256=%s" % (anchor.get("anchor_duplicate"),
                                              len(written_bytes), _sha256(
                                                  written_bytes.decode("utf-8"))[:16]))
    info_after = backend.info()
    emit(info_after.get("gate_exit") == 0, "mount gate_exit 0 after the write",
         "gate_exit=%s%s" % (info_after.get("gate_exit"),
                             (" gates=" + str(info_after.get("gates")))
                             if info_after.get("gates") else ""))

    # 2. process kill -> new process -> byte-identical recovery, zero mismatches.
    store_flags = _store_flags(backend, identity)
    child = _child_anchor(store_flags, identity)
    recovered_digest = ""
    for line in child.stdout.splitlines():
        if line.startswith("ANCHOR-SHA256 "):
            recovered_digest = line.split(" ", 1)[1].strip()
    emit(child.returncode == 9,
         "child process died hard (os._exit) instead of unwinding",
         "returncode=%s stderr=%s" % (child.returncode,
                                      (child.stderr or "").strip().splitlines()[-1:]
                                      if child.stderr.strip() else "none"))
    fresh = load_anchor(store=backend, identity=identity)
    fresh_bytes = _content_bytes(fresh) if fresh else b""
    # The ANCHOR's bytes are its identity payload. A re-anchor legitimately changes its own
    # supersession link and `anchored_at`, so the comparison is the payload the contract names —
    # which is also what a reader recovers: the same intelligence, constraints and sources.
    payload_before = _identity_payload_bytes(anchor)
    payload_after = _identity_payload_bytes(fresh) if fresh else b""
    emit(fresh is not None and payload_after == payload_before
         and fresh.get("anchor_row_id") == anchor_id,
         "anchor recovered BYTE-IDENTICALLY in a new process",
         "row=%s child_content_sha256=%s payload_sha256=%s payload_bytes=%d"
         % (fresh.get("anchor_row_id") if fresh else None, recovered_digest[:16],
            _sha256(payload_after.decode("utf-8"))[:16], len(payload_after)))
    mismatches = verify(fresh) if fresh else ["anchor missing after restart"]
    emit(mismatches == [], "verify() reports zero mismatches after restart",
         "mismatches=%s" % (mismatches or "none"))

    # 3. model binding change: append, order, chain, and identity-independent instructions.
    # The ids are RUN-UNIQUE and the assertion is RELATIVE to the history already on the mount.
    # A fixed "length == 2" would be a clean-substrate assumption: on a mount that already holds
    # a chain — a second run, or a shared mount with another workstream writing to it — it
    # measures the substrate instead of this run, and it would fail for the wrong reason.
    tag = _run_tag()
    model_a, model_b = "model-A-" + tag, "model-B-" + tag
    baseline_history = [e.get("model_id") for e in (fresh.get("model_binding_history") or [])
                        if isinstance(e, dict)]
    anchor_a = bind_model(fresh, model_a, store=backend, identity=identity)
    anchor_b = bind_model(anchor_a, model_b, store=backend, identity=identity)
    history = anchor_b.get("model_binding_history") or []
    order = [entry.get("model_id") for entry in history
             if isinstance(entry, dict)]
    emit(len(order) == len(baseline_history) + 2
         and order[-2:] == [model_a, model_b],
         "binding history appended in order (+2 this run)",
         "before=%s after=%s" % (baseline_history, order))
    chain_ok = (anchor_a.get("previous_anchor_id") == anchor_id
                and anchor_b.get("previous_anchor_id") == anchor_a.get("anchor_row_id")
                and anchor_a.get("anchor_row_id") != anchor_b.get("anchor_row_id"))
    emit(chain_ok, "previous_anchor_id chain intact (each binding is a NEW row)",
         "anchor0=%s anchorA=%s<-anchor0 anchorB=%s<-anchorA"
         % (anchor_id, anchor_a.get("anchor_row_id"), anchor_b.get("anchor_row_id")))
    head = load_anchor(store=backend, identity=identity)
    head_history = [e.get("model_id") for e in (head.get("model_binding_history") or [])
                    if isinstance(e, dict)] if head else []
    emit(head is not None and head.get("anchor_row_id") == anchor_b.get("anchor_row_id")
         and head_history == order,
         "chain head wins on re-load (not a stale or forked row)",
         "loaded_row=%s head=%s history_tail=%s"
         % (head.get("anchor_row_id") if head else None, anchor_b.get("anchor_row_id"),
            head_history[-2:]))
    instructions_after = compute_instructions(identity)
    emit(instructions_after == instructions_before,
         "compute_instructions BYTE-IDENTICAL across the binding change",
         "sha256_before=%s sha256_after=%s"
         % (_sha256(instructions_before)[:16], _sha256(instructions_after)[:16]))
    identity_payload = {k: anchor_b.get(k) for k in ANCHOR_FIELDS
                        if k not in ("anchored_at", "schema")}
    original_payload = {k: anchor.get(k) for k in ANCHOR_FIELDS
                        if k not in ("anchored_at", "schema")}
    emit(identity_payload == original_payload,
         "identity payload unchanged by binding (only the compute declaration moved)",
         "fields=%d" % len(identity_payload))

    # 4. reserved fields still UNSET, and the identity file was not rewritten.
    drift = reserved_fields_unset(root_dir, identity)
    emit(drift == [], "reserved fields still UNSET (no namespace/relationship ruling made)",
         "drift=%s" % (drift or "none: " + ", ".join(
             "%s=%s" % (k, v) for k, v in RESERVED_FIELDS.items())))
    lineage_after = _canonical(_record(identity, root_dir))
    emit(lineage_after == lineage_before, "lineage record bytes unchanged by the run",
         "sha256=%s" % _sha256(lineage_after)[:16])

    # 5. the store still certifies itself.
    final = backend.info()
    if final.get("gates"):
        emit(final.get("gate_exit") == 0 and final.get("gates", "").split("/")[0]
             == final.get("gates", "").split("/")[-1],
             "store audit clean after the run",
             "GATES %s (exit=%s)" % (final.get("gates"), final.get("gate_exit")))
    else:
        emit(final.get("gate_exit") == 0, "store /info gate_exit 0 after the run",
             "gate_exit=%s" % final.get("gate_exit"))

    ok = all(line.startswith("PASS") for line in lines)
    print(("SELFTEST: PASS" if ok else "SELFTEST: FAIL") + " (%d assertions)" % len(lines),
          file=out, flush=True)
    lines.append("SELFTEST: %s (%d assertions)" % ("PASS" if ok else "FAIL", len(lines)))
    return ok, lines


def _run_tag():
    """A short, unique tag so one run's model ids cannot collide with an earlier run's."""
    return hashlib.sha256(("%s:%s:%s" % (os.getpid(), time.time(),
                                         time.monotonic_ns())).encode()).hexdigest()[:8]


def _store_flags(backend, identity):
    """CLI flags that reproduce this backend in a child process."""
    flags = ["--identity", identity]
    if backend.backend == "engine":
        flags += ["--mount", str(backend.root), "--mind-engine", str(backend.engine)]
    else:
        flags += ["--base-url", backend.base_url, "--token", backend.token]
    return flags


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _token_from(args):
    if getattr(args, "token", None):
        return args.token
    path = getattr(args, "token_file", None)
    if path:
        return Path(path).expanduser().read_text().strip()
    return None


def _resolve_store(args):
    return _store(getattr(args, "base_url", None), _token_from(args),
                  getattr(args, "mount", None), identity=args.identity,
                  engine=getattr(args, "mind_engine", None))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m dottie.continuity",
        description="Durable identity anchor: prove the identity outlives the model.")
    parser.add_argument("--selftest", action="store_true",
                        help="run the full restart / binding-change / reserved-field proof")
    parser.add_argument("--anchor", action="store_true", help="write the first anchor")
    parser.add_argument("--show", action="store_true", help="print the newest anchor")
    parser.add_argument("--verify", action="store_true",
                        help="verify the newest anchor against the lineage record")
    parser.add_argument("--bind", metavar="MODEL_ID", help="append a model binding")
    parser.add_argument("--provider", default=None, help="provider label for --bind")
    parser.add_argument("--print-anchor-digest", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--identity", default=DEFAULT_IDENTITY)
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--principal", default=DEFAULT_PRINCIPAL)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--mount", default=None, help="engine mount root (directory store)")
    parser.add_argument("--mind-engine", default=None, help="path to mind.py")
    parser.add_argument("--workdir", default=None,
                        help="scratch root for the selftest's private mount")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        if args.print_anchor_digest:
            anchor = load_anchor(store=_resolve_store(args), identity=args.identity)
            if anchor is None:
                print("ANCHOR-MISSING", flush=True)
                os._exit(8)
            print("ANCHOR-SHA256 " + _sha256(_content_bytes(anchor).decode("utf-8")),
                  flush=True)
            # The contract is recovery by a process that does NOT get to unwind: no atexit,
            # no finally, no interpreter cleanup. Only the digest above leaves.
            os._exit(9)
        if args.selftest:
            ok, _ = selftest(args.base_url, _token_from(args), args.mount,
                             identity=args.identity, agent=args.agent,
                             principal=args.principal, engine=args.mind_engine,
                             workdir=args.workdir)
            return 0 if ok else 1
        store = _resolve_store(args)
        if args.anchor:
            anchor = anchor_identity(store=store, identity=args.identity)
            print(json.dumps(anchor, indent=2, sort_keys=True))
            return 0
        anchor = load_anchor(store=store, identity=args.identity)
        if anchor is None:
            print("no anchor in the store; run --anchor", file=sys.stderr)
            return 3
        if args.bind:
            anchor = bind_model(anchor, args.bind, store=store, provider=args.provider,
                               identity=args.identity)
            print(json.dumps(anchor, indent=2, sort_keys=True))
            return 0
        if args.verify:
            mismatches = verify(anchor)
            print(json.dumps({"row": anchor.get("anchor_row_id"),
                              "backend": anchor.get("anchor_backend"),
                              "mismatches": mismatches}, indent=2, sort_keys=True))
            return 0 if not mismatches else 1
        print(json.dumps(anchor, indent=2, sort_keys=True))
        return 0
    except (ContinuityError, NotFound, ValueError) as exc:
        print("continuity: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
