#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# AI-SDLC — single-host PoC deploy on Linux (EC2) with Amazon Bedrock.
#
# Run this ON the instance, from the repo root, after `git clone`.
# It configures .env for Bedrock (instance-role auth, no API keys), writes the
# ai-client credential override, then builds, migrates, seeds and starts the
# whole docker-compose stack.
#
# Bedrock auth uses the EC2 INSTANCE ROLE (see docs/DEPLOY-AWS-EC2-BEDROCK.md
# steps 1-3). This script does NOT create AWS IAM/Bedrock resources — those are
# a one-time account setup done from your laptop per the guide.
#
# Quick start:
#   ./deploy.sh --model-id us.anthropic.claude-3-5-sonnet-20241022-v2:0
#
# Full first-boot (also installs swap + Docker, needs sudo):
#   ./deploy.sh --bootstrap --model-id <ID> --region us-east-1
#
# Everything is idempotent — safe to re-run.
# ---------------------------------------------------------------------------
set -euo pipefail

# ---- defaults ----------------------------------------------------------------
MODEL_ID="${BEDROCK_MODEL_ID:-}"
REGION="${BEDROCK_REGION:-${AWS_REGION:-}}"
PUBIP="${PUBIP:-}"
CTX_THRESHOLD="${CONTEXT_TOKEN_THRESHOLD:-16000}"
DO_BOOTSTRAP=0
DO_BUILD=1
DO_CHECK=1
WITH_DIAGRAMS=1

CORE_SVCS="postgres redis dynamodb localstack orchestrator ai-client tool-connector frontend"
DIAGRAM_SVCS="plantuml drawio"

log()  { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
AI-SDLC — single-host PoC deploy on Linux (EC2) with Amazon Bedrock.

Run this ON the instance, from the repo root, after `git clone`. It configures
.env for Bedrock (instance-role auth, no API keys), writes the ai-client
credential override, then builds, migrates, seeds and starts the stack.
Bedrock auth uses the EC2 INSTANCE ROLE — this script does NOT create AWS
IAM/Bedrock resources (that's a one-time laptop setup per the guide).

Usage:
  ./deploy.sh --model-id <bedrock-id> [options]
  ./deploy.sh --bootstrap --model-id <bedrock-id> --region us-east-1

Options:
  --model-id <id>     Bedrock model / inference-profile id (required).
                      e.g. us.anthropic.claude-3-5-sonnet-20241022-v2:0
  --region <region>   AWS/Bedrock region. Auto-detected from IMDS if omitted.
  --pubip <ip|host>   Public address for APP_PUBLIC_URL. Auto-detected if omitted.
  --ctx <n>           CONTEXT_TOKEN_THRESHOLD (default 16000).
  --bootstrap         Also add a 4G swapfile and install Docker (needs sudo).
  --no-diagrams       Skip the plantuml/drawio containers (saves ~500-700MB RAM).
  --skip-build        Reuse existing images (don't run `docker compose build`).
  --skip-check        Skip the Bedrock/instance-role preflight check.
  -h, --help          Show this help.
EOF
}

# ---- arg parsing -------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-id)   MODEL_ID="${2:?}"; shift 2 ;;
    --region)     REGION="${2:?}"; shift 2 ;;
    --pubip)      PUBIP="${2:?}"; shift 2 ;;
    --ctx)        CTX_THRESHOLD="${2:?}"; shift 2 ;;
    --bootstrap)  DO_BOOTSTRAP=1; shift ;;
    --no-diagrams) WITH_DIAGRAMS=0; shift ;;
    --skip-build) DO_BUILD=0; shift ;;
    --skip-check) DO_CHECK=0; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

cd "$(dirname "$0")"
[[ -f docker-compose.yml ]] || die "Run this from the repo root (docker-compose.yml not found)."
[[ -f .env.example ]]       || die ".env.example missing — is this the AI-SDLC repo?"

# ---- IMDSv2 helper (best-effort; empty if not on EC2) ------------------------
imds() {
  local tok
  tok=$(curl -s --max-time 2 -X PUT "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 300" 2>/dev/null) || return 0
  [[ -n "$tok" ]] || return 0
  curl -s --max-time 2 -H "X-aws-ec2-metadata-token: $tok" \
       "http://169.254.169.254/latest/meta-data/$1" 2>/dev/null || true
}

# ---- optional bootstrap: swap + Docker --------------------------------------
if [[ "$DO_BOOTSTRAP" == "1" ]]; then
  log "Bootstrap: swapfile + Docker (sudo required)"
  if ! swapon --show 2>/dev/null | grep -q '/swapfile'; then
    sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
    sudo mkswap /swapfile && sudo swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    log "4G swap enabled."
  else
    log "Swap already present — skipping."
  fi
  if ! command -v docker >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y ca-certificates curl git
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "${USER}" || true
    warn "Added ${USER} to the docker group. If the next docker call fails with a"
    warn "permission error, log out/in (or run: newgrp docker) and re-run ./deploy.sh"
  else
    log "Docker already installed — skipping."
  fi
fi

command -v docker >/dev/null 2>&1 || die "docker not found. Re-run with --bootstrap or install Docker first."
docker compose version >/dev/null 2>&1 || die "docker compose plugin not found."
command -v openssl >/dev/null 2>&1 || die "openssl not found (needed to generate JWT_SECRET)."

# ---- resolve region / model / pubip -----------------------------------------
[[ -n "$MODEL_ID" ]] || die "Bedrock model id required. Pass --model-id <id>. Find it with:
  aws bedrock list-inference-profiles --region <REGION> \\
    --query \"inferenceProfileSummaries[?contains(inferenceProfileId,'anthropic')].[inferenceProfileId]\" --output table"

if [[ -z "$REGION" ]]; then
  REGION="$(imds "placement/region")"
  [[ -n "$REGION" ]] && log "Region auto-detected from IMDS: $REGION"
fi
[[ -n "$REGION" ]] || die "Region not set and not detectable. Pass --region <region>."

if [[ -z "$PUBIP" ]]; then
  PUBIP="$(imds "public-ipv4")"
  [[ -n "$PUBIP" ]] && log "Public IP auto-detected from IMDS: $PUBIP"
fi
[[ -n "$PUBIP" ]] || { PUBIP="localhost"; warn "No public IP detected — using localhost for APP_PUBLIC_URL."; }

# ---- Bedrock / instance-role preflight --------------------------------------
if [[ "$DO_CHECK" == "1" ]]; then
  ROLE="$(imds "iam/security-credentials/")"
  if [[ -z "$ROLE" ]]; then
    warn "No IAM instance role visible from IMDS. Bedrock auth needs the instance role."
    warn "Check: role attached (guide step 2/3) AND metadata hop-limit=2 (step 3)."
    warn "Continuing anyway — verify after startup with step 8 of the guide."
  else
    log "Instance role detected: $ROLE"
    if command -v aws >/dev/null 2>&1; then
      if aws bedrock list-foundation-models --region "$REGION" >/dev/null 2>&1; then
        log "Bedrock reachable in $REGION with the instance role."
      else
        warn "Could not list Bedrock models in $REGION with the instance role."
        warn "The role may lack bedrock:* or model access isn't enabled (guide step 1)."
      fi
    fi
  fi
fi

# ---- .env --------------------------------------------------------------------
if [[ ! -f .env ]]; then
  cp .env.example .env
  log "Created .env from .env.example"
else
  log ".env already exists — updating the Bedrock/PoC values in place."
fi

JWT_CUR="$(grep -E '^JWT_SECRET=' .env | cut -d= -f2- || true)"
if [[ -z "$JWT_CUR" || "$JWT_CUR" == dev-only-change-me* ]]; then
  JWT_NEW="$(openssl rand -hex 32)"
  log "Generated a fresh JWT_SECRET."
else
  JWT_NEW="$JWT_CUR"   # keep an operator-set secret across re-runs
fi

# NOTE: no API keys are written here — Bedrock auth is the instance role.
sed -i \
  -e "s|^GENERATION_MODE=.*|GENERATION_MODE=llm|" \
  -e "s|^BEDROCK_MODEL_ID=.*|BEDROCK_MODEL_ID=${MODEL_ID}|" \
  -e "s|^BEDROCK_REGION=.*|BEDROCK_REGION=${REGION}|" \
  -e "s|^AWS_REGION=.*|AWS_REGION=${REGION}|" \
  -e "s|^AUTH_MODE=.*|AUTH_MODE=local|" \
  -e "s|^APP_PUBLIC_URL=.*|APP_PUBLIC_URL=http://${PUBIP}:3000|" \
  -e "s|^JWT_SECRET=.*|JWT_SECRET=${JWT_NEW}|" \
  -e "s|^CONTEXT_TOKEN_THRESHOLD=.*|CONTEXT_TOKEN_THRESHOLD=${CTX_THRESHOLD}|" \
  .env
log ".env configured (mode=llm, model=${MODEL_ID}, region=${REGION}, url=http://${PUBIP}:3000)"

# ---- ai-client credential override (guide step 6a) ---------------------------
# .env ships AWS_ACCESS_KEY_ID=local for the DynamoDB/S3 emulators. That dummy
# value would override the instance role inside ai-client and break Bedrock, so
# blank it JUST for ai-client — its SDK then falls through to the instance role.
cat > docker-compose.override.yml <<'YAML'
services:
  ai-client:
    environment:
      # Empty => AWS SDK skips env creds and uses the EC2 instance role (Bedrock).
      AWS_ACCESS_KEY_ID: ""
      AWS_SECRET_ACCESS_KEY: ""
YAML
log "Wrote docker-compose.override.yml (ai-client uses the instance role for Bedrock)."

# ---- services list -----------------------------------------------------------
SVCS="$CORE_SVCS"
[[ "$WITH_DIAGRAMS" == "1" ]] && SVCS="$SVCS $DIAGRAM_SVCS"

# ---- build / migrate / seed / up --------------------------------------------
if [[ "$DO_BUILD" == "1" ]]; then
  log "Building images (first build ~5-10 min)…"
  docker compose build
fi

log "Applying database migrations…"
docker compose run --rm migrate

log "Seeding demo users (password for all: Password123!)…"
docker compose run --rm orchestrator python -m app.scripts seed

log "Starting the stack…"
docker compose up -d ${SVCS}

# ---- verify ------------------------------------------------------------------
log "Waiting for ai-client to answer /healthz…"
ok=0
for _ in $(seq 1 30); do
  if curl -sf http://localhost:8081/healthz >/dev/null 2>&1; then ok=1; break; fi
  sleep 3
done
if [[ "$ok" == "1" ]]; then
  log "ai-client healthy. Bedrock providers:"
  docker compose logs ai-client 2>/dev/null | grep -iE "provider|bedrock|mock" | tail -5 || true
else
  warn "ai-client did not report healthy in time. Check: docker compose logs -f ai-client"
fi

echo
log "Done. Open:  http://${PUBIP}:3000"
log "Sign in:     superadmin@sdlc.local  /  Password123!"
log "If generations fall back to mock, see guide §8 (hop-limit, model access, override)."
