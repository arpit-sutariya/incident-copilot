"""Synthetic customer traffic: browsing (70%) and checkouts (30%, 40% of them with a coupon)."""
from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor

import httpx

from shoplite.config import url

SKUS = ["HB-1001", "HB-1002", "HB-1003", "HB-1004", "HB-1005", "HB-1006"]
CLIENTS = ["7.2", "7.3", "7.3", "7.4", "7.2-beta", "8.0-rc1"]  # some mobile clients send pre-release versions


def one_request(rng: random.Random, client: httpx.Client) -> int:
    headers = {"x-client-version": rng.choice(CLIENTS)}
    try:
        if rng.random() < 0.7:
            r = client.get(url("gateway") + "/products", headers=headers, timeout=30)
        else:
            cart = {"items": [{"sku": rng.choice(SKUS), "qty": rng.randint(1, 4)} for _ in range(rng.randint(1, 3))],
                    "coupon": rng.choice(["SPRING15", "WELCOME10", "LOYAL5"]) if rng.random() < 0.4 else None}
            r = client.post(url("gateway") + "/checkout", json=cart, headers=headers, timeout=30)
        return r.status_code
    except httpx.HTTPError:
        return 599


def burst(n: int = 120, concurrency: int = 8, seed: int | None = None) -> dict:
    """Send n requests and return a status histogram."""
    rng = random.Random(seed)
    seeds = [rng.random() for _ in range(n)]
    with httpx.Client() as client, ThreadPoolExecutor(concurrency) as pool:
        codes = list(pool.map(lambda s: one_request(random.Random(s), client), seeds))
    hist: dict[int, int] = {}
    for c in codes:
        hist[c] = hist.get(c, 0) + 1
    return hist
