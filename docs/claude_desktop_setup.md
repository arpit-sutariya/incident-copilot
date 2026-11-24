# Use the Incident Copilot from Claude Desktop (no API key)

Claude investigates through the MCP server and proposes a fix. You approve it in the web console. The MCP server has no approve tool, so Claude can't run anything itself.

## 1. Install (once)

From the `incident-copilot` folder:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pwd
```
Copy the path `pwd` prints, e.g. `/Users/you/Documents/incident-copilot`.

## 2. Add the server to Claude Desktop

Open Claude Desktop → **Settings** → **Developer** → **Edit Config**, and add this (with your path in both places). If there's already an `mcpServers` section, add the `shoplite-copilot` entry inside it.

```json
{
  "mcpServers": {
    "shoplite-copilot": {
      "command": "/Users/you/Documents/incident-copilot/.venv/bin/python",
      "args": ["/Users/you/Documents/incident-copilot/mcp_server/server.py"]
    }
  }
}
```

Save, quit Claude Desktop completely (**Cmd+Q**), and reopen it. You should see **shoplite-copilot** with 10 tools in the tools menu of the message box.

## 3. Run an incident

1. In Terminal: `python -m console.app`, then open http://localhost:9200.
2. Click **Bad orders deploy**, then **Harmless deploy (decoy)**. Wait for the alert.
3. In Claude Desktop, ask: *"An alert fired on ShopLite. Investigate and propose a fix."*
4. Watch Claude call the tools. Its proposal appears in the console under **Proposed actions**.
5. Approve or reject it in the console. Then ask Claude: *"Did it work?"* It checks with `get_action_status`.

The decoy is the interesting test: the rule-based copilot rolls back the decoy, while Claude has to notice that the errors are in **orders**, started with **orders 2.4.0**, and only happen on that version.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Server not listed | Check the JSON (no trailing commas, absolute paths), then Cmd+Q and reopen |
| Tools return "No active alert" | Start the console and break something first; the copilot reads the console's live data |
| "Connection refused" in tool results | The console (which runs ShopLite) isn't running |
