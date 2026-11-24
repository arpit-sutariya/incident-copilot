"""Alerting: watches the gateway (what customers see) and raises an alert when it's unhealthy.
The alert names the symptom, not the cause; finding the cause is the copilot's job."""
from __future__ import annotations

import json
import time

from shoplite.config import ALERT_FILE, GATEWAY_TIMEOUT_S, LOG_DIR, TIME_SCALE

ERROR_RATE_THRESHOLD = 0.05
P95_THRESHOLD_S = 1.5


def requests(service: str, since: float | None = None, until: float | None = None) -> list[dict]:
    path = LOG_DIR / f"{service}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line being written right now
        if r.get("kind") != "request":
            continue
        if since is not None and r["ts"] < since:
            continue
        if until is not None and r["ts"] > until:
            continue
        out.append(r)
    return out


def stats(reqs: list[dict]) -> dict:
    if not reqs:
        return {"requests": 0, "error_rate": 0.0, "p95_ms": 0.0}
    lat = sorted(r["latency_ms"] for r in reqs)
    errors = sum(1 for r in reqs if r["status"] >= 500)
    return {"requests": len(reqs), "error_rate": round(errors / len(reqs), 3),
            "p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))]}


def evaluate(since: float) -> dict | None:
    """Check the gateway since `since`; write and return an alert if a rule fires."""
    s = stats(requests("gateway", since=since))
    alert = None
    if s["requests"] and s["error_rate"] > ERROR_RATE_THRESHOLD:
        alert = {"rule": "HighErrorRate", "summary": f"Gateway error rate {s['error_rate']:.0%} "
                 f"(threshold {ERROR_RATE_THRESHOLD:.0%})", "value": s["error_rate"]}
    elif s["requests"] and s["p95_ms"] > P95_THRESHOLD_S * 1000 * TIME_SCALE:
        alert = {"rule": "HighLatency", "summary": f"Gateway p95 latency {s['p95_ms']:.0f} ms", "value": s["p95_ms"]}
    if alert:
        alert.update(id=f"ALERT-{int(time.time())}", service="gateway", window_start=since, fired_at=time.time(),
                     requests_in_window=s["requests"])
        ALERT_FILE.write_text(json.dumps(alert, indent=1))
    else:
        ALERT_FILE.unlink(missing_ok=True)
    return alert


def current_alert() -> dict | None:
    return json.loads(ALERT_FILE.read_text()) if ALERT_FILE.exists() else None


# referenced by the console to scale its live windows
LIVE_WINDOW_S = 30 * TIME_SCALE
GATEWAY_TIMEOUT_MS = GATEWAY_TIMEOUT_S * 1000 * TIME_SCALE
