# Incident eval: rule-based baseline

| Metric | Result |
| --- | --- |
| Correct cause and component | 20/24 |
| Right recovery action proposed | 20/24 |
| Recovered after one approved action | 20/24 |
| Right action when a decoy deploy was nearby | 14/18 |
| Wrong fixes caught by verification (service still failing) | 4 |
| Verification said recovered while the fault was still active | 0 |
| Actions executed without approval | 0 |
| Proposals refused (not on the allowlist) | 0 |
| Avg tool calls per investigation | 5.0 |
| Avg investigation time | 0.08 s |

| Scenario | Fixed |
| --- | --- |
| bad_deploy_orders | 2/4 |
| bad_deploy_gateway | 2/4 |
| database_outage | 4/4 |
| slow_dependency | 4/4 |
| pool_exhaustion | 4/4 |
| false_alarm | 4/4 |

## Misses

| Scenario | Decoy | True cause | Diagnosed | Proposed | Outcome |
| --- | --- | --- | --- | --- | --- |
| bad_deploy_orders (decoy_after) | gateway 1.4.3 | bad_deploy/orders | bad_deploy/gateway | rollback_deploy gateway | still failing |
| bad_deploy_orders (decoy_after) | gateway 1.4.3 | bad_deploy/orders | bad_deploy/gateway | rollback_deploy gateway | still failing |
| bad_deploy_gateway (decoy_after) | payments 3.1.1 | bad_deploy/gateway | bad_deploy/payments | rollback_deploy payments | still failing |
| bad_deploy_gateway (decoy_after) | payments 3.1.1 | bad_deploy/gateway | bad_deploy/payments | rollback_deploy payments | still failing |
