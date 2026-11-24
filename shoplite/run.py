"""Start ShopLite's three services as separate processes.

    python -m shoplite.run          # Ctrl+C stops them
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import httpx

from shoplite import state
from shoplite.config import LOG_DIR, PORTS, ROOT, RUNTIME, url


class Cluster:
    """Starts and stops the three service processes. Used by the CLI, the console, the eval and the tests."""

    def __init__(self, time_scale: float | None = None, quiet: bool = True):
        self.procs: list[subprocess.Popen] = []
        self.env = dict(os.environ)
        if time_scale is not None:
            self.env["SHOPLITE_TIME_SCALE"] = str(time_scale)
        self.quiet = quiet

    def start(self, timeout_s: float = 20):
        busy = [f"{svc} ({port})" for svc, port in PORTS.items() if _port_in_use(port)]
        if busy:
            raise RuntimeError(f"Port already in use: {', '.join(busy)}. Another ShopLite (or console) is probably "
                               "still running. Stop it first, or kill the leftover 'uvicorn shoplite.services' processes.")
        RUNTIME.mkdir(parents=True, exist_ok=True)
        fresh_start()
        for svc, port in PORTS.items():
            out = subprocess.DEVNULL if self.quiet else None
            self.procs.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", f"shoplite.services:{svc}", "--port", str(port),
                 "--log-level", "warning", "--workers", "1"], cwd=ROOT, env=self.env, stdout=out, stderr=out))
        deadline = time.time() + timeout_s
        for svc in PORTS:
            while True:
                try:
                    if httpx.get(url(svc) + "/_health", timeout=2).status_code < 500:
                        break
                except httpx.HTTPError:
                    pass
                if time.time() > deadline:
                    self.stop()
                    raise RuntimeError(f"{svc} didn't start on port {PORTS[svc]} (is something else using it?)")
                time.sleep(0.2)
        return self

    def stop(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs = []

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def fresh_start():
    """Baseline versions, no faults, empty logs: the state before an incident."""
    from shoplite.services import init_db
    state.reset()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for f in LOG_DIR.glob("*.jsonl"):
        f.unlink()
    for name in ("alert.json", "actions.json"):
        (RUNTIME / name).unlink(missing_ok=True)
    init_db()


if __name__ == "__main__":
    import signal
    c = Cluster(quiet=False).start()

    def _shutdown(signum, frame):
        c.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    print("ShopLite running: " + ", ".join(f"{s} {url(s)}" for s in PORTS) + "  (Ctrl+C to stop)")
    while True:
        time.sleep(1)
