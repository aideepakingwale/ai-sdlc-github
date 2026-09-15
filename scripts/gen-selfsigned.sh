#!/usr/bin/env bash
# Self-signed cert for the local TLS overlay (browsers will warn — dev only).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p infra/certs
openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout infra/certs/server.key -out infra/certs/server.crt \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
echo "Wrote infra/certs/server.{crt,key} — start with:"
echo "  docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d"
