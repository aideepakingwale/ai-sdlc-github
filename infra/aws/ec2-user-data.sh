#!/usr/bin/env bash
# ===========================================================================
# AI-SDLC — EC2 user-data (cloud-init) for Ubuntu 22.04.
#
# Paste this as the instance "User data" at launch (or --user-data file://...).
# cloud-init runs it ONCE, as root, on first boot. It installs every
# prerequisite the PoC needs:
#   - a 4 GB swapfile (so the first docker build doesn't OOM on t3.medium)
#   - Docker Engine + compose plugin
#   - git, GitHub CLI (gh), AWS CLI v2, unzip, jq
#
# OPTIONAL auto-deploy: if you fill in REPO_URL + BEDROCK_MODEL_ID in the
# CONFIG block below, it also clones the repo and runs ./deploy.sh for you,
# so the instance comes up with the whole stack already running.
#
# Logs: /var/log/ai-sdlc-userdata.log     Done marker: /opt/ai-sdlc-userdata.done
# Watch live on the box:  sudo tail -f /var/log/ai-sdlc-userdata.log
# NOTE: Bedrock auth is still the EC2 INSTANCE ROLE — no API keys here. Attach
# the role + set metadata hop-limit=2 at launch (guide steps 2-3).
# ===========================================================================
set -euxo pipefail
exec > >(tee -a /var/log/ai-sdlc-userdata.log) 2>&1
echo "[user-data] start $(date -u)"

# --------------------------- CONFIG (edit me) ------------------------------
# Leave REPO_URL empty to ONLY install prerequisites (then clone + run
# ./deploy.sh yourself). Fill both to auto-deploy on first boot.
REPO_URL=""                         # e.g. https://github.com/aideepakingwale/ai-sdlc-github.git
REPO_BRANCH="main"
BEDROCK_MODEL_ID=""                 # e.g. us.anthropic.claude-3-5-sonnet-20241022-v2:0
REGION=""                           # empty => auto-detect from instance metadata
TARGET_USER="ubuntu"                # non-root user that will own the checkout
CLONE_DIR="/opt/ai-sdlc"
# ---------------------------------------------------------------------------

export DEBIAN_FRONTEND=noninteractive

# 1) Swap (idempotent) -------------------------------------------------------
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 2) Base packages -----------------------------------------------------------
apt-get update -y
apt-get install -y ca-certificates curl git unzip jq gnupg lsb-release

# 3) Docker Engine + compose plugin (official convenience script) ------------
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
usermod -aG docker "${TARGET_USER}" || true

# 4) GitHub CLI (gh) ---------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
  mkdir -p -m 755 /etc/apt/keyrings
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -y
  apt-get install -y gh
fi

# 5) AWS CLI v2 --------------------------------------------------------------
if ! command -v aws >/dev/null 2>&1; then
  ARCH=$(uname -m)   # x86_64 or aarch64 — both map 1:1 to the AWS bundle names
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${ARCH}.zip" -o /tmp/awscliv2.zip
  unzip -q /tmp/awscliv2.zip -d /tmp
  /tmp/aws/install --update
  rm -rf /tmp/aws /tmp/awscliv2.zip
fi

echo "[user-data] prerequisites installed: docker=$(docker --version 2>/dev/null), gh=$(gh --version 2>/dev/null | head -1), aws=$(aws --version 2>/dev/null)"

# 6) OPTIONAL auto-deploy ----------------------------------------------------
if [[ -n "${REPO_URL}" && -n "${BEDROCK_MODEL_ID}" ]]; then
  echo "[user-data] auto-deploy: cloning ${REPO_URL} (${REPO_BRANCH})"
  # For a PRIVATE repo, embed a token in REPO_URL
  # (https://<TOKEN>@github.com/owner/repo.git) or bake a deploy key first.
  rm -rf "${CLONE_DIR}"
  git clone --branch "${REPO_BRANCH}" --depth 1 "${REPO_URL}" "${CLONE_DIR}"
  chown -R "${TARGET_USER}:${TARGET_USER}" "${CLONE_DIR}"

  DEPLOY_ARGS=(--model-id "${BEDROCK_MODEL_ID}")
  [[ -n "${REGION}" ]] && DEPLOY_ARGS+=(--region "${REGION}")

  # Run deploy.sh as the target user so docker group + file ownership are right.
  # Docker (root daemon) is already up, so no --bootstrap needed here.
  sudo -iu "${TARGET_USER}" bash -lc "cd '${CLONE_DIR}' && chmod +x deploy.sh && ./deploy.sh ${DEPLOY_ARGS[*]}"
  echo "[user-data] auto-deploy finished — app should be on http://<public-ip>:3000"
else
  echo "[user-data] prerequisites only. Next: clone the repo and run ./deploy.sh --model-id <id>"
fi

touch /opt/ai-sdlc-userdata.done
echo "[user-data] done $(date -u)"
