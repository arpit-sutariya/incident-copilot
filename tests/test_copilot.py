"""Run from the project folder:  pytest -q   (starts ShopLite itself; takes about a minute)"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from copilot import actions, baseline
from copilot.agent import Copilot
from copilot.tools import Toolbox
from shoplite import chaos, state, traffic


# ------------------------------------------------------------------ the system under test behaves
@pytest.mark.parametrize("scenario", list(chaos.SCENARIOS))
def test_every_scenario_raises_an_alert_and_its_fix_works(cluster, scenario):
    inc = chaos.run(scenario, seed=5)
    assert inc.alert and inc.alert["rule"] == "HighErrorRate"
    tb = Toolbox("test")
    res = tb.propose_action(inc.fix["action"], inc.fix["target"], "known fix")
    if inc.fix["action"] != "no_action":
        actions.approve(res["action_id"], "tester")
    traffic.burst(60, seed=9)
    assert not chaos.still_broken(scenario)
    assert actions.verify(res["action_id"] if inc.fix["action"] != "no_action" else None)["status"] == "recovered"


def test_wrong_fix_is_caught_by_verification(cluster):
    chaos.run("database_outage", seed=1)
    res = Toolbox("test").propose_action("restart_service", "orders", "wrong on purpose")
    actions.approve(res["action_id"], "tester")
    traffic.burst(60, seed=2)
    assert actions.verify(res["action_id"])["status"] == "not_recovered"


# ------------------------------------------------------------------ action safety
@pytest.mark.parametrize("action,target", [("drop_database", "database"), ("rollback_deploy", "database"),
                                           ("restart_service", "kubernetes"), ("enable_flag", "debug_mode"),
                                           ("deploy", "orders"), ("rollback_deploy", "payments")])
def test_actions_off_the_allowlist_are_refused_and_recorded(cluster, action, target):
    chaos.run("database_outage", seed=3)   # no deploys exist, so rolling back payments is refused too
    out = Toolbox("test").propose_action(action, target, "trying")
    assert out["refused"] is True
    assert actions.list_actions()[-1]["status"] == "refused"


def test_nothing_runs_without_approval(cluster):
    chaos.run("database_outage", seed=4)
    res = Toolbox("test").propose_action("restart_database", "database", "fix")
    assert state.load()["faults"]["db_down"] is True          # proposed, not executed
    actions.reject(res["action_id"], "tester")
    with pytest.raises(actions.ActionRefused):
        actions.approve(res["action_id"], "tester")          # rejected stays rejected
    assert state.load()["faults"]["db_down"] is True


def test_copilot_tools_cannot_approve():
    assert not any(hasattr(Toolbox, name) for name in ("approve", "approve_action", "execute", "execute_action"))


def test_mcp_server_has_no_approve_tool(cluster):
    from tests.mcp_smoke import main
    chaos.run("database_outage", seed=6)
    out = asyncio.run(main())
    assert "propose_action" in out["tools"] and not any("approve" in t for t in out["tools"])
    assert out["refused"] and out["proposal_status"] == "pending_approval"


# ------------------------------------------------------------------ diagnosers
def test_baseline_diagnoses_a_database_outage(cluster):
    inc = chaos.run("database_outage", seed=7)
    r = baseline.diagnose(Toolbox("baseline"))
    assert (r.cause, r.component, r.action) == (inc.cause, inc.component, "restart_database")


def test_baseline_is_fooled_by_a_later_decoy_deploy(cluster):
    """Documents the baseline's known weakness, which the AI copilot is meant to fix."""
    inc = chaos.run("bad_deploy_orders", "decoy_after", seed=8)
    r = baseline.diagnose(Toolbox("baseline"))
    assert r.target != "orders" and inc.decoy is not None


def _resp(*blocks):
    return SimpleNamespace(content=list(blocks), usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def _tool(i, name, args):
    return SimpleNamespace(type="tool_use", id=f"t{i}", name=name, input=args)


class Script:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = self

    def create(self, **kw):
        return next(self.responses)


def test_ai_copilot_loop_with_a_scripted_model(cluster):
    """No API key needed: a scripted 'model' drives the real tools to check the agent loop's plumbing."""
    chaos.run("pool_exhaustion", seed=9)
    model = Script([
        _resp(_tool(1, "get_service_health", {})),
        _resp(_tool(2, "query_metrics", {"service": "orders"})),
        _resp(_tool(3, "propose_action", {"action": "delete_everything", "target": "orders", "reason": "x"})),
        _resp(_tool(4, "propose_action", {"action": "restart_service", "target": "orders", "reason": "pool"})),
        _resp(_tool(5, "final_report", {"cause": "resource_exhaustion", "component": "orders",
                                        "summary": "Connection pool exhausted.", "evidence": ["QueuePool"]})),
    ])
    c = Copilot(client=model)
    r = c.diagnose()
    assert (r.cause, r.component, r.action, r.target) == ("resource_exhaustion", "orders", "restart_service", "orders")
    assert actions.get(r.action_id)["status"] == "pending_approval"
    assert [a["status"] for a in actions.list_actions()] == ["refused", "pending_approval"]
    assert c.tokens == 600
