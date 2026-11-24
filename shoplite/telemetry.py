"""Structured logs: every service writes one JSON line per request and per error, the way most teams ship
logs to Datadog/Loki/CloudWatch. Metrics (error rate, latency) are computed from the request lines."""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shoplite import state
from shoplite.config import LOG_DIR

_lock = threading.Lock()


def log(service: str, level: str, msg: str, **fields) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = {"ts": round(time.time(), 3), "service": service, "level": level, "msg": msg, **fields}
    with _lock, open(LOG_DIR / f"{service}.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def instrument(app: FastAPI, service: str) -> None:
    """Log every request (status, latency, version, trace id) and turn crashes into logged 500s."""

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        if request.url.path.startswith("/_"):  # health and admin endpoints aren't traffic
            return await call_next(request)
        trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex[:12]
        request.state.trace_id = trace_id
        start = time.perf_counter()
        version = state.load()["services"][service]["version"]
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception as e:  # an unhandled bug in the handler
            status = 500
            log(service, "ERROR", f"Unhandled {type(e).__name__}: {e}", trace_id=trace_id,
                path=request.url.path, error_type=type(e).__name__, version=version,
                stack=traceback.format_exc().strip().splitlines()[-3:])
            response = JSONResponse({"error": "internal error", "trace_id": trace_id}, status_code=500)
        ms = round((time.perf_counter() - start) * 1000, 1)
        log(service, "INFO" if status < 500 else "ERROR", "request", kind="request", method=request.method,
            path=request.url.path, status=status, latency_ms=ms, trace_id=trace_id, version=version)
        return response
