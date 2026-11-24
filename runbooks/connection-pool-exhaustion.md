# Runbook: connection pool exhaustion in orders

Signs: orders logs `QueuePool limit of size 10 reached`; the error rate climbs to almost 100% as connections leak; the database itself is healthy.

- Fix: `restart_service orders` to release the leaked connections.
- Then find the leak: it will come back if the code path that doesn't return its connection is still running.
- Restarting the database doesn't help; the leak is in the application.
