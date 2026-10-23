# 部署与运维

## 环境要求

- Linux（Ubuntu 20.04+ / Debian 11+ 均可），x86_64
- Docker 24+ 与 Docker Compose v2
- 建议 2 vCPU / 3.5 GB 内存 / 20 GB 磁盘（QQ 客户端比较吃内存）
- 可选：一个域名 + TLS 证书（公网访问控制台时强烈建议）

> QQ 客户端需要图形环境，NapCat 镜像里已经带了 Xvfb，不需要自己装桌面。

## 一、准备

```bash
git clone <this-repo> qq-ai-groupmate
cd qq-ai-groupmate

cp .env.example .env
vim .env
```

`.env` 里至少填四项：

| 变量 | 说明 |
|---|---|
| `QQ_ACCOUNT` | 机器人 QQ 号（**小号**） |
| `NAPCAT_WEBUI_TOKEN` | 随机字符串，NapCat WebUI 用 |
| `BRAIN_TOKEN` | 随机字符串，AstrBot ↔ Console 内部鉴权 |
| `DEEPSEEK_API_KEY` | DeepSeek 平台申请 |

可选 `CONSOLE_PASSWORD`：不填则首次启动随机生成，写在
`data/console/initial_password.txt`。

## 二、启动

```bash
chmod +x scripts/*.sh
./scripts/init.sh
docker compose up -d
docker compose ps
```

`init.sh` 会：

- 建好 `data/` 下的目录（`napcat/{config,plugins,cache,qq}`、`console`、`astrbot`、`run`）
- 把权限给到 uid 1000（NapCat 容器用户）
- 安装 NapCat 桥插件并写 `plugins.json`

## 三、扫码登录

```bash
docker logs -f qq-ai-napcat | grep -A40 "二维码"
```

或者启动后打开控制台「概览」页，离线时会出现二维码卡片（每 15 秒自动刷新）。

扫码成功后：

- `docker logs qq-ai-astrbot | grep 适配器已连接` 应该出现日志
- 控制台右上角变成「机器人在线」

## 四、首次配置

1. 打开 `http://<host>:8088`，用初始密码登录。
2. 「模型」页确认 Base URL / Key / 模型名，点「测试」。
3. 「人设」页填**常住城市**，按需改人设和生活碎片提示词。
4. 「触发策略」页把目标群号填进白名单（留空 = 所有群）。
5. 「主动发言」页填目标群，或者留空自动选最近活跃的群。

## 五、开机自启与看门狗

### systemd（推荐）

```ini
# /etc/systemd/system/qq-ai.service
[Unit]
Description=QQ AI Groupmate
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/qq-ai-groupmate
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose stop
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now qq-ai.service
```

### 看门狗（cron，每分钟）

```bash
echo '* * * * * root /opt/qq-ai-groupmate/scripts/watchdog.sh' \
  | sudo tee /etc/cron.d/qq-ai-watchdog
sudo chmod 644 /etc/cron.d/qq-ai-watchdog
```

看门狗负责：容器没跑就拉起、控制台健康检查失败就重启 console、
处理控制台发来的「重启 NapCat 刷新二维码」请求。

日志：`/var/log/qq-ai-watchdog.log`。

### 最后底线

```bash
cd /opt/qq-ai-groupmate && ./scripts/start.sh
```

## 六、更新

```bash
git pull
docker compose build console   # 只改了 Python 时才需要
docker compose up -d
```

NapCat 镜像不建议自动更新：它绑定 QQ 客户端版本，升级可能直接导致登录失效。
要升级先备份 `data/napcat/qq`。

## 七、备份

必须备份的：

| 路径 | 内容 |
|---|---|
| `data/console/brain.db` | 消息、记忆、设置、表情库 |
| `data/napcat/qq/` | QQ 登录态（体积较大，可不备） |
| `.env` | 密钥 |

```bash
# 每天 4:00 备份，保留 14 天
0 4 * * * cd /opt/qq-ai-groupmate && \
  tar czf /var/backups/qq-ai-$(date +\%F).tar.gz .env data/console/brain.db && \
  find /var/backups -name 'qq-ai-*.tar.gz' -mtime +14 -delete
```

控制台「日志」页也有导出 / 导入按钮（JSON）。

## 八、反向代理（公网访问）

控制台默认是明文 HTTP + 密码，公网暴露请加 TLS：

```nginx
server {
    listen 443 ssl http2;
    server_name bot.example.com;

    ssl_certificate     /etc/ssl/fullchain.pem;
    ssl_certificate_key /etc/ssl/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 180s;   # 合并回复会等待 7 秒，给足余量
    }
}
```

不要把 `6099`（NapCat WebUI）和 `6185`（AstrBot）暴露到公网。

## 九、排错

### 扫码后频繁掉线

新号 + 机房 IP 的典型风控。日志里会出现：

```
[KickedOffLine] [下线通知] 你的账号当前登录已失效
```

- 确认 `.env` 的 `QQ_ACCOUNT` 已设置，NapCat 会尝试免密重登；
- 免密也失败时，概览页会出二维码，扫码即可；
- 手机 QQ 保持登录这个号，养几天设备信任；
- 少重启容器：每次重启等于一次新登录。

### 二维码出不来

```bash
ls -l data/napcat/cache/qrcode.png
docker logs --tail 100 qq-ai-napcat
```

没有文件说明 NapCat 还没进入登录流程，检查 `QQ_ACCOUNT` 与容器是否正常。

### 控制台 401

- Cookie 过期：重新登录。
- 机器调用：确认 `X-Brain-Token` 与控制台里的 `brain_token` 一致
  （`GET /api/system` 可查）。

### 生图 402

Pollinations 免费额度是 **1 张 / 45 秒 / IP**，超了返回 402。
代码里已经做了 45 秒间隔和重试，仍失败时它会自己说“卡在潜空间”。

### WebSocket 连不上

```bash
docker logs qq-ai-console | grep -i napcat
```

确认 `NAPCAT_WS=ws://napcat:3001`、NapCat 容器在跑、3001 端口已监听。
NapCat 登录成功前不会开 3001。

### 磁盘满

```bash
du -sh data/*
docker system prune -f
```

日志已经限制单文件 10MB × 3；生成的图片在 `data/console/gen/`，可以定期清。
