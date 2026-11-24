# Incident eval: oracle (harness self-check)

| Metric | Result |
| --- | --- |
| Correct cause and component | 24/24 |
| Right recovery action proposed | 24/24 |
| Recovered after one approved action | 24/24 |
| Right action when a decoy deploy was nearby | 18/18 |
| Wrong fixes caught by verification (service still failing) | 0 |
| Actions executed without approval | 0 |
| Proposals refused (not on the allowlist) | 0 |
| Avg tool calls per investigation | 1.0 |
| Avg investigation time | 0.0 s |

| Scenario | Fixed |
| --- | --- |
| bad_deploy_orders | 4/4 |
| bad_deploy_gateway | 4/4 |
| database_outage | 4/4 |
| slow_dependency | 4/4 |
| pool_exhaustion | 4/4 |
| false_alarm | 4/4 |
