"""Rule-based diagnoser: the kind of script a careful on-call engineer might write. Free, instant and
explainable. It's the baseline the AI copilot has to beat.

Its weak spot is on purpose: when the cause looks like a deploy, it rolls back the most recent deploy,
whichever service that was. Real triage has the same temptation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from copilot.tools import Toolbox


@dataclass
class Report:
    cause: str          # bad_deploy | database_outage | slow_dependency | resource_exhaustion | none
    component: str      # gateway | orders | payments | database | none
    action: str
    target: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    action_id: int | None = None
    tool_calls: int = 0


def diagnose(tb: Toolbox) -> Report:
    calls = 0

    def call(fn, *a, **kw):
        nonlocal calls
        calls += 1
        return fn(*a, **kw)

    alert = call(tb.get_alert)
    health = call(tb.get_service_health)["services"]
    gw = health["gateway"]["latest_50_requests"]
    # "already recovered" needs a big enough sample: a bug hitting 12% of requests often shows 0 errors in 20
    if "rule" not in alert or gw["error_rate"] <= 0.01:
        r = Report("none", "none", "no_action", "none", "Gateway is healthy now; the alert has already recovered.",
                   [f"gateway latest error rate {gw['error_rate']:.0%}"])
    else:
        deps = call(tb.check_dependencies)
        orders_errors = " ".join(e["message"] for e in call(tb.query_metrics, "orders")["top_errors_during_incident"])
        db = str(deps.get("database"))
        if "Connection refused" in db or "could not connect" in orders_errors:
            r = Report("database_outage", "database", "restart_database", "database",
                       "Orders can't connect to the database.", [f"orders health check: {db}"])
        elif "QueuePool" in orders_errors:
            r = Report("resource_exhaustion", "orders", "restart_service", "orders",
                       "Orders has run out of database connections.", [orders_errors[:160]])
        elif "TimeoutError calling payments" in orders_errors:
            r = Report("slow_dependency", "payments", "enable_flag", "payments_fallback",
                       "The payment provider is too slow; checkouts time out.", [orders_errors[:160]])
        else:
            deploys = call(tb.get_deploy_history)["deploys"]
            if deploys:
                last = deploys[-1]  # naive: the most recent deploy, whichever service it was
                r = Report("bad_deploy", last["service"], "rollback_deploy", last["service"],
                           f"Errors after deploying {last['service']} {last['to_version']}.",
                           [f"latest deploy: {last['service']} {last['from_version']} -> {last['to_version']} "
                            f"({last['when']})"])
            else:
                r = Report("none", "none", "no_action", "none", "No cause found.", [])
    res = call(tb.propose_action, r.action, r.target, r.summary)
    r.action_id = res.get("action_id")
    r.tool_calls = calls
    return r
