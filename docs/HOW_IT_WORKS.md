# How it works: a guided tour of the code

Read this with the code open. It follows one incident from the fault to the verified fix.

## 1. The shop (`shoplite/`)

- **`services.py`** holds three FastAPI apps, each run as its own process by `run.py`:
  - **gateway** (port 9100): the public API (`/products`, `/checkout`). It calls orders.
  - **orders** (9101): owns the database (SQLite) and calls payments for checkouts.
  - **payments** (9102): a stand-in for an external payment provider.
- **`state.py`** is the platform's memory, in one JSON file: which version each service runs, feature flags, injected faults, and the deploy history. Services re-read it on every request, so a rollback takes effect immediately, like a real deploy system.
- **The bugs are real code paths:**
  - `orders 2.4.0` really raises `KeyError: 'discount_code'` on coupon checkouts (`_discount`).
  - `gateway 1.5.0` really fails to parse version headers like `7.2-beta` (`_parse_client_version`).
- **Faults are injected where they'd happen:**
  - `db_down` makes the database layer refuse connections.
  - `pool_leak` exhausts the connection pool.
  - `payments_slow` delays the provider past the orders timeout.
- **`telemetry.py`** writes one JSON log line per request and per error.
- **`monitor.py`** turns those lines into error rates and latency, and fires an alert when the gateway's error rate passes 5%.

## 2. An incident (`shoplite/chaos.py`)

`chaos.run(scenario, variant, seed)` does five things:
1. Resets everything and sends healthy traffic (the baseline).
2. Breaks one thing.
3. In decoy variants, deploys a harmless version just before or just after the break.
4. Sends more traffic.
5. Evaluates the alert.

It returns the **ground truth**: the true cause, the broken component, and the one fix that works. `still_broken()` checks after a fix whether the fault is really gone. That's how the eval proves verification never says "recovered" falsely.

## 3. The copilot (`copilot/`)

- **`observability.py`** is the read side: before-vs-during stats per service, by endpoint and **by version** (the strongest deploy evidence), top error messages, log search, deploy history timed relative to the incident, and dependency health.
- **`tools.py`** wraps those as tools and defines `INSTRUCTIONS`, the investigation method: follow errors to the deepest failing service; blame a deploy only if it's to that service, its timing matches, and the errors appear only on the new version; check dependencies; use the runbook.
- **`actions.py`** is the safety core:
  - `propose()` refuses anything off the allowlist and records the refusal.
  - `approve()` is the **only** function that changes production, and no copilot tool calls it.
  - `verify()` requires at least 40 requests at ≤1% errors after the action runs.
- **`baseline.py`** is the rule-based copilot: simple checks in order (database → pool → payments timeouts → most recent deploy). It's deliberately naive about deploys, which is exactly what decoys exploit.
- **`agent.py`** runs Claude in a tool-use loop with the same tools, plus `final_report` for a structured diagnosis.

## 4. The front ends

- **`console/app.py`** starts ShopLite, sends background traffic (~6 requests a second), runs the alert loop, and serves the page. It's the only place a person approves actions.
- **`mcp_server/server.py`** exposes the same tools to Claude Desktop. `propose_action` is marked as not read-only; everything else is read-only. There's no approve tool.

## 5. The eval (`eval/run_eval.py`)

For each of 24 incidents:
1. Inject the fault.
2. Let a diagnoser investigate.
3. A stand-in person approves whatever it proposed.
4. Send 60 real requests.
5. Verify, and compare with the ground truth.

Approving everything is deliberate: a wrong proposal really runs, and verification has to catch it. That tests the whole safety loop, not just the diagnosis.

## 6. Design decisions (interview talking points)

1. **The AI proposes; a person approves.** The approval boundary is in code (`actions.approve` isn't reachable from any tool), not in a prompt asking the model to wait.
2. **Allowlist, not blocklist.** Six known-safe action types with valid targets. Anything else is refused and logged, including made-up actions like `drop_database`.
3. **Verify on real traffic, with a big enough sample.** An action isn't done when it runs, only when 40+ real requests succeed. Building the console exposed the same mistake twice, both caused by a bug that only hits 12% of requests (coupon checkouts):
    - verification used 20 requests at 2%, and showed a failed fix as "recovered";
    - the "already recovered?" check looked at the latest 20 requests, saw no errors, and proposed doing nothing.

    Both now use larger samples and stricter thresholds, and the eval checks verification against the ground truth.
4. **Decoys make the eval honest.** Without nearby harmless deploys, "roll back the last deploy" looks perfect. With them, the baseline drops to 14 of 18.
5. **A baseline to beat.** The AI copilot is only worth its cost if it beats 20/24 from simple rules, and the eval measures exactly that.
6. **Ground truth for every check.** `still_broken()` lets the eval prove that verification never lies, not just that it usually agrees.

## 7. Exercises

1. **Fix the baseline's weakness:** make `baseline.py` roll back the deploy whose service has the errors and whose version shows them (`during_by_version`). Re-run the eval. Does it reach 24/24?
2. **Add a scenario:** a bad `payments 3.2.0` that returns 500s. Add it to `state.VERSIONS`, `chaos.SCENARIOS` and `still_broken()`. What should the fix be, given that payments is an external provider?
3. **Loosen verification on purpose:** set `MIN_VERIFY_REQUESTS = 10` and `RECOVERED_ERROR_RATE = 0.05`. Run the eval. Does the false-recovery count stay at 0?
4. **Try Claude Desktop** on the decoy scenario and compare its reasoning with the rules.
5. **With an API key,** run the AI eval and look at its misses. Change one line in `INSTRUCTIONS` and run it again.
