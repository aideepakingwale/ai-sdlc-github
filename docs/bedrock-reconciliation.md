# Reconciling token usage with AWS Bedrock

The token ledger (`llm_usage_events`, see [token-governance.md](token-governance.md)) records what the gateway saw. Bedrock
itself logs every call it served. Comparing the two each night answers: *is the ledger right?* It catches calls the ledger missed
(a crashed worker, a stream that died before reporting usage), calls the ledger counted that AWS has no record of, and counts
that differ by a few tokens.

Nothing here changes how calls are made or counted. With no log source configured, reconciliation is simply off.

## What is compared

For one completed UTC day, every Bedrock call in the ledger is matched to the Bedrock invocation log record with the same
**request id** (the ledger keeps Bedrock's `x-amzn-requestid` as `provider_request_id`; the log's `requestId` is the same id).

| Result | Meaning |
|---|---|
| matched | Same call, same token total on both sides |
| different counts | Same call, different totals: a bug in how usage is read, or an estimated stream |
| in AWS only | AWS served a call the ledger never recorded (lost write, another app in the account, a gateway bypass) |
| in the ledger only | The ledger has a call AWS has no record of (logging gap, or a call newer than ~2 hours that has not been delivered yet) |
| ledger rows with no request id | Cannot be proven either way; counted in the ledger total |

The day is **ok** when the ledger and AWS totals are within the allowed difference (default 2%), **differs** when they are not,
**no AWS log** when AWS returned nothing for the day (logging off, wrong group, not delivered yet) and **could not run** when the
source failed (for example missing permission). A day with no log or an error is tried again after six hours, up to three
days back, so a late delivery or a fixed permission heals itself.

**How the log counts input is worked out, not assumed.** The log's `inputTokenCount` could be the uncached part of the prompt
or everything the model read. For each matched call the comparison tries both readings and uses the one that explains more
calls; the result records which. If nothing in the day used the prompt cache the two readings are indistinguishable and the
result says so. Output tokens are unambiguous.

Calls are assigned to a day by their own timestamp, and a one-hour margin is fetched either side of midnight, so a call near
midnight is neither dropped nor counted twice. A request id appearing twice in the log counts once.

Only token counts are read. Prompts and answers in the log are never kept.

## Turning it on

1. **Enable Bedrock model invocation logging** (account-wide, per region) to CloudWatch Logs or S3. `scripts/bedrock-usage-logging.sh`
   prints the AWS CLI commands (it only runs them with `APPLY=1`); it also creates the log group, the role Bedrock writes with,
   and an optional read policy for the orchestrator. Invocation logging applies to **every** application calling Bedrock in that
   region and replaces an existing logging configuration, so read the output first.
2. **Give the orchestrator read access:** `logs:FilterLogEvents` on the group (CloudWatch), or `s3:ListBucket` and `s3:GetObject`
   (S3). It uses the normal AWS credential chain (the task/instance role), never the Dynamo dummy keys.
3. **Configure the orchestrator** and restart it:

| Variable | Meaning |
|---|---|
| `BEDROCK_RECON_SOURCE` | `cloudwatch`, `s3` or `file` (empty = off) |
| `BEDROCK_RECON_LOG_GROUP` | the CloudWatch log group (cloudwatch) |
| `BEDROCK_RECON_S3_BUCKET`, `BEDROCK_RECON_S3_PREFIX` | the bucket and the key prefix invocation logging writes under (s3) |
| `BEDROCK_RECON_FILE_DIR` | a directory of exported `.json`/`.jsonl`/`.gz` logs (file: offline checks and local testing) |
| `BEDROCK_RECON_REGION` | defaults to `BEDROCK_REGION`, then `AWS_REGION` |
| `BEDROCK_RECON_THRESHOLD` | allowed `(ledger - aws) / aws`, default `0.02` |
| `BEDROCK_RECON_HOUR_UTC` | the previous day is reconciled after this hour, default `3` (delivery delay) |
| `BEDROCK_RECON_IDENTITY_FILTER` | count only AWS calls whose caller ARN contains this text: **set it if other applications use the same account and region**, otherwise their calls show as "in AWS only" |

Several orchestrator replicas are fine: one takes a short Redis lock, and a day's result is replaced atomically.

## Where to see it

* **Usage page, organisation view (administrators):** "Checked against AWS": the last 14 days, a result per day, and the
  calls behind a difference (AWS request id, work, ledger and AWS tokens). "Compare yesterday now" re-runs a day.
* **API:** `GET /api/usage/reconciliation`, `GET /api/usage/reconciliation/{day}`, `POST /api/usage/reconciliation/run {"day": "YYYY-MM-DD"}` (administrators).
* **Metrics:** `sdlc_token_ledger_drift` and `sdlc_token_ledger_drift_alert` on `/metrics`; the orchestrator also logs
  `TOKEN LEDGER DRIFT` at warning level. Alert on either.

## Reading a drift

| You see | Likely cause |
|---|---|
| Mostly "in AWS only", unknown request ids | Another application in the account (set the identity filter), or a path that bypasses the gateway |
| "In AWS only" for DevMind's own role | Ledger writes failing: search the orchestrator log for `usage ledger write FAILED` |
| "In the ledger only" for a whole stretch of time | Logging was off or delivery was delayed; re-run the day later |
| "Different counts" on calls using the prompt cache | The cache fields are read wrongly; compare one request in the AWS console with its ledger row |
| "Different counts" with a few tokens each | Estimated streams (ledger rows marked `estimated`) are excluded from exact matching; check `estimated_pairs` |
| `no AWS log` every day | Logging not enabled in this region, wrong group name, or the orchestrator role cannot read it |

## Prompts in the log, and what to enable

Invocation logging can also deliver the full prompts and answers. The comparison only needs counts, and prompts can contain
confidential project material, so the script defaults to **not** delivering text (`TEXT_DELIVERY=false`). The parser reads the
counts from the record's own fields and, if a record has none, from the usage block of a logged response body. **Confirm on a
real log that the count fields are present with text delivery off** (the field layout was taken from AWS-adjacent sources, not
a live account); if they are not, either enable text delivery with a short retention and encryption on the log group, or the day
will show as "no AWS log"/unreadable and the note will say how many records could not be read.

## Application inference profiles and cost tags

For AWS-side money per environment (and per project, if you create one profile per project), create an **application inference
profile** with tags and point `BEDROCK_MODEL_ID` at its ARN; activate the tag keys as cost-allocation tags in Billing. That
reconciles *money* in Cost Explorer; the ledger stays the source of per-person and per-activity *tokens*. The script's step 5
shows the call. A profile per project would need the gateway to pick the profile from the project and is not built.

## Checking it on a machine with no AWS

`BEDROCK_RECON_SOURCE=file` with a directory of exported logs runs the whole path (matching, storage, API, page). The tests
use realistic invocation log records, including the stream-event form of the response body.
