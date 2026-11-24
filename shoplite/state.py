"""The platform's state: which version each service runs, feature flags, injected faults and the deploy
history. Stands in for what Kubernetes, a feature-flag service and a CI/CD system would hold.

Stored in one JSON file so the separate service processes, the console and the copilot all see the same
truth. Services re-read it on every request, so a rollback or a flag change takes effect immediately.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager

from shoplite.config import RUNTIME, STATE_FILE

try:
    import fcntl
except ImportError:  # Windows: no file locking, fine for a single-user demo
    fcntl = None

# Every version that can be deployed, what changed, and the bug it carries (if any).
VERSIONS = {
    "gateway": {
        "1.4.2": {"summary": "Baseline release", "bug": None},
        "1.4.3": {"summary": "Update footer copy and analytics tag", "bug": None},
        "1.5.0": {"summary": "Parse X-Client-Version header for mobile feature gating", "bug": "client_header"},
    },
    "orders": {
        "2.3.0": {"summary": "Baseline release", "bug": None},
        "2.3.1": {"summary": "Bump sqlalchemy to 2.0.36", "bug": None},
        "2.4.0": {"summary": "Apply coupon discounts at checkout", "bug": "coupon_keyerror"},
    },
    "payments": {
        "3.1.0": {"summary": "Baseline release", "bug": None},
        "3.1.1": {"summary": "Reduce log verbosity for card tokens", "bug": None},
    },
}
BASELINE = {"gateway": "1.4.2", "orders": "2.3.0", "payments": "3.1.0"}
FLAGS = {"payments_fallback": "Queue payments and confirm the order without waiting for the payment provider"}


def default_state() -> dict:
    return {
        "generation": 0,
        "services": {s: {"version": v, "restarts": 0} for s, v in BASELINE.items()},
        "flags": {f: False for f in FLAGS},
        "faults": {"db_down": False, "payments_slow": False, "pool_leak": False},
        "database": {"restarts": 0},
        "deploys": [],
    }


@contextmanager
def _locked(mode: str):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME / ".state.lock"
    with open(lock_path, "a+") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX if mode == "w" else fcntl.LOCK_SH)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_UN)


def load() -> dict:
    with _locked("r"):
        if not STATE_FILE.exists():
            return default_state()
        return json.loads(STATE_FILE.read_text())


@contextmanager
def edit():
    """with edit() as s: ...  -- read, change and write atomically."""
    with _locked("w"):
        s = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else default_state()
        yield s
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1))
        os.replace(tmp, STATE_FILE)


def reset():
    with edit() as s:
        gen = s.get("generation", 0) + 1
        s.clear()
        s.update(default_state())
        s["generation"] = gen


# ----------------------------------------------------------------------------- operations
def deploy(service: str, version: str, author: str = "ci-bot") -> dict:
    if version not in VERSIONS[service]:
        raise ValueError(f"{service} has no version {version}")
    with edit() as s:
        before = s["services"][service]["version"]
        s["services"][service]["version"] = version
        rec = {"id": len(s["deploys"]) + 1, "service": service, "from_version": before, "to_version": version,
               "at": time.time(), "author": author, "summary": VERSIONS[service][version]["summary"],
               "kind": "deploy"}
        s["deploys"].append(rec)
    return rec


def rollback(service: str, author: str) -> dict:
    """Go back to the version before the most recent deploy of this service."""
    with edit() as s:
        mine = [d for d in s["deploys"] if d["service"] == service]
        if not mine:
            raise ValueError(f"{service} has no deploy to roll back")
        last = mine[-1]
        s["services"][service]["version"] = last["from_version"]
        rec = {"id": len(s["deploys"]) + 1, "service": service, "from_version": last["to_version"],
               "to_version": last["from_version"], "at": time.time(), "author": author,
               "summary": f"Rollback of deploy #{last['id']}", "kind": "rollback"}
        s["deploys"].append(rec)
    return rec


def restart_service(service: str) -> None:
    with edit() as s:
        s["services"][service]["restarts"] += 1
        if service == "orders":
            s["faults"]["pool_leak"] = False  # a restart frees the leaked connections


def restart_database() -> None:
    with edit() as s:
        s["database"]["restarts"] += 1
        s["faults"]["db_down"] = False


def set_flag(name: str, value: bool) -> None:
    if name not in FLAGS:
        raise ValueError(f"Unknown flag {name}")
    with edit() as s:
        s["flags"][name] = value


def set_fault(name: str, value: bool) -> None:
    with edit() as s:
        s["faults"][name] = value


def version_bug(s: dict, service: str) -> str | None:
    return VERSIONS[service][s["services"][service]["version"]]["bug"]
