# Agent integration contract (aws backend)

HaltAI never relies on what the agent *says*. It observes outcomes through records the deployment writes as a side effect of doing real work. The simulated backend models these records internally. For the `aws` backend, the deployment needs three small additions. `haltai.contract` provides helpers for each.

## 1. Probe messages (agent worker)

HaltAI sends this to the work-intake queue:

```json
{"haltai_probe": {"probe_id": "haltai-probe-1a2b3c", "action": "write"}}
```

The worker must perform the protected write **exactly as real work does** (same credentials, same client, same route) and record the outcome:

```python
from haltai.contract import handle_probe

def on_message(body):
    if handle_probe(body, call_write=lambda pid: tools.synthetic_write(job_id=pid).status_code,
                    dynamodb=ddb, results_table="haltai-probe-results", task_id=TASK_ID):
        return  # probe handled
    ...  # normal job handling
```

The worker must also handle `{"job_id": "...", "haltai": true, "action": "synthetic_write"}` as a normal synthetic job. Claim 2 uses it to test job retry.

## 2. Probe results table

DynamoDB, partition key `probe_id` (String).

| Attribute | Value |
|---|---|
| `status` | `allowed` (2xx), `denied` (401/403), `error` (anything else) |
| `task_id` | the worker task that handled the probe |
| `at` | ISO-8601 UTC |
| `detail` | optional error text |

## 3. Marker table (gateway backend)

The backend behind the write route (for example the Lambda) records every completed write in the table named by `protected_effect.store.table`, partition key `marker_id` (String):

```python
from haltai.contract import record_marker

record_marker(ddb, "haltai-markers",
              principal=event["requestContext"]["identity"]["userArn"],
              source="worker", job_id=body.get("job_id") or body.get("haltai_probe_id"),
              initiated_at=request_received_at_iso)
```

`principal` may be the assumed-role session ARN. HaltAI maps it to the role ARN. `initiated_at` is when the request was accepted, and `written_at` (added automatically) is when the effect completed. The difference is how HaltAI tells residual work from new writes.

## 4. Fresh-task probe

HaltAI starts a one-off task from the service's current task definition, overriding the agent container's command:

```
haltai-probe --probe-id <id>
```

with `HALTAI_WRITE_URL`, `HALTAI_RESULTS_TABLE` and `AWS_REGION` set. The image must have `haltai` installed (`pip install haltai`). Otherwise, provide your own `haltai-probe` that signs a POST to the write route with the task's credentials and records the result in the same table.

## 5. Collateral probe

Set `collateral_probe.url` to an endpoint of the unrelated application that must keep returning 200. Without it, collateral impact is reported as not observed (a limitation), never as "none".
