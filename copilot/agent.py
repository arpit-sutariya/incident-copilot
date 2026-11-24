"""AI copilot: Claude with the same tools as the baseline, plus final_report for a structured diagnosis.
Needs ANTHROPIC_API_KEY. Model: COPILOT_MODEL (default claude-sonnet-5-5).
Without a key, use Claude Desktop with the MCP server (docs/claude_desktop_setup.md)."""
from __future__ import annotations

import json
import os

from copilot.baseline import Report
from copilot.tools import INSTRUCTIONS, Toolbox

MAX_STEPS = 14
CAUSES = ["bad_deploy", "database_outage", "slow_dependency", "resource_exhaustion", "none"]
COMPONENTS = ["gateway", "orders", "payments", "database", "none"]

FINAL_RULE = """
When done, call final_report once: cause, component, a two-sentence summary, the evidence you relied on,
and the action_id returned by propose_action.
"""

_obj = {"type": "object", "properties": {}}
TOOLS = [
    {"name": "get_alert", "description": "The alert that fired.", "input_schema": _obj},
    {"name": "get_service_health", "description": "Error rate and p95 latency per service: before the incident, "
     "during it, and the latest 50 requests.", "input_schema": _obj},
    {"name": "query_metrics", "description": "One service in detail: before/during stats, by endpoint, by "
     "version, and its top error messages.",
     "input_schema": {"type": "object", "properties": {"service": {"type": "string", "enum": ["gateway", "orders", "payments"]}},
                      "required": ["service"]}},
    {"name": "search_logs", "description": "Recent log lines for a service, filtered by level and text.",
     "input_schema": {"type": "object", "properties": {
         "service": {"type": "string", "enum": ["gateway", "orders", "payments"]},
         "level": {"type": "string", "enum": ["ERROR", "INFO"]}, "contains": {"type": "string"},
         "limit": {"type": "integer"}}, "required": ["service"]}},
    {"name": "get_deploy_history", "description": "Recent deploys and rollbacks, with timing relative to the incident.",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "check_dependencies", "description": "Database connectivity, payment provider latency, running "
     "versions and feature flags.", "input_schema": _obj},
    {"name": "list_runbooks", "description": "Available runbooks.", "input_schema": _obj},
    {"name": "get_runbook", "description": "Read a runbook.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "propose_action", "description": "Propose ONE recovery action for a person to approve. Allowed: "
     "rollback_deploy(gateway|orders|payments), restart_service(gateway|orders|payments), "
     "restart_database(database), enable_flag(payments_fallback), disable_flag(payments_fallback), no_action(none).",
     "input_schema": {"type": "object", "properties": {"action": {"type": "string"}, "target": {"type": "string"},
                                                       "reason": {"type": "string"}},
                      "required": ["action", "target", "reason"]}},
    {"name": "final_report", "description": "Your diagnosis. Call once, last.",
     "input_schema": {"type": "object", "properties": {
         "cause": {"type": "string", "enum": CAUSES}, "component": {"type": "string", "enum": COMPONENTS},
         "summary": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "string"}},
         "action_id": {"type": ["integer", "null"]}}, "required": ["cause", "component", "summary"]}},
]


class Copilot:
    def __init__(self, client=None, model: str | None = None, actor: str = "ai-copilot"):
        if client is None:
            import anthropic
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise RuntimeError("Set ANTHROPIC_API_KEY to use the AI copilot, or use Claude Desktop via MCP.")
            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.getenv("COPILOT_MODEL", "claude-sonnet-5-5")
        self.tb = Toolbox(actor)
        self.tokens = 0

    def _call(self, name: str, args: dict):
        fn = getattr(self.tb, name, None)
        if fn is None or name.startswith("_"):
            return {"error": f"Unknown tool {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"Bad arguments: {e}"}

    def diagnose(self) -> Report:
        messages = [{"role": "user", "content": "An alert fired. Investigate and propose a recovery action."}]
        calls, proposals = 0, []
        for _ in range(MAX_STEPS):
            resp = self.client.messages.create(model=self.model, max_tokens=2000, system=INSTRUCTIONS + FINAL_RULE,
                                               tools=TOOLS, messages=messages)
            u = getattr(resp, "usage", None)
            self.tokens += (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "output_tokens", 0) or 0)
            messages.append({"role": "assistant", "content": resp.content})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                break
            results = []
            for tu in uses:
                if tu.name == "final_report":
                    a = tu.input
                    last = proposals[-1] if proposals else {}
                    return Report(a.get("cause", "none"), a.get("component", "none"), last.get("action", "none"),
                                  last.get("target", "none"), a.get("summary", ""), a.get("evidence") or [],
                                  a.get("action_id") or last.get("action_id"), calls)
                calls += 1
                out = self._call(tu.name, tu.input or {})
                if tu.name == "propose_action" and isinstance(out, dict) and out.get("action_id"):
                    proposals.append({**tu.input, "action_id": out["action_id"]})
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": json.dumps(out, default=str)[:15000]})
            messages.append({"role": "user", "content": results})
        last = proposals[-1] if proposals else {}
        return Report("none", "none", last.get("action", "none"), last.get("target", "none"),
                      "The copilot stopped without a final report.", [], last.get("action_id"), calls)
