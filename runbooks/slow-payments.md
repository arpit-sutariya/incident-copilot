# Runbook: slow payment provider

Signs: checkout fails with timeouts calling payments while browsing works; the payment provider's health check is much slower than the orders payment timeout.

- Payments is an external provider: we can't restart or roll it back.
- Fix: `enable_flag payments_fallback`. Orders are confirmed immediately and payments are charged later from a queue.
- Turn the flag off again (`disable_flag`) once the provider's latency is back to normal.
