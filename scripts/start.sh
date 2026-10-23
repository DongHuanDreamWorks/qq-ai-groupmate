#!/usr/bin/env bash
# Last-resort start command: bring the whole stack up.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "[$(date '+%F %T')] starting qq-ai-groupmate"
docker compose up -d
docker compose ps
