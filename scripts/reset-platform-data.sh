#!/usr/bin/env bash
# Reset the platform to a pristine state: wipe ALL project/mock data across
# every store, keeping ONLY the platform login users (Postgres `users` +
# Keycloak accounts are untouched). Safe to re-run any time.
#
#   ./scripts/reset-platform-data.sh
#
# Wipes: projects, members, sessions, chat, artifacts, workflows, codebase
# uploads, KB documents (standards reseed at boot), audit index, LLM traces
# (Postgres) · phase states + build trackers (DynamoDB) · caches/breakers/
# sessions (Redis) · generated files (content-store volume) · audit objects
# (LocalStack S3, bucket recreated).
set -euo pipefail

PG=ai-sdlc-postgres-1
ORCH=ai-sdlc-orchestrator-1
REDIS=ai-sdlc-redis-1
LOCALSTACK=ai-sdlc-localstack-1

echo "[1/6] Postgres: truncating all data tables (keeping users + schema_migrations)"
docker exec "$PG" psql -U sdlc -d sdlc -q -c "
  TRUNCATE TABLE artefacts, audit_index, chat_messages, codebase_files,
                 kb_documents, llm_traces, project_members, project_workflows,
                 projects, sessions
  RESTART IDENTITY CASCADE;"

echo "[2/6] DynamoDB: clearing PhaseState + BuildRecoveryTracker"
docker exec "$ORCH" python - <<'PY'
import boto3, os
c = boto3.client("dynamodb", endpoint_url=os.environ["DYNAMO_ENDPOINT"],
                 region_name=os.environ.get("AWS_REGION", "eu-west-2"))
for table in ("PhaseState", "BuildRecoveryTracker"):
    keys = [k["AttributeName"] for k in c.describe_table(TableName=table)["Table"]["KeySchema"]]
    deleted, resp = 0, c.scan(TableName=table, ProjectionExpression=", ".join(f"#k{i}" for i in range(len(keys))),
                              ExpressionAttributeNames={f"#k{i}": k for i, k in enumerate(keys)})
    while True:
        for item in resp["Items"]:
            c.delete_item(TableName=table, Key={k: item[k] for k in keys})
            deleted += 1
        if "LastEvaluatedKey" not in resp:
            break
        resp = c.scan(TableName=table, ExclusiveStartKey=resp["LastEvaluatedKey"])
    print(f"  {table}: {deleted} items deleted")
PY

echo "[3/6] Redis: FLUSHALL (sessions, caches, breakers, rate limits)"
docker exec "$REDIS" redis-cli FLUSHALL >/dev/null

echo "[4/6] Content store: removing generated files"
docker exec -u root "$ORCH" sh -c 'rm -rf /data/content-store/* 2>/dev/null || true; ls /data/content-store | wc -l'

echo "[5/6] LocalStack: recreating the audit bucket"
docker exec "$LOCALSTACK" sh -c \
  'awslocal s3 rb "s3://${AUDIT_BUCKET:-sdlc-audit-logs}" --force >/dev/null 2>&1 || true;
   awslocal s3 mb "s3://${AUDIT_BUCKET:-sdlc-audit-logs}" >/dev/null && echo "  bucket ready"'

echo "[6/6] Restarting orchestrator (reseeds KB standards, clears in-memory state)"
docker restart "$ORCH" >/dev/null
for i in $(seq 1 30); do
  sleep 2
  docker exec "$ORCH" python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/healthz')" 2>/dev/null && break
done

echo ""
echo "--- verification ---"
docker exec "$PG" psql -U sdlc -d sdlc -t -c "
  SELECT 'users kept: ' || count(*) FROM users
  UNION ALL SELECT 'projects: ' || count(*) FROM projects
  UNION ALL SELECT 'artefacts: ' || count(*) FROM artefacts
  UNION ALL SELECT 'chat_messages: ' || count(*) FROM chat_messages
  UNION ALL SELECT 'llm_traces: ' || count(*) FROM llm_traces
  UNION ALL SELECT 'kb standards (reseeded): ' || count(*) FROM kb_documents;"
echo "RESET COMPLETE ✅ — platform is blank; login users preserved"
