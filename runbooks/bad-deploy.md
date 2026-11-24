# Runbook: errors after a deploy

Signs: new exceptions in one service, first seen right after that service was deployed, and only on the new version (check error rate by version).

- Fix: `rollback_deploy` on the service that was deployed. Roll back only the service whose errors started with its deploy.
- Don't roll back unrelated services that happened to deploy nearby; that adds risk and doesn't help.
- Afterwards: confirm the error rate is back to baseline, then open a bug for the failing code path.
