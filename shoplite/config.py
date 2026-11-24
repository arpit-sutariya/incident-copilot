"""Settings for ShopLite, the small shop the copilot looks after."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = Path(os.getenv("SHOPLITE_RUNTIME", ROOT / "runtime"))
STATE_FILE = RUNTIME / "state.json"
LOG_DIR = RUNTIME / "logs"
DB_FILE = RUNTIME / "shop.db"
ALERT_FILE = RUNTIME / "alert.json"
ACTIONS_FILE = RUNTIME / "actions.json"

SERVICES = ["gateway", "orders", "payments"]
PORTS = {"gateway": int(os.getenv("GATEWAY_PORT", 9100)),
         "orders": int(os.getenv("ORDERS_PORT", 9101)),
         "payments": int(os.getenv("PAYMENTS_PORT", 9102))}

# Everything time-based is multiplied by this. 1.0 for the live demo; the eval uses 0.1 so an incident
# takes seconds instead of minutes. Ratios (timeouts vs latency) stay the same.
TIME_SCALE = float(os.getenv("SHOPLITE_TIME_SCALE", "1.0"))

GATEWAY_TIMEOUT_S = 3.0      # gateway -> orders
PAYMENTS_TIMEOUT_S = 1.0     # orders -> payments
PAYMENTS_NORMAL_LATENCY_S = 0.05
PAYMENTS_SLOW_LATENCY_S = 2.5


def url(service: str) -> str:
    return f"http://127.0.0.1:{PORTS[service]}"
