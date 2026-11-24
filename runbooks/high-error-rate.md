# Runbook: high error rate at the gateway

The gateway is what customers hit. Its errors usually start somewhere behind it.

1. Find where errors start: compare error rates for gateway, orders and payments before vs during the incident. The deepest service with new errors is the likely origin.
2. Read that service's top error messages. The exception type usually names the problem.
3. Check what changed: deploys, flag changes and config changes around the incident start. A change only matters if it touches the service that's failing and its timing matches the first errors.
4. Check dependencies: database connectivity and payment-provider latency.
5. Pick the runbook for the cause you found. If the errors have already stopped, don't act: write it up instead.
