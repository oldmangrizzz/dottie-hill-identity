"""One agent's REST client for the live dottie-memory service.

The server authenticates a TUPLE: bearer token, principal (`X-Cpki-User-Id`), and the
per-writer identity (`X-Dottie-Agent`). This module presents that tuple and reports what
the server answered. It never decides isolation locally — a 403 here is the server's
verdict about a row, not a client-side guess, and the raw status stays available so a
caller can prove which one it got.

Wire facts this client depends on (measured against /opt/dottie-memory, 2026-11-03):

  * `POST /api/memories`          201 new row / 200 absorbed duplicate; row carries `agent`
  * `POST /api/memories/recall`   200 `{memories:[...]}`; own + principal-level rows only
  * `GET  /api/memories`          200 `{memories:[...]}`; same visibility rule
  * `PATCH|DELETE /api/memories/<id>`  200 for the row's own agent, 403 otherwise
  * missing `X-Dottie-Agent`      400 with `MEMORY_AGENT_SCOPING` in the message
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8471"
PRINCIPAL = "grizz"
AGENT_HEADER = "X-Dottie-Agent"
MAX_BODY = 2_000_000
TIMEOUT = 120.0


class MemoryFailure(Exception):
    """Base class for every failure this client raises."""


class MemoryUnreachable(MemoryFailure):
    """The service could not be talked to at all (refused, timeout, non-JSON answer)."""


class MemoryHTTPError(MemoryFailure):
    """A non-2xx answer that is not one of the two named cases below."""

    def __init__(self, status: int, payload: object, method: str, path: str) -> None:
        self.status = status
        self.payload = payload
        self.method = method
        self.path = path
        super().__init__("%s %s -> %d: %s" % (method, path, status, _error_text(payload)))

    def excerpt(self, limit: int = 200) -> str:
        return _excerpt(self.payload, limit)


class MemoryDenied(MemoryHTTPError):
    """403 — the row belongs to another agent, or is principal-level (no agent stamp)."""


class MemoryUnscoped(MemoryHTTPError):
    """400 — `X-Dottie-Agent` was absent while `MEMORY_AGENT_SCOPING` is on."""


def _error_text(payload: object) -> str:
    if isinstance(payload, dict):
        return str(payload.get("error") or payload)
    return str(payload)


def _excerpt(payload: object, limit: int = 200) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(payload)
    return text if len(text) <= limit else text[:limit] + "…"


def _is_missing_agent_header(status: int, payload: object) -> bool:
    """A 400 is only *this* 400 when the server names the header it wanted."""
    return status == 400 and AGENT_HEADER in _error_text(payload)


class Response:
    """A raw answer: status, parsed body, and the body as text (for excerpts)."""

    __slots__ = ("status", "payload", "text")

    def __init__(self, status: int, payload: object, text: str) -> None:
        self.status = status
        self.payload = payload
        self.text = text

    @property
    def memories(self) -> list:
        if isinstance(self.payload, dict) and isinstance(self.payload.get("memories"), list):
            return self.payload["memories"]
        return []

    @property
    def id(self) -> str:
        return str(self.payload.get("id") or "") if isinstance(self.payload, dict) else ""

    def excerpt(self, limit: int = 200) -> str:
        return _excerpt(self.payload, limit)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<Response %d %s>" % (self.status, self.excerpt(80))


class MemoryClient:
    """A memory client bound to (base_url, token, agent_id). One agent per instance."""

    def __init__(self, base_url: str, token: str, agent_id: str,
                 principal: str = PRINCIPAL, timeout: float = TIMEOUT) -> None:
        if not agent_id or not isinstance(agent_id, str):
            raise ValueError("agent_id is required: scoping is enforced on every route")
        if not token:
            raise ValueError("token is required: /api/memories is behind bearer auth")
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.token = token
        self.agent_id = agent_id.strip()
        self.principal = principal
        self.timeout = timeout
        # No proxy: a loopback service reached through a workstation tunnel must not be
        # routed by an ambient HTTP_PROXY.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ---------------------------------------------------------------- transport
    def headers(self) -> dict:
        """The exact identity tuple this client sends. Exposed so tests can assert it."""
        return {
            "Authorization": "Bearer " + self.token,
            "X-Cpki-User-Id": self.principal,
            AGENT_HEADER: self.agent_id,
            "Content-Type": "application/json",
        }

    def raw(self, method: str, path: str, payload: dict | None = None,
            omit_agent: bool = False) -> Response:
        """One request; HTTP errors are DATA. Only transport failure raises here."""
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = self.headers()
        if omit_agent:
            headers.pop(AGENT_HEADER, None)
        request = urllib.request.Request(self.base_url + path, data=body,
                                         method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                text = response.read(MAX_BODY + 1).decode("utf-8", "replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            text = exc.read(MAX_BODY + 1).decode("utf-8", "replace")
            status = exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise MemoryUnreachable("%s unreachable at %s: %s"
                                    % ("dottie-memory", self.base_url, exc)) from exc
        try:
            parsed = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            raise MemoryUnreachable("%s %s answered non-JSON (status %d): %s"
                                    % (method, path, status, text[:120])) from None
        return Response(status, parsed, text)

    # ---------------------------------------------------------------- verdicts
    @staticmethod
    def _checked(response: Response, method: str, path: str) -> Response:
        """2xx passes; 403 and the missing-header 400 get their named exceptions."""
        if 200 <= response.status < 300:
            return response
        if response.status == 403:
            raise MemoryDenied(response.status, response.payload, method, path)
        if _is_missing_agent_header(response.status, response.payload):
            raise MemoryUnscoped(response.status, response.payload, method, path)
        raise MemoryHTTPError(response.status, response.payload, method, path)

    def call(self, method: str, path: str, payload: dict | None = None,
             omit_agent: bool = False) -> Response:
        """Raw request + verdict: raises MemoryDenied / MemoryUnscoped / MemoryHTTPError.

        `omit_agent=True` is the negative case the server must refuse with 400 — the only
        way to *make* an unscoped request through a client that always sends the header.
        """
        return self._checked(self.raw(method, path, payload, omit_agent=omit_agent),
                             method, path)

    # ---------------------------------------------------------------- memory verbs
    def save(self, content: str, kind: str = "topical", scope: str = "user") -> Response:
        return self.call("POST", "/api/memories",
                         {"content": content, "kind": kind, "scope": scope})

    def recall(self, query: str, limit: int = 5, scope: str | None = None,
               include_invalidated: bool = False) -> Response:
        body: dict = {"query": query, "limit": limit,
                      "includeInvalidated": include_invalidated}
        if scope is not None:
            body["scope"] = scope
        return self.call("POST", "/api/memories/recall", body)

    def list(self, include_invalidated: bool = False) -> Response:
        path = ("/api/memories?includeInvalidated=true" if include_invalidated
                else "/api/memories?includeInvalidated=false")
        return self.call("GET", path)

    def patch(self, memory_id: str, content: str, reason: str = "") -> Response:
        path = "/api/memories/" + urllib.parse.quote(memory_id, safe="")
        return self.call("PATCH", path, {"content": content, "reason": reason})

    def delete(self, memory_id: str, reason: str = "") -> Response:
        path = "/api/memories/" + urllib.parse.quote(memory_id, safe="")
        query = "?reason=" + urllib.parse.quote(reason, safe="") if reason else ""
        return self.call("DELETE", path + query)

    def info(self) -> Response:
        """`/info` is unauthenticated; no identity headers are sent or needed."""
        return self.raw("GET", "/info")

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<MemoryClient %s @ %s>" % (self.agent_id, self.base_url)
