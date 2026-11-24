"""Incident scenarios. Each one breaks ShopLite in a specific way and records the true cause, so a
diagnosis can be graded. Decoys make it realistic: a harmless deploy lands near the incident, so
"roll back whatever changed last" is often wrong.

The copilot never calls anything in this file. Only the eval, the console's demo buttons and the tests do.
"""
from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass

from shoplite import monitor, state, traffic
from shoplite.run import fresh_start

# true cause, broken component, and the one fix that resolves it
SCENARIOS = {
    "bad_deploy_orders": ("bad_deploy", "orders", {"action": "rollback_deploy", "target": "orders"}),
    "bad_deploy_gateway": ("bad_deploy", "gateway", {"action": "rollback_deploy", "target": "gateway"}),
    "database_outage": ("database_outage", "database", {"action": "restart_database", "target": "database"}),
    "slow_dependency": ("slow_dependency", "payments", {"action": "enable_flag", "target": "payments_fallback"}),
    "pool_exhaustion": ("resource_exhaustion", "orders", {"action": "restart_service", "target": "orders"}),
    "false_alarm": ("none", "none", {"action": "no_action", "target": "none"}),
}
HARMLESS = [("gateway", "1.4.3"), ("orders", "2.3.1"), ("payments", "3.1.1")]


@dataclass
class Incident:
    scenario: str
    variant: str          # none | decoy_before | decoy_after
    cause: str
    component: str
    fix: dict
    decoy: str | None
    alert: dict | None
    traffic: dict


def _pause():
    time.sleep(0.05)  # keep timestamps strictly ordered


def _decoy(rng: random.Random, scenario: str, component: str) -> tuple[str, str]:
    if scenario.startswith("bad_deploy") or scenario == "pool_exhaustion":
        # a different service: deploying the leaking service would restart it and hide the leak
        options = [h for h in HARMLESS if h[0] != component]
    else:                                   # anything, including the broken component itself
        options = HARMLESS
    return rng.choice(options)


def run(scenario: str, variant: str = "none", seed: int = 0, baseline_n: int = 80, incident_n: int = 120) -> Incident:
    """Reset, send healthy traffic, break something (plus maybe a decoy deploy), send traffic, raise the alert."""
    rng = random.Random(seed)
    cause, component, fix = SCENARIOS[scenario]
    fresh_start()
    traffic.burst(baseline_n, seed=seed)
    _pause()
    decoy = None
    if variant == "decoy_before":
        decoy = _decoy(rng, scenario, component)
        state.deploy(*decoy, author=rng.choice(["priya", "ci-bot", "marco"]))
        traffic.burst(30, seed=seed + 1)  # the decoy deploy runs fine for a while
        _pause()

    incident_start = time.time()
    if scenario == "bad_deploy_orders":
        state.deploy("orders", "2.4.0", author="marco")
    elif scenario == "bad_deploy_gateway":
        state.deploy("gateway", "1.5.0", author="priya")
    elif scenario == "database_outage":
        state.set_fault("db_down", True)
    elif scenario == "slow_dependency":
        state.set_fault("payments_slow", True)
    elif scenario == "pool_exhaustion":
        state.set_fault("pool_leak", True)
    elif scenario == "false_alarm":
        state.set_fault("db_down", True)   # a 'blip': the database fails over by itself

    if scenario == "false_alarm":
        traffic.burst(incident_n // 2, seed=seed + 2)
        state.restart_database()             # automatic failover: healthy again before anyone looks
        _pause()
        if variant == "decoy_after":
            decoy = _decoy(rng, scenario, component)
            state.deploy(*decoy, author=rng.choice(["priya", "ci-bot", "marco"]))
        hist = traffic.burst(incident_n // 2, seed=seed + 3)
    elif variant == "decoy_after":
        traffic.burst(incident_n // 3, seed=seed + 2)
        _pause()
        decoy = _decoy(rng, scenario, component)
        state.deploy(*decoy, author=rng.choice(["priya", "ci-bot", "marco"]))
        hist = traffic.burst(incident_n - incident_n // 3, seed=seed + 3)
    else:
        hist = traffic.burst(incident_n, seed=seed + 2)

    alert = monitor.evaluate(since=incident_start)
    return Incident(scenario, variant, cause, component, fix,
                    f"{decoy[0]} {decoy[1]}" if decoy else None, alert, hist)


def still_broken(scenario: str) -> bool:
    """Ground truth after a fix: is the injected fault still affecting customers?"""
    s = state.load()
    if scenario == "bad_deploy_orders":
        return state.version_bug(s, "orders") is not None
    if scenario == "bad_deploy_gateway":
        return state.version_bug(s, "gateway") is not None
    if scenario in ("database_outage",):
        return s["faults"]["db_down"]
    if scenario == "slow_dependency":
        return s["faults"]["payments_slow"] and not s["flags"]["payments_fallback"]
    if scenario == "pool_exhaustion":
        return s["faults"]["pool_leak"]
    return False


def all_cases(per_type_variants=("none", "decoy_before", "decoy_after", "decoy_after")) -> list[tuple[str, str, int]]:
    """The eval's incident list: every scenario x variant, each with its own seed."""
    cases = []
    for i, scenario in enumerate(SCENARIOS):
        for j, variant in enumerate(per_type_variants):
            cases.append((scenario, variant, 1000 * i + j))
    return cases


def to_dict(inc: Incident) -> dict:
    return asdict(inc)
