#!/usr/bin/env bash
# Install the small NapCat bridge plugin that exposes Qzone/avatar/profile APIs
# and the friend-request helpers to the console.
set -euo pipefail
cd "$(dirname "$0")/.."

DEST=data/napcat/plugins/napcat-plugin-qce
mkdir -p data/napcat/plugins
rm -rf "$DEST"
cp -r napcat-plugin "$DEST"

cat > data/napcat/plugins/plugins.json <<'JSON'
{
  "napcat-plugin-qce": true
}
JSON

echo "NapCat bridge plugin installed at $DEST"
echo "说明：该插件以 NapCat 官方白名单里的包名运行（napcat-plugin-qce），"
echo "NapCat 升级后若白名单变化，需要相应调整 package.json 里的 name。"
