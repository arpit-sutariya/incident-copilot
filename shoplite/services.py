"""ShopLite's three services. Each runs as its own process:

    uvicorn shoplite.services:gateway  --port 9100   public API: /products, /checkout
    uvicorn shoplite.services:orders   --port 9101   catalog + orders, owns the database
    uvicorn shoplite.services:payments --port 9102   stand-in for an external payment provider

(`python -m shoplite.run` starts all three.) Each request re-reads the platform state, so deploys,
rollbacks, flags and injected faults take effect immediately. The bugs are real code paths in the
"bad" versions, so the errors, stack traces and logs are genuine.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shoplite import state, telemetry
from shoplite.config import (DB_FILE, GATEWAY_TIMEOUT_S, PAYMENTS_NORMAL_LATENCY_S, PAYMENTS_SLOW_LATENCY_S,
                             PAYMENTS_TIMEOUT_S, TIME_SCALE, url)

PRODUCTS = [("HB-1001", "Trash liners, case", 38.5), ("HB-1002", "Floor cleaner", 27.8),
            ("HB-1003", "Safety glasses", 4.1), ("HB-1004", "Copy paper, case", 44.99),
            ("HB-1005", "LED panel light", 62.75), ("HB-1006", "Stretch wrap roll", 22.4)]
POOL_SIZE = 10


class Cart(BaseModel):
    items: list[dict]          # [{"sku": "HB-1001", "qty": 2}]
    coupon: str | None = None


def _upstream_error(service: str, target: str, request: Request, exc: Exception | None, status: int | None):
    trace = getattr(request.state, "trace_id", None)
    if exc is not None:
        telemetry.log(service, "ERROR", f"TimeoutError calling {target}: no response within the timeout",
                      trace_id=trace, error_type="UpstreamTimeout", upstream=target, path=request.url.path)
        return JSONResponse({"error": f"{target} timed out"}, status_code=504)
    telemetry.log(service, "ERROR", f"Upstream {target} returned {status}", trace_id=trace,
                  error_type="UpstreamError", upstream=target, upstream_status=status, path=request.url.path)
    return JSONResponse({"error": f"{target} returned {status}"}, status_code=502)


# ============================================================================ gateway
gateway = FastAPI(title="ShopLite gateway")
telemetry.instrument(gateway, "gateway")
_gw_http = httpx.Client()


def _parse_client_version(header: str) -> tuple[int, int]:
    """Added in gateway 1.5.0. Breaks on pre-release clients like '7.2-beta' (the bug)."""
    major, minor = header.split(".")[:2]
    return int(major), int(minor)


def _gateway_checks(request: Request):
    s = state.load()
    if state.version_bug(s, "gateway") == "client_header":
        _parse_client_version(request.headers.get("x-client-version", "7.2"))


def _call_orders(request: Request, method: str, path: str, **kw):
    headers = {"x-trace-id": request.state.trace_id}
    try:
        r = _gw_http.request(method, url("orders") + path, headers=headers,
                             timeout=GATEWAY_TIMEOUT_S * TIME_SCALE, **kw)
    except httpx.HTTPError as e:
        return None, _upstream_error("gateway", "orders", request, e, None)
    if r.status_code >= 500:
        return None, _upstream_error("gateway", "orders", request, None, r.status_code)
    return r, None


@gateway.get("/products")
def products(request: Request):
    _gateway_checks(request)
    r, err = _call_orders(request, "GET", "/catalog")
    return err or r.json()


@gateway.post("/checkout")
def checkout(cart: Cart, request: Request):
    _gateway_checks(request)
    r, err = _call_orders(request, "POST", "/orders", json=cart.model_dump())
    return err or JSONResponse(r.json(), status_code=r.status_code)


@gateway.get("/_health")
def gateway_health():
    return {"ok": True, "version": state.load()["services"]["gateway"]["version"]}


# ============================================================================ orders
@asynccontextmanager
async def _orders_lifespan(app):
    init_db()
    yield


orders = FastAPI(title="ShopLite orders", lifespan=_orders_lifespan)
telemetry.instrument(orders, "orders")
_orders_http = httpx.Client()
_pool = {"in_use": 0, "seen_restarts": None}
_pool_lock = threading.Lock()


class DatabaseUnavailable(Exception):
    pass


class PoolExhausted(Exception):
    pass


def init_db():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_FILE)
    con.executescript("""CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, name TEXT, price REAL);
                         CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,
                           total REAL, items INTEGER, payment TEXT);""")
    con.executemany("INSERT OR IGNORE INTO products VALUES (?,?,?)", PRODUCTS)
    con.commit()
    con.close()


def _db(s: dict):
    """Get a database connection, the way a real pool would. Faults show up here."""
    if s["faults"]["db_down"]:
        raise DatabaseUnavailable("could not connect to server: Connection refused. Is the server running on "
                                  "host 'shop-db' and accepting TCP/IP connections on port 5432?")
    with _pool_lock:
        restarts = s["services"]["orders"]["restarts"]
        if _pool["seen_restarts"] != restarts:   # the process was "restarted": fresh pool
            _pool.update(in_use=0, seen_restarts=restarts)
        if s["faults"]["pool_leak"]:
            _pool["in_use"] = min(POOL_SIZE, _pool["in_use"] + 1)   # a code path that never returns its connection
            if _pool["in_use"] >= POOL_SIZE:
                raise PoolExhausted(f"QueuePool limit of size {POOL_SIZE} reached, connection timed out "
                                    f"({_pool['in_use']} checked out)")
    return sqlite3.connect(DB_FILE, timeout=5)


@orders.exception_handler(DatabaseUnavailable)
@orders.exception_handler(PoolExhausted)
def _db_error(request: Request, exc: Exception):
    telemetry.log("orders", "ERROR", f"{type(exc).__name__}: {exc}", trace_id=getattr(request.state, "trace_id", None),
                  error_type=type(exc).__name__, path=request.url.path)
    return JSONResponse({"error": str(exc)}, status_code=503)


@orders.get("/catalog")
def catalog():
    s = state.load()
    con = _db(s)
    rows = con.execute("SELECT sku, name, price FROM products").fetchall()
    con.close()
    return [{"sku": r[0], "name": r[1], "price": r[2]} for r in rows]


def _discount(cart: Cart, s: dict) -> float:
    if state.version_bug(s, "orders") == "coupon_keyerror" and cart.coupon:
        # orders 2.4.0 looks the coupon up in a dict that has no 'discount_code' key for most coupons
        promo = {"code": cart.coupon, "percent": 10}
        return promo["discount_code"] and 0.1  # KeyError: 'discount_code'
    return 0.1 if cart.coupon == "WELCOME10" else 0.0


@orders.post("/orders")
def create_order(cart: Cart, request: Request):
    s = state.load()
    con = _db(s)
    prices = dict(con.execute("SELECT sku, price FROM products").fetchall())
    total = sum(prices.get(i["sku"], 0) * i.get("qty", 1) for i in cart.items)
    total = round(total * (1 - _discount(cart, s)), 2)
    payment = "charged"
    if s["flags"]["payments_fallback"]:
        payment = "queued"  # confirm now, charge later from a queue
    else:
        try:
            r = _orders_http.post(url("payments") + "/charge", json={"amount": total},
                                  headers={"x-trace-id": request.state.trace_id},
                                  timeout=PAYMENTS_TIMEOUT_S * TIME_SCALE)
            if r.status_code >= 500:
                con.close()
                return _upstream_error("orders", "payments", request, None, r.status_code)
        except httpx.HTTPError as e:
            con.close()
            return _upstream_error("orders", "payments", request, e, None)
    con.execute("INSERT INTO orders (ts, total, items, payment) VALUES (?,?,?,?)",
                (time.time(), total, len(cart.items), payment))
    con.commit()
    con.close()
    return {"status": "confirmed", "total": total, "payment": payment}


@orders.get("/_health")
def orders_health():
    s = state.load()
    try:
        _db(s).close()
        db = "ok"
    except Exception as e:
        db = f"error: {e}"
    return {"ok": db == "ok", "version": s["services"]["orders"]["version"], "database": db}


# ============================================================================ payments
payments = FastAPI(title="ShopLite payments (external provider stand-in)")
telemetry.instrument(payments, "payments")


@payments.post("/charge")
def charge(body: dict):
    s = state.load()
    delay = PAYMENTS_SLOW_LATENCY_S if s["faults"]["payments_slow"] else PAYMENTS_NORMAL_LATENCY_S
    time.sleep(delay * TIME_SCALE)
    return {"status": "approved", "amount": body.get("amount")}


@payments.get("/_health")
def payments_health():
    start = time.perf_counter()
    s = state.load()
    delay = PAYMENTS_SLOW_LATENCY_S if s["faults"]["payments_slow"] else PAYMENTS_NORMAL_LATENCY_S
    time.sleep(delay * TIME_SCALE)
    return {"ok": True, "latency_ms": round((time.perf_counter() - start) * 1000, 1)}
