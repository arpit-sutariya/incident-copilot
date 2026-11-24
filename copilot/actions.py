"""Recovery actions: proposed by the copilot, approved by a person, executed, then verified.

Safety rules, enforced here rather than in the AI's instructions:
  * Only actions on the ALLOWED list, with valid targets, can even be proposed. Anything else is refused
    and recorded.
  * The copilot can PROPOSE. Only approve() executes, and the copilot's tools don't include approve().
  * Every proposal, approval, rejection, execution and verification is written to runtime/actions.json.
"""
from __future__ import annotations

import json
import os
import time

from shoplite import monitor, state
from shoplite.config import ACTIONS_FILE, SERVICES

ALLOWED = {
    "rollback_deploy": {"targets": SERVICES, "does": "Return a service to the version before its latest deploy"},
    "restart_service": {"targets": SERVICES, "does": "Restart a service's process (clears leaked resources)"},
    "restart_database": {"targets": ["database"], "does": "Fail the database over to a healthy replica"},
    "enable_flag": {"targets": list(state.FLAGS), "does": "Turn a feature flag on"},
    "disable_flag": {"targets": list(state.FLAGS), "does": "Turn a feature flag off"},
    "no_action": {"targets": ["none"], "does": "Record a decision not to act (e.g. already recovered)"},
}
# Recovered = at most 1% errors over at least 40 requests. Some failures only hit a slice of traffic
# (e.g. checkouts with a coupon, ~12%), so a small or lenient sample can look healthy when it isn't.
RECOVERED_ERROR_RATE = 0.01
MIN_VERIFY_REQUESTS = 40


class ActionRefused(Exception):
    pass


def _load() -> list[dict]:
    return json.loads(ACTIONS_FILE.read_text()) if ACTIONS_FILE.exists() else []


def _save(items: list[dict]):
    ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACTIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1))
    os.replace(tmp, ACTIONS_FILE)


def _update(action_id: int, **fields) -> dict:
    items = _load()
    for a in items:
        if a["id"] == action_id:
            a.update(fields)
            _save(items)
            return a
    raise ActionRefused(f"No action #{action_id}")


def list_actions() -> list[dict]:
    return _load()


def get(action_id: int) -> dict:
    for a in _load():
        if a["id"] == action_id:
            return a
    raise ActionRefused(f"No action #{action_id}")


def propose(action: str, target: str, reason: str, proposed_by: str) -> dict:
    items = _load()
    rec = {"id": len(items) + 1, "action": action, "target": target, "reason": reason, "proposed_by": proposed_by,
           "proposed_at": time.time()}
    problem = None
    if action not in ALLOWED:
        problem = f"'{action}' isn't an allowed action. Allowed: {', '.join(ALLOWED)}."
    elif target not in ALLOWED[action]["targets"]:
        problem = f"'{target}' isn't a valid target for {action}. Valid: {', '.join(ALLOWED[action]['targets'])}."
    elif action == "rollback_deploy" and not any(d["service"] == target for d in state.load()["deploys"]):
        problem = f"{target} has no deploy to roll back."
    if problem:
        rec.update(status="refused", refusal=problem)
        items.append(rec)
        _save(items)
        raise ActionRefused(problem)
    rec["status"] = "recorded" if action == "no_action" else "pending_approval"
    items.append(rec)
    _save(items)
    return rec


def reject(action_id: int, approver: str, note: str = "") -> dict:
    a = get(action_id)
    if a["status"] != "pending_approval":
        raise ActionRefused(f"Action #{action_id} is {a['status']}, not pending approval")
    return _update(action_id, status="rejected", decided_by=approver, decided_at=time.time(), note=note)


def approve(action_id: int, approver: str) -> dict:
    """The only path that changes production. Approve, then execute."""
    a = get(action_id)
    if a["status"] != "pending_approval":
        raise ActionRefused(f"Action #{action_id} is {a['status']}, not pending approval")
    _update(action_id, status="approved", decided_by=approver, decided_at=time.time())
    try:
        detail = _execute(a["action"], a["target"])
    except Exception as e:
        return _update(action_id, status="failed", error=str(e))
    return _update(action_id, status="executed", executed_at=time.time(), result=detail)


def _execute(action: str, target: str) -> str:
    if action == "rollback_deploy":
        rec = state.rollback(target, author="incident-copilot")
        return f"{target} rolled back {rec['from_version']} -> {rec['to_version']}"
    if action == "restart_service":
        state.restart_service(target)
        return f"{target} restarted"
    if action == "restart_database":
        state.restart_database()
        return "database failed over to a healthy replica"
    if action in ("enable_flag", "disable_flag"):
        state.set_flag(target, action == "enable_flag")
        return f"flag {target} {'on' if action == 'enable_flag' else 'off'}"
    raise ActionRefused(f"Can't execute {action}")


def verify(action_id: int | None = None, min_requests: int = MIN_VERIFY_REQUESTS) -> dict:
    """Has the gateway recovered since the action ran (or right now, for no_action)?"""
    since = get(action_id).get("executed_at") if action_id else None
    if action_id and since is None:
        return {"status": "not_executed"}
    reqs = monitor.requests("gateway", since=since) if since else monitor.requests("gateway")[-min_requests:]
    s = monitor.stats(reqs)
    if s["requests"] < min_requests:
        return {"status": "waiting_for_traffic", **s}
    result = {"status": "recovered" if s["error_rate"] <= RECOVERED_ERROR_RATE else "not_recovered", **s}
    if action_id:
        _update(action_id, verification=result)
    return result
