"""Incident console: watch ShopLite, break it on purpose, investigate, and approve or reject fixes.

    python -m console.app            # starts ShopLite + live traffic + alerting, then http://localhost:9200

Proposals from Claude Desktop (via the MCP server) show up here too. This is the only place they get approved.
"""
from __future__ import annotations

import json
import os
import random
import signal
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from shoplite import monitor, state, traffic
from shoplite.config import ROOT, RUNTIME, SERVICES
from shoplite.run import Cluster, fresh_start

from copilot import actions, baseline
from copilot.tools import Toolbox

load_dotenv(ROOT / ".env")
STATIC = Path(__file__).parent / "static"
REPORT_FILE = RUNTIME / "report.json"
HISTORY_FILE = RUNTIME / "alert_history.json"
LIVE_WINDOW_S = 15     # alert rule window
HEALTH_WINDOW_S = 8    # what the health table shows: recent enough to reflect a recovery

app = FastAPI(title="ShopLite incident console")
_stop = threading.Event()


# ----------------------------------------------------------------------------- background loops
def _traffic_loop():
    rng = random.Random()
    with httpx.Client() as client:
        while not _stop.is_set():
            traffic.one_request(random.Random(rng.random()), client)
            time.sleep(0.15)


def _monitor_loop():
    while not _stop.is_set():
        now = time.time()
        alert = monitor.current_alert()
        if alert is None:
            monitor.evaluate(since=now - LIVE_WINDOW_S)
        else:
            # resolve only on a clean, large-enough sample: low-rate failures can look healthy on a few requests
            recent = monitor.stats(monitor.requests("gateway", since=now - 10))
            if now - alert["fired_at"] > 10 and recent["requests"] >= 30 and recent["error_rate"] == 0:
                hist = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
                hist.append({**alert, "resolved_at": now})
                HISTORY_FILE.write_text(json.dumps(hist[-20:], indent=1))
                monitor.ALERT_FILE.unlink(missing_ok=True)
        time.sleep(2)


# ----------------------------------------------------------------------------- API
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def get_state():
    now = time.time()
    s = state.load()
    health = {svc: monitor.stats(monitor.requests(svc, since=now - HEALTH_WINDOW_S)) for svc in SERVICES}
    acts = actions.list_actions()[-8:]
    for a in acts:
        if a["status"] == "executed" and a.get("verification", {}).get("status") != "recovered":
            a["verification"] = actions.verify(a["id"])
    return {
        "alert": monitor.current_alert(),
        "history": (json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else [])[-5:][::-1],
        "health": health, "versions": {k: v["version"] for k, v in s["services"].items()},
        "flags": s["flags"], "faults": s["faults"],
        "deploys": s["deploys"][-6:][::-1], "actions": acts[::-1],
        "report": json.loads(REPORT_FILE.read_text()) if REPORT_FILE.exists() else None,
        "ai_available": bool(os.getenv("ANTHROPIC_API_KEY")), "now": now,
    }


@app.post("/api/investigate/{engine}")
def investigate(engine: str):
    if monitor.current_alert() is None:
        raise HTTPException(400, "No active alert to investigate.")
    t = time.time()
    if engine == "rules":
        rep = baseline.diagnose(Toolbox("rule-based copilot"))
    elif engine == "ai":
        from copilot.agent import Copilot
        try:
            rep = Copilot(actor="ai-copilot").diagnose()
        except RuntimeError as e:
            raise HTTPException(400, str(e))
    else:
        raise HTTPException(404, "engine must be rules or ai")
    out = {**rep.__dict__, "engine": engine, "seconds": round(time.time() - t, 1), "at": time.time()}
    REPORT_FILE.write_text(json.dumps(out, indent=1))
    return out


@app.post("/api/actions/{action_id}/{decision}")
def decide(action_id: int, decision: str):
    try:
        if decision == "approve":
            return actions.approve(action_id, approver="you (console)")
        if decision == "reject":
            return actions.reject(action_id, approver="you (console)")
    except actions.ActionRefused as e:
        raise HTTPException(409, str(e))
    raise HTTPException(404, "decision must be approve or reject")


BREAK = {
    "bad_deploy_orders": lambda: state.deploy("orders", "2.4.0", author="marco"),
    "bad_deploy_gateway": lambda: state.deploy("gateway", "1.5.0", author="priya"),
    "database_outage": lambda: state.set_fault("db_down", True),
    "slow_dependency": lambda: state.set_fault("payments_slow", True),
    "pool_exhaustion": lambda: state.set_fault("pool_leak", True),
    "harmless_deploy": lambda: _harmless_deploy(),
}


def _harmless_deploy():
    """Deploy a harmless version as a decoy: to a service that isn't already running it, and never over a
    broken version (that would replace the bug, i.e. quietly fix the incident)."""
    s = state.load()
    current = s["services"]
    options = [(svc, v) for svc, v in [("gateway", "1.4.3"), ("orders", "2.3.1"), ("payments", "3.1.1")]
               if current[svc]["version"] != v and state.version_bug(s, svc) is None]
    if not options:
        raise HTTPException(409, "Every service already runs its harmless version. Reset first.")
    return state.deploy(*random.choice(options), author="ci-bot")


@app.post("/api/break/{what}")
def break_it(what: str):
    if what not in BREAK:
        raise HTTPException(404, f"Choose from {list(BREAK)}")
    BREAK[what]()
    return {"ok": True}


@app.post("/api/reset")
def reset():
    fresh_start()
    REPORT_FILE.unlink(missing_ok=True)
    HISTORY_FILE.unlink(missing_ok=True)
    return {"ok": True}


def main():
    cluster = Cluster(quiet=True).start()

    def _shutdown(signum, frame):  # uvicorn re-raises the stop signal after it shuts down: stop the services too
        _stop.set()
        cluster.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    REPORT_FILE.unlink(missing_ok=True)
    HISTORY_FILE.unlink(missing_ok=True)
    for loop in (_traffic_loop, _monitor_loop):
        threading.Thread(target=loop, daemon=True).start()
    print("Console: http://localhost:9200   (Ctrl+C to stop)")
    try:
        uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("CONSOLE_PORT", 9200)), log_level="warning")
    finally:
        _stop.set()
        cluster.stop()


if __name__ == "__main__":
    main()
