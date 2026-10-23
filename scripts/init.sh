#!/usr/bin/env bash
# First-run helper: create data dirs, copy .env, install the NapCat bridge plugin.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p data/napcat/config data/napcat/plugins data/napcat/cache data/napcat/qq \
         data/console data/astrbot run

# NapCat container runs as uid 1000; make sure it can write its own dirs.
if [ "$(id -u)" = "0" ]; then
  chown -R 1000:1000 data/napcat run 2>/dev/null || true
fi
chmod -R u+rwX,g+rwX data/napcat run 2>/dev/null || true

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已生成 .env，请先编辑它（QQ_ACCOUNT / NAPCAT_WEBUI_TOKEN / BRAIN_TOKEN / DEEPSEEK_API_KEY）"
fi

./scripts/install-napcat-plugin.sh
echo "初始化完成。编辑 .env 后执行：docker compose up -d"
