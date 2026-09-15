#!/usr/bin/env bash
# Generate a JWT secret and patch it into .env (creates .env from .env.example if missing).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
SECRET=$(node -e "console.log(require('node:crypto').randomBytes(32).toString('hex'))")
if grep -q '^JWT_SECRET=' .env; then
  sed -i.bak "s/^JWT_SECRET=.*/JWT_SECRET=${SECRET}/" .env && rm -f .env.bak
else
  echo "JWT_SECRET=${SECRET}" >> .env
fi
echo "JWT_SECRET updated in .env"
