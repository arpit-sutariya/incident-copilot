"""Incident eval: inject every scenario, let a diagnoser investigate, approve its proposal, verify recovery.

Run:  python -m eval.run_eval                      # rule-based baseline (free, no API key)
      python -m eval.run_eval --diagnoser ai       # Claude copilot (needs ANTHROPIC_API_KEY)
      python -m eval.run_eval --diagnoser oracle   # cheats with the true answer: checks the harness itself

24 incidents: 6 scenarios x 4 variants (no decoy, a harmless deploy just before, two with a harmless
deploy just after). Starts the three services itself, with time compressed 10x.

Approval is simulated: a stand-in "person" approves whatever was proposed, so a wrong proposal is
executed and the verification step has to catch it. That measures the copilot, not the approver.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict

from dotenv import load_dotenv

from shoplite import chaos, traffic
from shoplite.config import ROOT
from shoplite.run import Cluster

from copilot import actions, baseline
from copilot.baseline import Report
from copilot.tools import Toolbox

load_dotenv(ROOT / ".env")
RESULTS = ROOT / "eval" / "results"


def oracle(inc) -> Report:
    """A perfect diagnoser, for checking that the harness and grading work."""
    tb = Toolbox("oracle")
    res = tb.propose_action(inc.fix["action"], inc.fix["target"], "oracle")
    return Report(inc.cause, inc.component, inc.fix["action"], inc.fix["target"], "oracle", [], res.get("action_id"), 1)


def run(diagnoser: str, model: str | None = None) -> dict:
    rows = []
    with Cluster(time_scale=0.1):
        for scenario, variant, seed in chaos.all_cases():
            inc = chaos.run(scenario, variant, seed=seed)
            t0 = time.time()
            tokens = 0
            if diagnoser == "baseline":
                rep = baseline.diagnose(Toolbox("baseline"))
            elif diagnoser == "oracle":
                rep = oracle(inc)
            else:
                from copilot.agent import Copilot
                c = Copilot(model=model)
                rep = c.diagnose()
                tokens = c.tokens
            diagnose_s = time.time() - t0

            # the simulated person approves what was proposed; then real traffic shows whether it worked
            if rep.action_id and actions.get(rep.action_id)["status"] == "pending_approval":
                actions.approve(rep.action_id, approver="eval-approver")
                traffic.burst(60, seed=seed + 50)
                outcome = actions.verify(rep.action_id)["status"]
            else:
                traffic.burst(40, seed=seed + 50)
                outcome = actions.verify(None)["status"]   # no action: is it healthy anyway?

            log = actions.list_actions()
            rows.append({
                "scenario": scenario, "variant": variant, "decoy": inc.decoy,
                "true": f"{inc.cause}/{inc.component}", "got": f"{rep.cause}/{rep.component}",
                "fix": f"{inc.fix['action']} {inc.fix['target']}", "proposed": f"{rep.action} {rep.target}",
                "cause_ok": rep.cause == inc.cause, "component_ok": rep.component == inc.component,
                "action_ok": (rep.action, rep.target) == (inc.fix["action"], inc.fix["target"]),
                "recovered": outcome == "recovered",
                "false_recovery": outcome == "recovered" and chaos.still_broken(scenario),
                "refused_proposals": sum(1 for a in log if a["status"] == "refused"),
                "unapproved_executions": sum(1 for a in log if a["status"] == "executed" and not a.get("decided_by")),
                "tool_calls": rep.tool_calls, "diagnose_s": round(diagnose_s, 2), "tokens": tokens,
                "summary": rep.summary, "evidence": rep.evidence,
            })
            r = rows[-1]
            print(f"{'PASS' if r['action_ok'] and r['recovered'] else 'FAIL'}  {scenario:<19} {variant:<13} "
                  f"got {r['got']:<29} proposed {r['proposed']:<34} {outcome}")
    return summarize(rows, diagnoser, model)


def summarize(rows, diagnoser, model) -> dict:
    n = len(rows)
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        by[r["scenario"]][0] += r["action_ok"] and r["recovered"]
        by[r["scenario"]][1] += 1
    decoy_rows = [r for r in rows if r["decoy"]]
    return {
        "diagnoser": diagnoser, "model": model, "incidents": n,
        "diagnosis_correct": sum(r["cause_ok"] and r["component_ok"] for r in rows),
        "right_action": sum(r["action_ok"] for r in rows),
        "recovered_after_one_action": sum(r["recovered"] for r in rows),
        "with_decoy_deploy": [sum(r["action_ok"] for r in decoy_rows), len(decoy_rows)],
        "false_recoveries": sum(r["false_recovery"] for r in rows),
        "wrong_fixes_caught_by_verification": sum(1 for r in rows if not r["action_ok"] and not r["recovered"]),
        "refused_proposals": sum(r["refused_proposals"] for r in rows),
        "unapproved_executions": sum(r["unapproved_executions"] for r in rows),
        "avg_tool_calls": round(sum(r["tool_calls"] for r in rows) / n, 1),
        "avg_diagnose_s": round(sum(r["diagnose_s"] for r in rows) / n, 2),
        "tokens": sum(r["tokens"] for r in rows),
        "by_scenario": dict(by), "rows": rows,
    }


def to_markdown(s: dict) -> str:
    n = s["incidents"]
    label = {"baseline": "rule-based baseline", "oracle": "oracle (harness self-check)",
             "ai": f"AI copilot ({s['model'] or 'default model'})"}[s["diagnoser"]]
    out = [f"# Incident eval: {label}", "", "| Metric | Result |", "| --- | --- |",
           f"| Correct cause and component | {s['diagnosis_correct']}/{n} |",
           f"| Right recovery action proposed | {s['right_action']}/{n} |",
           f"| Recovered after one approved action | {s['recovered_after_one_action']}/{n} |",
           f"| Right action when a decoy deploy was nearby | {s['with_decoy_deploy'][0]}/{s['with_decoy_deploy'][1]} |",
           f"| Wrong fixes caught by verification (service still failing) | {s['wrong_fixes_caught_by_verification']} |",
           f"| Verification said recovered while the fault was still active | {s['false_recoveries']} |",
           f"| Actions executed without approval | {s['unapproved_executions']} |",
           f"| Proposals refused (not on the allowlist) | {s['refused_proposals']} |",
           f"| Avg tool calls per investigation | {s['avg_tool_calls']} |",
           f"| Avg investigation time | {s['avg_diagnose_s']} s |"]
    if s["tokens"]:
        out.append(f"| Total tokens | {s['tokens']:,} |")
    out += ["", "| Scenario | Fixed |", "| --- | --- |"] + [f"| {k} | {v[0]}/{v[1]} |" for k, v in s["by_scenario"].items()]
    fails = [r for r in s["rows"] if not (r["action_ok"] and r["recovered"])]
    if fails:
        out += ["", "## Misses", "", "| Scenario | Decoy | True cause | Diagnosed | Proposed | Outcome |",
                "| --- | --- | --- | --- | --- | --- |"]
        out += [f"| {r['scenario']} ({r['variant']}) | {r['decoy'] or '-'} | {r['true']} | {r['got']} | "
                f"{r['proposed']} | {'recovered' if r['recovered'] else 'still failing'} |" for r in fails]
    return "\n".join(out) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--diagnoser", choices=["baseline", "ai", "oracle"], default="baseline")
    p.add_argument("--model", default=None)
    a = p.parse_args()
    s = run(a.diagnoser, a.model)
    md = to_markdown(s)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{a.diagnoser}.md").write_text(md)
    (RESULTS / f"{a.diagnoser}.json").write_text(json.dumps(s, indent=1, default=str))
    print("\n" + md)


if __name__ == "__main__":
    main()
