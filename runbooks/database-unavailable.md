# Runbook: database unavailable

Signs: orders logs `could not connect to server: Connection refused`; both browsing and checkout fail; the orders health check reports the database as down.

- Fix: `restart_database` (fails over to a healthy replica).
- Not this runbook if the error is `QueuePool limit ... reached`: the database is fine and the orders service has run out of connections (see connection-pool-exhaustion).
- Rolling back application deploys doesn't help with a database outage.
