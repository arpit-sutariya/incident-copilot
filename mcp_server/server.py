"""MCP server: lets Claude Desktop investigate ShopLite incidents and PROPOSE fixes.
Approving a fix happens in the web console (http://localhost:9200), by a person. This server has no
approve tool, so the model can't execute anything.

Configured in Claude Desktop's claude_desktop_config.json (see docs/claude_desktop_setup.md).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from copilot.tools import INSTRUCTIONS, Toolbox  # noqa: E402

tb = Toolbox(actor="claude-desktop")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
PROPOSE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
server = MCPServer(name="shoplite-incident-copilot", instructions=INSTRUCTIONS)


@server.tool(annotations=READ)
def get_alert() -> dict:
    """The alert that fired: rule, summary, value, and when the incident window started."""
    return tb.get_alert()


@server.tool(annotations=READ)
def get_service_health() -> dict:
    """Error rate and p95 latency for gateway, orders and payments: before the incident, during it,
    and over the latest 50 requests (shows whether it has already recovered)."""
    return tb.get_service_health()


@server.tool(annotations=READ)
def query_metrics(service: str) -> dict:
    """One service (gateway, orders or payments) in detail: stats by endpoint and by running version,
    and its most frequent error messages during the incident."""
    return tb.query_metrics(service)


@server.tool(annotations=READ)
def search_logs(service: str, level: str = "ERROR", contains: str | None = None, limit: int = 15) -> dict:
    """Recent log lines for a service, filtered by level (ERROR/INFO) and optional text."""
    return tb.search_logs(service, level, contains, limit)


@server.tool(annotations=READ)
def get_deploy_history(limit: int = 10) -> dict:
    """Recent deploys and rollbacks: service, versions, author, change summary, timing vs the incident."""
    return tb.get_deploy_history(limit)


@server.tool(annotations=READ)
def check_dependencies() -> dict:
    """Database connectivity, payment-provider latency vs the timeout, running versions, feature flags."""
    return tb.check_dependencies()


@server.tool(annotations=READ)
def list_runbooks() -> dict:
    """Names of the team's runbooks."""
    return tb.list_runbooks()


@server.tool(annotations=READ)
def get_runbook(name: str) -> dict:
    """Read one runbook."""
    return tb.get_runbook(name)


@server.tool(annotations=PROPOSE)
def propose_action(action: str, target: str, reason: str) -> dict:
    """Propose ONE recovery action. It is NOT executed: a person approves it in the console.
    Allowed: rollback_deploy(gateway|orders|payments), restart_service(gateway|orders|payments),
    restart_database(database), enable_flag(payments_fallback), disable_flag(payments_fallback), no_action(none)."""
    return tb.propose_action(action, target, reason)


@server.tool(annotations=READ)
def get_action_status(action_id: int) -> dict:
    """Whether a proposed action was approved, rejected or executed, and whether the service recovered."""
    return tb.get_action_status(action_id)


if __name__ == "__main__":
    server.run()
