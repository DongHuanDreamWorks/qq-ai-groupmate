#!/usr/bin/env bash
# Run every minute from cron. Restarts dead containers, honours the
# console's "restart napcat" request, and checks the console health endpoint.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 0

LOG=${WATCHDOG_LOG:-/var/log/qq-ai-watchdog.log}
stamp() { date '+%F %T'; }
mkdir -p run

if [ -f run/restart-napcat ]; then
  echo "$(stamp) restart requested by console" >> "$LOG"
  docker compose restart napcat >> "$LOG" 2>&1
  rm -f run/restart-napcat
fi

running=$(docker compose ps --status running --services 2>/dev/null || true)
for svc in console astrbot napcat; do
  if ! echo "$running" | grep -qx "$svc"; then
    echo "$(stamp) $svc not running, starting" >> "$LOG"
    docker compose up -d "$svc" >> "$LOG" 2>&1
  fi
done

if ! curl -fsS -m 5 http://127.0.0.1:8088/api/health >/dev/null 2>&1; then
  echo "$(stamp) console health check failed, restarting" >> "$LOG"
  docker compose restart console >> "$LOG" 2>&1
fi
