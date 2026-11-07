"""The local cognitive pair: LFM 3 (prose) + LAYA (typed verdicts).

Dottie's System 1 and System 2 are two different instruments and this module keeps them
distinct instead of pretending one is the other:

  * LAYA answers a TYPED question about a decision -- a choice, a probability distribution,
    a score -- over a fixed state. Its wire is `POST /judge` on the sidecar that runs beside
    Dottie on the Proxmox host (`dottie-laya.service`, loopback port 8473). Its answers are
    calibrated only as far as the checkpoint says they are; the checkpoint itself warns when
    its temperatures are off, and this module records that the verdict came from the model
    (input_tokens > 0) rather than from the arithmetic fast path (input_tokens == 0, exact
    1.0/0.0), because those two 200s mean very different things.

  * LFM 3 answers in PROSE through the Ollama chat endpoint. The model server is not inside
    Dottie's container: it is the GPU container (`127.0.0.1:11434`), and the slot table
    (`dottie/model_slots.toml`) is the one place its tag and address are named.

`think()` asks both, returns both, and never conflates them. If the cloud tier is requested
and no credential resolves, it says so in `evidence` and answers locally -- a degraded answer
that announces itself, never a silent one.

No new dependency: stdlib `urllib` only, like the rest of this codebase.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_LAYER = "english"

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# --------------------------------------------------------------------------- transports

_THINK_BLOCK = re.compile(r"<think\b[^>]*>(.*?)</think\s*>", re.S | re.I)


def strip_thinking(text: str) -> str:
    """Remove inline reasoning blocks a model emits in its CONTENT.

    Some runtimes honour `enable_thinking`; the llama.cpp build serving tier 1 writes the
    reasoning straight into `content` between <think> tags instead. A caller that wants the
    answer, not the transcript, must strip them -- and must NOT silently return an empty
    string when the model thought and never concluded, which the caller detects as a failure.
    """
    if not isinstance(text, str):
        return ""
    return _THINK_BLOCK.sub("", text).strip()


def _post_json(url: str, payload: dict, timeout: float,
               headers: dict | None = None) -> tuple[int | None, Any, str]:
    """(status, parsed_json, error). Transport failure is DATA, not an exception.

    `headers` carries the credential for authenticated slots. It defaults to none so an
    unauthenticated local call is unchanged; a cloud call MUST pass a bearer token or the
    endpoint answers 401 and this function reports that as data rather than success.
    """
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    request = urllib.request.Request(url, data=body, method="POST", headers=hdrs)
    try:
        with _opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(raw) if raw.strip() else {}), ""
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {"error": raw[:300]}
        return exc.code, parsed, "HTTP %d" % exc.code
    except (urllib.error.URLError, OSError) as exc:
        return None, {}, "%s: %s" % (type(exc).__name__, exc)


def _laya_question(spec: dict) -> dict:
    """One caller question -> the sidecar's schema (`dottie-laya/server/core.py:148-175`).

    The sidecar coerces every question to one of four shapes and REQUIRES the fields that make
    it answerable:

        choice/score -> {"type": ..., "instructions": <text>, "criteria": {...}|[...]}
        bool/noul    -> {"type": "noul", "instructions": <text>}

    A `choice` sent with a bare `options` list is an HTTP 500 on the sidecar — measured — so this
    function builds the shape from whatever the caller supplied, and refuses (rather than
    sending something the sidecar will reject) when a choice has no rubric: a judgement with no
    criteria is a coin flip wearing a verdict's clothes.
    """
    kind = str(spec.get("type") or "choice")
    instructions = str(spec.get("instructions") or spec.get("question") or "").strip()
    if kind in ("bool", "noul"):
        entry = {"type": "noul"}
        if instructions:
            entry["instructions"] = instructions
        if isinstance(spec.get("criteria"), dict):
            entry["crit"] = spec["criteria"]
        return entry
    if kind == "score":
        criteria = spec.get("criteria")
        if criteria is None:
            raise ValueError("a score question needs `criteria` (a list of rubric lines)")
        entry = {"type": "score", "instructions": instructions, "criteria": criteria}
        return entry
    criteria = spec.get("criteria")
    if isinstance(criteria, dict) and criteria:
        entry = {"type": "choice", "instructions": instructions, "criteria": criteria}
        return entry
    # A convenience path for callers that pass `options`: synthesise a rubric per option so the
    # sidecar has something to judge against. It is deliberately explicit — the rubric says the
    # option IS the answer, which is only honest when the options are mutually exclusive.
    options = spec.get("options")
    if isinstance(options, list) and options:
        rubric = {str(o): "this option is the answer" for o in options}
        return {"type": "choice", "instructions": instructions, "criteria": rubric}
    raise ValueError("a choice question needs `criteria` (option -> rubric) or `options`")


def laya_judge(state: dict, questions: dict, *, base_url: str, timeout: float = 60.0,
               layer: str = DEFAULT_LAYER) -> dict:
    """One typed verdict from LAYA. Returns {ok, verdict, latency_ms, model, error}.

    `questions` is the CALLER's shape — {id: {"type": ..., "instructions": ..., "criteria": ...}}
    — and `_laya_question` translates each one into the sidecar's own schema. Sending the
    caller's shape unchanged 500s on any `choice` (the sidecar demands `criteria`), which is why
    this translation lives here and not in every caller.
    """
    try:
        wire = {qid: _laya_question(spec or {}) for qid, spec in (questions or {}).items()}
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "status": None, "verdict": None}
    status, payload, err = _post_json(base_url.rstrip("/") + "/judge",
                                      {"state": state, "questions": wire,
                                       "layer": layer}, timeout)
    if status != 200:
        return {"ok": False, "error": err or payload.get("detail") or payload,
                "status": status, "verdict": None}
    answers = payload.get("answers") or {}
    usage = payload.get("usage") or {}
    first = next(iter(answers.values()), {}) if isinstance(answers, dict) else {}
    return {
        "ok": True,
        "verdict": answers,
        "model_answer": int(usage.get("input_tokens") or 0) > 0,
        "model": payload.get("model"),
        "latency_ms": payload.get("latency_ms"),
        "confidence": first.get("answer_confidence") if isinstance(first, dict) else None,
        "error": None,
    }


def lfm_chat(prompt: str, *, base_url: str, model: str, timeout: float = 300.0,
             system: str | None = None, max_tokens: int = 1024,
             disable_thinking: bool = True, api_key: str | None = None) -> dict:
    """One prose answer from the local model. Returns {ok, answer, model, usage, latency_ms, error}."""
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    started = time.monotonic()
    body = {"model": model, "messages": messages, "stream": False,
            "max_tokens": int(max_tokens)}
    if disable_thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    auth = {"Authorization": "Bearer " + api_key} if api_key else None
    status, payload, err = _post_json(base_url.rstrip("/") + "/v1/chat/completions",
                                      body, timeout, headers=auth)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if status != 200:
        return {"ok": False, "error": err or payload.get("error") or payload,
                "status": status, "answer": None, "latency_ms": elapsed_ms}
    choices = payload.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    answer = strip_thinking(message.get("content") or "")
    if not answer and message.get("reasoning_content"):
        return {"ok": False,
                "error": ("model spent the token budget on reasoning and returned no answer "
                          "(finish_reason=%s); raise max_tokens or disable thinking"
                          % ((choices[0] or {}).get("finish_reason"))),
                "answer": None, "model": payload.get("model"),
                "usage": payload.get("usage"), "latency_ms": elapsed_ms}
    return {
        "ok": True,
        "answer": answer,
        "model": payload.get("model"),
        "usage": payload.get("usage"),
        "latency_ms": elapsed_ms,
        "error": None,
    }


# --------------------------------------------------------------------------- the pair
def think(text: str, *, slots: dict, state: dict | None = None,
          questions: dict | None = None, use_cloud: bool = False,
          system: str | None = None) -> dict:
    """Ask LAYA for a verdict and LFM 3 for prose. Both answers are returned, labelled.

    `slots` is the resolved slot table from `dottie.models` (slot -> binding-like dict with
    provider/model/base_url). Nothing here decides what a slot IS; it only asks the slot the
    registry already resolved.
    """
    s1 = slots["dottie.system1"]
    s2 = slots["dottie.system2"]

    verdict = None
    if questions:
        verdict = laya_judge(state or {}, questions,
                             base_url=s2["base_url"], layer=s2.get("model") or DEFAULT_LAYER)

    answer = lfm_chat(text, base_url=s1["base_url"], model=s1["model"],
                      system=system)

    evidence: dict = {
        "tier_used": "local",
        "local_model": s1["model"],
        "local_endpoint": s1["base_url"],
        "laya_endpoint": s2["base_url"],
        "verdict_from_model": bool(verdict and verdict.get("model_answer")) if verdict else None,
        "cloud_skipped": None,
    }
    if use_cloud:
        s3 = slots.get("dottie.reasoning") or {}
        evidence["cloud_requested"] = True
        evidence["cloud_model"] = s3.get("model")
        # Credential resolution is the caller's (dottie.models credential()); this function
        # does not read secrets. If the caller did not pre-resolve a usable credential, the
        # cloud tier is honestly skipped rather than attempted and failed silently.
        if not s3.get("_credential_present"):
            evidence["cloud_skipped"] = ("no credential (OLLAMA_API_KEY / OLLAMA_API_KEY_FILE "
                                         "unresolved) — answered locally")
        else:
            # The credential VALUE is resolved here and handed straight to the transport. It is
            # never put in evidence, never logged, and never returned.
            try:
                from .models import credential as _cred
                resolved = None
                binding = self_slot = None
                try:
                    from .models import load as _load
                    _reg = _load()
                    _key = _cred(_reg.resolve("dottie.reasoning"))
                except Exception:
                    _key = None
            except Exception:
                _key = None
            escalated = lfm_chat(text, base_url=s3["base_url"], model=s3["model"],
                                 system=system, api_key=_key)
            evidence["tier_used"] = "reasoning" if escalated.get("ok") else "local"
            evidence["cloud_response"] = escalated
            evidence["cloud_attempted"] = True
            if not escalated.get("ok"):
                # A FAILED cloud call is a SKIP with a cause, not a silent success. The local
                # answer is what `answer` holds, so a caller must not attribute it to tier 3.
                evidence["cloud_skipped"] = ("reasoning tier failed: %s"
                                             % (escalated.get("error") or "unknown"))
                evidence["reasoning_answer"] = None
            else:
                evidence["reasoning_answer"] = escalated.get("answer")

    return {
        "tier_used": evidence["tier_used"],
        "answer": answer.get("answer"),
        "verdict": verdict,
        "evidence": evidence,
        "ok": bool(answer.get("ok")),
    }
