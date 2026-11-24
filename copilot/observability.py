"""Read-only views over logs, metrics, deploys and dependency health. What an on-call engineer would look
at in Grafana, the log search and the CI/CD history, as plain functions the copilot can call."""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime

import httpx

from shoplite import monitor, state
from shoplite.config import LOG_DIR, SERVICES, TIME_SCALE, url


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


def incident_window() -> tuple[float, float, float]:
    """(before_start, incident_start, now). Without an alert: the last 60 (scaled) seconds."""
    now = time.time()
    alert = monitor.current_alert()
    if alert:
        start = alert["window_start"]
    else:
        start = now - 30 * TIME_SCALE
    return start - max(now - start, 1.0), start, now


def _relative(ts: float, start: float) -> str:
    d = ts - start
    return f"{d:+.2f}s from incident start"


def service_metrics(service: str) -> dict:
    """Error rate and latency before vs during the incident, the latest requests, and breakdowns."""
    b0, start, now = incident_window()
    before = monitor.requests(service, since=b0, until=start)
    during = monitor.requests(service, since=start)
    out = {"service": service, "before_incident": monitor.stats(before), "during_incident": monitor.stats(during),
           "latest_50_requests": monitor.stats(during[-50:])}
    by_path, by_version = {}, {}
    for r in during:
        by_path.setdefault(r["path"], []).append(r)
        by_version.setdefault(r.get("version", "?"), []).append(r)
    out["during_by_endpoint"] = {k: monitor.stats(v) for k, v in by_path.items()}
    out["during_by_version"] = {k: monitor.stats(v) for k, v in by_version.items()}
    return out


def _error_lines(service: str, since: float) -> list[dict]:
    path = LOG_DIR / f"{service}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r["ts"] >= since and r.get("level") == "ERROR" and r.get("kind") != "request":
            out.append(r)
    return out


def _signature(r: dict) -> str:
    msg = re.sub(r"\b[0-9a-f]{12}\b", "<id>", r.get("msg", ""))
    return msg[:140]


def top_errors(service: str, limit: int = 5) -> list[dict]:
    _, start, _ = incident_window()
    lines = _error_lines(service, start)
    counts = Counter(_signature(r) for r in lines)
    first = {}
    for r in lines:
        first.setdefault(_signature(r), r)
    return [{"count": n, "message": sig, "error_type": first[sig].get("error_type"),
             "first_seen": _relative(first[sig]["ts"], start), "version": first[sig].get("version")}
            for sig, n in counts.most_common(limit)]


def search_logs(service: str, level: str = "ERROR", contains: str | None = None, limit: int = 15) -> list[dict]:
    path = LOG_DIR / f"{service}.jsonl"
    if not path.exists():
        return []
    _, start, _ = incident_window()
    keep = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if level and r.get("level") != level.upper():
            continue
        if contains and contains.lower() not in json.dumps(r).lower():
            continue
        keep.append(r)
    out = []
    for r in keep[-limit:]:
        item = {k: r.get(k) for k in ("level", "msg", "error_type", "path", "status", "latency_ms", "version",
                                       "upstream", "trace_id", "stack") if r.get(k) is not None}
        item["time"] = _clock(r["ts"])
        item["when"] = _relative(r["ts"], start)
        out.append(item)
    return out


def deploy_history(limit: int = 10) -> list[dict]:
    _, start, _ = incident_window()
    s = state.load()
    return [{**{k: d[k] for k in ("id", "kind", "service", "from_version", "to_version", "author", "summary")},
             "time": _clock(d["at"]), "when": _relative(d["at"], start)} for d in s["deploys"][-limit:]]


def dependencies() -> dict:
    out = {}
    try:
        h = httpx.get(url("orders") + "/_health", timeout=5).json()
        out["database"] = h.get("database")
    except httpx.HTTPError as e:
        out["database"] = f"unknown (orders health check failed: {type(e).__name__})"
    t = time.perf_counter()
    try:
        httpx.get(url("payments") + "/_health", timeout=10)
        ms = (time.perf_counter() - t) * 1000
        out["payments_provider"] = {"health_check_latency_ms": round(ms, 1),
                                    "orders_payment_timeout_ms": round(1000 * TIME_SCALE, 1)}
    except httpx.HTTPError as e:
        out["payments_provider"] = f"unreachable ({type(e).__name__})"
    s = state.load()
    out["running_versions"] = {svc: s["services"][svc]["version"] for svc in SERVICES}
    out["feature_flags"] = s["flags"]
    return out
