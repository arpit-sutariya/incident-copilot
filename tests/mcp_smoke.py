"""Start the MCP server the way Claude Desktop does (subprocess over stdio) and call some tools.
Needs ShopLite running with an incident (the tests set that up).

Run from the project folder:  python -m tests.mcp_smoke
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


def _text(result) -> dict:
    return json.loads("".join(getattr(c, "text", "") for c in result.content))


async def main() -> dict:
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server" / "server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            alert = _text(await s.call_tool("get_alert", {}))
            bad = _text(await s.call_tool("propose_action", {"action": "drop_database", "target": "database",
                                                             "reason": "test"}))
            ok = _text(await s.call_tool("propose_action", {"action": "restart_database", "target": "database",
                                                            "reason": "test"}))
            return {"server": init.server_info.name, "tools": tools, "alert_rule": alert.get("rule"),
                    "refused": bad.get("refused", False), "proposal_status": ok.get("status")}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main()), indent=1))
