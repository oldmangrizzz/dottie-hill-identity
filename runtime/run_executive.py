#!/usr/bin/env python3
"""Dottie's executive runner: resume every open task on start, then serve new ones on demand.

This is the process that makes the loop operational rather than importable. It does one thing
on startup — continue every ACTIVE task from bytes — and then watches a spool directory for
new task requests, so an interruption costs the principal nothing but the wall time.

Configuration (invariant I9 — nothing here is a credential; the token is still READ FROM A FILE):

  DOTTIE_RUNTIME_ROOT   where the `dottie` package is imported from
                        (default /opt/dottie-runtime — the deployed checkout)
  DOTTIE_MEMORY_TOKEN_FILE
                        path to the file holding the bearer token
                        (default /etc/dottie-memory.token; the value is never logged)
  DOTTIE_MEMORY_URL     the memory service base URL (default http://127.0.0.1:8471)
  DOTTIE_SPOOL          the task-spool directory (default /var/lib/dottie-executive)

The DEFAULTS are the deployed values, so this process behaves identically on CT 115 whether or
not the environment says anything. A host that runs the same code somewhere else can now move
the paths without editing this file.
"""
import json, os, sys, time

RUNTIME_ROOT = os.environ.get("DOTTIE_RUNTIME_ROOT") or "/opt/dottie-runtime"
sys.path.insert(0, RUNTIME_ROOT)
from dottie import executive as E, cognition, models as M

TOKEN_FILE = os.environ.get("DOTTIE_MEMORY_TOKEN_FILE") or "/etc/dottie-memory.token"
TOKEN = open(TOKEN_FILE).read().strip()
os.environ.setdefault("DOTTIE_MODEL_ALLOW_PLAINTEXT", "127.0.0.1")
SPOOL = os.environ.get("DOTTIE_SPOOL") or "/var/lib/dottie-executive"
os.makedirs(SPOOL, exist_ok=True)

MEMORY_URL = os.environ.get("DOTTIE_MEMORY_URL") or "http://127.0.0.1:8471"
store = E.Store(base_url=MEMORY_URL, token=TOKEN)
reg = M.load()
def _slot(s):
    b = reg.resolve(s)
    d = {"provider": getattr(b, "provider"), "model": getattr(b, "model"),
         "base_url": getattr(b, "base_url")}
    # The cloud tier is only attempted when a credential actually resolves. Carrying this flag
    # is what lets cognition.think() HONESTLY SKIP instead of attempting a 401 and calling the
    # resulting silence an answer.
    try:
        d["_credential_present"] = M.credential(b) is not None
    except Exception:
        d["_credential_present"] = False
    return d


slots = {s: _slot(s) for s in M.SLOTS}
# Alternate local engine: Ollama daemon on the same box, same GGUF, its own queue.
slots.setdefault("dottie.system1_alt", {
    "provider": "ollama-local", "model": "lfm3:1.7b-q4_K_M",
    "base_url": "http://127.0.0.1:11434"})


def think(step, *, use_cloud=False, state=None, questions=None, system=None):
    """The local tier's answer, and -- when explicitly asked -- the reasoning tier's too.

    `use_cloud` is NOT defaulted on: escalation is licensed by the caller (the executive asks for
    a lease before setting it), so this function never spends the cloud tier on its own authority.
    The full cognition envelope is returned so the executive can retain BOTH answers.
    """
    out = cognition.think(step, slots=slots, state=state or {},
                          questions=questions, use_cloud=use_cloud, system=system)
    out.setdefault("tier", out.get("tier_used"))
    return out


def judge(step):
    v = cognition.laya_judge(
        {"step": step},
        {"q": {"type": "choice",
               "instructions": "Decide from the stated trust rule, not from plausibility.",
               "criteria": {"yes": "the statement may be treated as fact",
                            "no": "the statement may not be treated as fact"}}},
        base_url="http://127.0.0.1:8473")
    answer = E.verdict_text((v.get("verdict") or {}).get("q"))
    return {"ok": bool(v.get("ok")) and bool(answer), "answer": answer,
            "confidence": v.get("confidence")}


def agent(agent_id, instruction):
    # Dispatch a step to one AEGIS intelligence, as that agent, over its own identity.
    # Each agent has its own memory client, so the row is stamped with the agent id and no
    # other agent can read or replace it. Dottie coordinates; she does not write in their name.
    # The agent model is cloud-only by the principal rule: with no credential the step fails
    # honestly rather than borrowing the local model.
    try:
        from aegis.registry import clients as aegis_clients, AEGIS
    except Exception as exc:
        return {"ok": False, "error": "aegis registry unavailable: %s" % exc}
    if agent_id not in AEGIS:
        return {"ok": False, "error": "unknown AEGIS agent %r" % agent_id}
    try:
        client = aegis_clients("http://127.0.0.1:8471", TOKEN)[agent_id]
    except Exception as exc:
        return {"ok": False, "error": "cannot build the %s client: %s" % (agent_id, exc)}
    slot = slots.get("aegis." + agent_id) or {}
    if not (os.environ.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_API_KEY_FILE")):
        return {"ok": False,
                "error": ("no cloud credential: AEGIS %s is cloud-only and cannot borrow the local model" % agent_id)}
    # Two things an AEGIS agent needs that a bare model handle does not:
    #   1. the credential, or the cloud endpoint answers 401 (the transport sent no auth header)
    #   2. its own instruction text, or the model has no idea which agent it is speaking as.
    # Without both, five slots were five identical unauthenticated calls -- verified, not assumed.
    try:
        from dottie.lineage import compute_instructions
        persona = compute_instructions(agent_id)
    except Exception as exc:
        persona = None
        persona_error = "%s: %s" % (type(exc).__name__, exc)
    else:
        persona_error = None
    if not persona:
        # Refuse rather than run an anonymous model call under an agent's name. An agent
        # answering with no identity is not that agent; it is a handle wearing its label.
        return {"ok": False, "agent": agent_id,
                "error": ("agent identity instructions unavailable (%s); refusing to answer "
                          "under this agent's name" % (persona_error or "empty instructions"))}
    try:
        key = M.credential(reg.resolve("aegis." + agent_id))
    except Exception as exc:
        return {"ok": False, "agent": agent_id,
                "error": "credential resolution failed: %s: %s" % (type(exc).__name__, exc)}
    answer = cognition.lfm_chat(instruction, base_url=slot.get("base_url", ""),
                                model=slot.get("model", ""), api_key=key, system=persona)
    if not answer.get("ok"):
        return {"ok": False, "agent": agent_id, "error": answer.get("error")}
    text = (answer.get("answer") or "").strip()
    if not text:
        return {"ok": False, "error": "agent returned an empty answer"}
    try:
        client.save(text, kind="operational")
    except Exception as exc:
        return {"ok": False, "error": "agent answer not recorded: %s" % exc}
    return {"ok": True, "answer": text, "agent": agent_id}






def planner(request):
    # PLANNING GOES TO OLLAMA. Measured: with three concurrent tasks, llama.cpp's single
    # serialized slot truncated the JSON array mid-element on 2 of 3 plans (1 of 3 even
    # with a bounded retry). Ollama runs the SAME GGUF with its own queue and returns
    # intact arrays under the same load. Planning therefore uses the alternate engine
    # while llama.cpp serves the single-path prose steps.
    alt = slots.get("dottie.system1_alt") or {}
    return cognition.lfm_chat(E.PLANNER_PROMPT + "\n\nREQUEST:\n" + request,
                              base_url=alt.get("base_url") or slots["dottie.system1"]["base_url"],
                              model=alt.get("model") or slots["dottie.system1"]["model"],
                              max_tokens=3072, disable_thinking=True)

ex = E.Executive(store, think=think, judge=judge, agent=agent)


def _resume_in_background():
    # Resume is work, but it is not a prerequisite for accepting new work. Calling
    # resume_all() inline put the service inside model calls for minutes while a queued
    # request sat untouched across two restarts.
    import threading

    def _run():
        try:
            resumed = ex.resume_all()
            print("DOTTIE-EXEC: resumed %d task(s)" % len(resumed), flush=True)
            for t in resumed:
                print("  %s -> %s" % (t.task_id, t.state), flush=True)
        except Exception as exc:
            print("DOTTIE-EXEC: resume failed: %s: %s" % (type(exc).__name__, exc),
                  file=sys.stderr, flush=True)

    threading.Thread(target=_run, name="resume", daemon=True).start()


import concurrent.futures

WORKERS = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="task")

print("DOTTIE-EXEC: accepting work; resume running alongside", flush=True)
_resume_in_background()


# On start, any spec left .inprogress by a previous process is unfinished work, not a
# finished file: put it back in the queue so a restart resumes it.
for name in sorted(os.listdir(SPOOL)):
    if name.endswith(".json.inprogress"):
        target = os.path.join(SPOOL, name[:-11])
        os.rename(os.path.join(SPOOL, name), target)
        os.utime(target, None)
        print("DOTTIE-EXEC: requeued interrupted spec %s" % name, flush=True)

# Recover claims whose worker is gone: anything still .inprogress after STALE seconds
# is re-queued. A live worker completes or fails well inside that window.
STALE = 600

while True:
    for stale in sorted(os.listdir(SPOOL)):
        if not stale.endswith(".json.inprogress"):
            continue
        full = os.path.join(SPOOL, stale)
        try:
            if time.time() - os.path.getmtime(full) > STALE:
                target = full[:-11]
                os.rename(full, target)
                os.utime(target, None)   # restart the staleness clock

                print("DOTTIE-EXEC: recovered stale claim %s" % stale, flush=True)
        except OSError:
            pass

    for name in sorted(os.listdir(SPOOL)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(SPOOL, name)
        try:
            spec = json.loads(open(path).read())
        except Exception:
            os.rename(path, path + ".bad")
            continue
        os.rename(path, path + ".inprogress")
        request = spec.get("request") or ""
        supplied = spec.get("steps")
        taken = path + ".taken"   # written only on completion

        def _execute(req=request, steps=supplied, out=taken):
            # BOTH planning and running happen here: planning is a model call of
            # tens of seconds, and doing it in the loop body blocked every other
            # queued spec.
            try:
                if steps:
                    task = ex.plan(req, [(st["instruction"], st.get("tier", "local"))
                                         for st in steps])
                else:
                    task = ex.plan_with_model(
                        req, lambda r: planner(r))
                done = ex.run(task)
                open(out, "w").write(json.dumps(E.task_summary(done), indent=1))
                # The claim outlived the work: leaving it behind meant the stale sweep
                # recovered a COMPLETED task and ran it again every STALE seconds.
                try:
                    os.remove(path + ".inprogress")
                except OSError:
                    pass
                print("DOTTIE-EXEC: %s -> %s" % (done.task_id, done.state), flush=True)
            except Exception as exc:
                print("DOTTIE-EXEC: task failed: %s: %s" % (type(exc).__name__, exc),
                      file=sys.stderr, flush=True)
                try:
                    open(out, "w").write(json.dumps(
                        {"state": "BLOCKED", "request": req,
                         "error": "%s: %s" % (type(exc).__name__, exc)}, indent=1))
                except OSError:
                    pass
                try:
                    os.remove(path + ".inprogress")
                except OSError:
                    pass

        WORKERS.submit(_execute)
        continue
    time.sleep(2)
