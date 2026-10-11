#!/usr/bin/env bash
# Set up what the nightly token reconciliation needs on the AWS side (docs/bedrock-reconciliation.md):
#   1. a CloudWatch log group for Bedrock model invocation logs
#   2. an IAM role Bedrock uses to write to it
#   3. the account's invocation-logging configuration pointing at that group
#   4. (optional) an IAM policy for the orchestrator to read the group
#   5. (optional) an application inference profile with cost-allocation tags
#
# It only PRINTS the aws commands unless you run it with APPLY=1. Invocation logging is an ACCOUNT-WIDE setting per
# region (it applies to every application calling Bedrock there), and replaces any logging configuration already in
# place, so read the output first. Not run by the project's automated tests: check the flags against `aws bedrock help`.
#
#   REGION=eu-west-2 LOG_GROUP=/devmind/bedrock/invocations scripts/bedrock-usage-logging.sh          # print
#   APPLY=1 REGION=eu-west-2 LOG_GROUP=/devmind/bedrock/invocations scripts/bedrock-usage-logging.sh  # do it
#
# Optional: RETENTION_DAYS (default 30), TEXT_DELIVERY (true|false, default false: see the guide), ORCHESTRATOR_ROLE
# (an existing role name to attach the read policy to), PROFILE_NAME + PROFILE_SOURCE_ARN (create a tagged inference profile).
set -euo pipefail

REGION="${REGION:?set REGION, e.g. eu-west-2}"
LOG_GROUP="${LOG_GROUP:-/devmind/bedrock/invocations}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
TEXT_DELIVERY="${TEXT_DELIVERY:-false}"
BEDROCK_LOG_ROLE="${BEDROCK_LOG_ROLE:-devmind-bedrock-invocation-logging}"
ACCOUNT_ID="${ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo 000000000000)}"
LOG_GROUP_ARN="arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:${LOG_GROUP}:*"

run() { echo "+ $*"; if [ "${APPLY:-0}" = "1" ]; then "$@"; fi; }

echo "== 1. log group (${LOG_GROUP}, kept ${RETENTION_DAYS} days)"
run aws logs create-log-group --region "$REGION" --log-group-name "$LOG_GROUP" || true
run aws logs put-retention-policy --region "$REGION" --log-group-name "$LOG_GROUP" --retention-in-days "$RETENTION_DAYS"

echo "== 2. role Bedrock assumes to write the logs"
TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock.amazonaws.com"},"Action":"sts:AssumeRole",
 "Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT_ID}"},"ArnLike":{"aws:SourceArn":"arn:aws:bedrock:${REGION}:${ACCOUNT_ID}:*"}}}]}
JSON
)
WRITE=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["logs:CreateLogStream","logs:PutLogEvents"],"Resource":"${LOG_GROUP_ARN}"}]}
JSON
)
run aws iam create-role --role-name "$BEDROCK_LOG_ROLE" --assume-role-policy-document "$TRUST" || true
run aws iam put-role-policy --role-name "$BEDROCK_LOG_ROLE" --policy-name write-invocation-logs --policy-document "$WRITE"

echo "== 3. invocation logging configuration (text bodies delivered: ${TEXT_DELIVERY})"
CONFIG=$(cat <<JSON
{"cloudWatchConfig":{"logGroupName":"${LOG_GROUP}","roleArn":"arn:aws:iam::${ACCOUNT_ID}:role/${BEDROCK_LOG_ROLE}"},
 "textDataDeliveryEnabled":${TEXT_DELIVERY},"imageDataDeliveryEnabled":false,"embeddingDataDeliveryEnabled":false}
JSON
)
run aws bedrock put-model-invocation-logging-configuration --region "$REGION" --logging-config "$CONFIG"

if [ -n "${ORCHESTRATOR_ROLE:-}" ]; then
  echo "== 4. let the orchestrator read the log group (role ${ORCHESTRATOR_ROLE})"
  READ=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["logs:FilterLogEvents","logs:GetLogEvents"],"Resource":"${LOG_GROUP_ARN}"}]}
JSON
)
  run aws iam put-role-policy --role-name "$ORCHESTRATOR_ROLE" --policy-name read-bedrock-invocation-logs --policy-document "$READ"
fi

if [ -n "${PROFILE_NAME:-}" ] && [ -n "${PROFILE_SOURCE_ARN:-}" ]; then
  echo "== 5. application inference profile ${PROFILE_NAME} (tagged for cost allocation)"
  run aws bedrock create-inference-profile --region "$REGION" --inference-profile-name "$PROFILE_NAME" \
      --model-source "copyFrom=${PROFILE_SOURCE_ARN}" --tags "key=application,value=devmind" "key=environment,value=${ENVIRONMENT:-prod}"
  echo "   then set BEDROCK_MODEL_ID to the profile ARN, and activate the 'application' and 'environment' cost-allocation tags in Billing."
fi

echo
echo "Next: set BEDROCK_RECON_SOURCE=cloudwatch and BEDROCK_RECON_LOG_GROUP=${LOG_GROUP} on the orchestrator, then restart it."
