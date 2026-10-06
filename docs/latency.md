# Latency

Where a stage run spends its time, and the settings that change it.

## What a run does (in order)
1. **Plan** ("Review plan"): one structured planning call (`PLAN_MAX_TOKENS`), plus - on the
   first review - a clarification check and project-trait detection.
2. **Generate**: context is assembled (compressed past `CONTEXT_TOKEN_THRESHOLD`), then the
   stage's artifacts are generated in ONE call of up to `PHASE_MAX_TOKENS` output tokens
   (or one call per artifact, in parallel, with `PER_ARTIFACT_GENERATION=true`).
3. **Validate**: one judging call. If it flags errors the flagged artifacts are reworked.
4. **Fact-check**: one judging call on the result.

Output tokens dominate: generation time is roughly output tokens / the model's speed.

## What was changed
| Change | Effect |
|---|---|
| **Light calls use a fast model** - validator, fact-check, clarification, trait detection, context compression (`LIGHT_MODEL`); the plan proposal can use `PLAN_MODEL` | those calls drop from tens of seconds to a few. Unset = unchanged. A wrong id falls back to the normal chain and is bypassed for 5 minutes. |
| **Localised validator rework** (`VALIDATION_LOCALISED_REWORK`, default on) | when the validator names the artifacts at fault, only those are regenerated (the rest come from the first run's saved parts) instead of the whole stage. Issues that cannot be localised still trigger a full rework. |
| **No planner-node model call** | the execution plan is derived from the stage template; the plan you reviewed is what runs. One round trip removed from every run. |
| **Parallel context compression** | older artifacts are summarised concurrently (4 at a time) in the smallest batch expected to fit the budget, instead of one after another. |
| **`GENERATION_WORKERS` 2 -> 4** | more stage runs execute at once. Lower it if your model provider rate-limits (429s). |

## Setting it up
```bash
./deploy.sh --model-id <big-model> --light-model-id <fast-model> --region <r>
# or in .env:
LIGHT_MODEL=bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
```
`./deploy.sh` sets `CONTEXT_TOKEN_THRESHOLD=16000` by default, so compression only triggers on
large contexts there.

## Other levers (not changed)
* `PER_ARTIFACT_GENERATION=true` - parallel generation of a stage's artifacts; the largest remaining
  win. Validate output quality on your model before enabling.
* `ATTACHMENT_CONTEXT_CHARS` - attached documents add input tokens to every call; lower it to trade
  document coverage for speed.
* `PHASE_MAX_TOKENS` - a lower cap shortens the worst case but can truncate big artifacts.

## Measure it
Every model call is recorded with its latency:
```sql
SELECT tag, count(*), round(avg(latency_ms)/1000.0,1) AS avg_s, round(max(latency_ms)/1000.0,1) AS max_s,
       sum(completion_tokens) AS out_tokens
FROM llm_traces WHERE ts > now() - interval '1 day' AND kind='llm'
GROUP BY tag ORDER BY avg(latency_ms) * count(*) DESC;
```
