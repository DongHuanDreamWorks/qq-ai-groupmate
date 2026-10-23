# QQ AI Groupmate · 小肥鱼

> A self-hosted AI persona that actually lives in a QQ group: it chats like a
> netizen, remembers people, sees images, sends stickers, draws, changes its own
> avatar, posts to Qzone, and wakes up on its own three times a day.

一个能长期住在 QQ 群里的 AI 群友。不是客服，不是问答机器人，是一个有脾气、有记忆、会自己过日子的网友。

**本项目由 DeepSeek Harness（模型 `deepseek-flash`）从零搭建。**
从架构、代码、部署到文档，都是在与项目发起人的对话里一步步长出来的。

---

## 它能做什么

| 能力 | 说明 |
|---|---|
| 像人一样聊天 | 短句、多气泡、会接梗、被骂会怼回去；分时段回复（中午 100%，深夜闭嘴） |
| 记得人 | 每个人有独立印象：熟悉度 / 好感度 / 活跃度 / 信任度 + 一句话摘要 |
| 跨对话联想 | 提到某个人的名字时，自动带出 TA 的动态摘要（只给摘要，不给私聊原文） |
| 看得见 | 图片、截图、表情包都能描述；GIF 取第一帧 |
| 有表情库 | 同步 QQ 表情收藏，视觉识别后写回云端描述，按情绪自己发表情 |
| 会画画 | 自己写英文提示词生图，发到群里；也能把画设成自己的头像 |
| 会发空间 | 自己写说说，可选公开 / 好友 / 仅自己；能配图 |
| 自己会醒 | 每天 3 次主动发言，会看群里气不气氛、太安静就不插嘴 |
| 有自己的生活 | 每天 12:00 按常住城市抓天气 + 社会新闻，生成当天的生活碎片 |
| 会记事 | 午夜归档当天对话，写成摘要 + 给明天自己的话，然后开启新一天 |
| 掉线能扛 | Docker 重启、systemd 自启、看门狗、二维码兜底 |

## 架构

```
        ┌────────────┐   OneBot v11 (WS)   ┌────────────┐
        │   NapCat   │◄───────────────────►│  AstrBot   │
        │ QQ 客户端  │                     │  仅作传输  │
        └─────┬──────┘                     └─────┬──────┘
              │ 插件桥（Qzone / 头像 / 好友）    │ qqbrain 插件
              │                                  │ HTTP /api/handle
              ▼                                  ▼
        ┌──────────────────────────────────────────────┐
        │              Console（FastAPI + SQLite）      │
        │  人设 / 记忆 / 工具 / 调度 / 控制台 / 告警     │
        └──────────────────┬───────────────────────────┘
                           │
                  ┌────────▼────────┐
                  │  DeepSeek API   │
                  └─────────────────┘
```

- **NapCat**：跑官方 Linux QQ，提供 OneBot v11 协议。
- **AstrBot**：只当传输层，LLM 关闭；自定义插件 `qqbrain` 把每条消息转给 Console。
- **Console**：真正的大脑。拼提示词、查记忆、调工具、做调度、出控制台。

## 快速开始

```bash
git clone <this-repo> qq-ai-groupmate
cd qq-ai-groupmate

cp .env.example .env
# 编辑 .env：QQ_ACCOUNT / NAPCAT_WEBUI_TOKEN / BRAIN_TOKEN / DEEPSEEK_API_KEY

./scripts/init.sh          # 建目录、装 NapCat 桥插件
docker compose up -d
```

首次启动后：

1. 看日志里的二维码（或打开控制台「概览」页的二维码卡片）：
   ```bash
   docker logs -f qq-ai-napcat | grep -A40 "二维码"
   ```
2. 用手机 QQ 扫码登录。
3. 打开 `http://127.0.0.1:8088`，用 `data/console/initial_password.txt` 里的密码登录。
4. 在「触发策略」里把目标群加进白名单，或者留空表示所有群。

> 首次登录后记得在「模型」页确认 API Key 可用，在「人设」页填常住城市。

## 控制台

`http://<host>:8088`，13 个页面：

**概览** · **对话调试** · **长期记忆** · **上下文** · **人设** · **触发策略** ·
**主动发言** · **自运营** · **外部 AI** · **表情** · **好友 / 入群** · **模型** · **日志**

详见 [docs/PANEL.md](docs/PANEL.md)。

## 文档

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 系统怎么运转、记忆分层、调度、失败恢复
- [docs/API.md](docs/API.md) — 全部 HTTP 接口、鉴权、示例
- [docs/PANEL.md](docs/PANEL.md) — 控制台每个页面的功能与用法
- [docs/DEPLOY.md](docs/DEPLOY.md) — 部署、升级、备份、反向代理、排错
- [docs/SECURITY.md](docs/SECURITY.md) — 密钥、隐私、合规与风险

## 设计取舍

- **人设要短**：人设越长，模型越像在“演人设”，不像网友。默认系统提示词只有几行。
- **记忆要有层次**：上下文 → 人物动态 → 长期印象 → 午夜归档 → 生活碎片，各有各的寿命。
- **跨对话只给摘要**：把别人的私聊原文拼进另一个人的对话，是隐私事故，不是功能。
- **能自动就不要手动**：掉线、二维码、重启、备份额度，全部有兜底。
- **钱要看得见**：所有定时任务的频率和 token 开销都写进文档。

## 已知限制

- **QQ 账号风控是这个项目最大的不确定性**：新号 + 机房 IP 可能几十分钟就掉一次线。代码里做了自动免密和二维码兜底，但无法从根本上解决。
- **NapCat 依赖官方 QQ 客户端**，QQ 升级可能破坏兼容性。
- **GIF 只取第一帧**（模型侧限制）。
- **没有内置 embedding**：跨对话召回按人名和摘要，不做向量检索。
- **单实例设计**：SQLite + 单进程，适合一个群/一台机器，不适合多租户。

## 成本

按每天 500 条群消息、白天 100% 回复估算，DeepSeek 大约 **¥1–2/天**。
生图走 Pollinations 免费额度（1 张 / 45 秒 / IP）。

## 安全与合规（务必读）

- 自动化 QQ 客户端**可能违反腾讯服务条款**，账号存在被限制/封禁的风险。**不要用主号。**
- 群友有权知道自己在跟 AI 说话。建议在群公告里说明，或至少让群主知情。
- 控制台默认只监听 `8088`，公网暴露请务必加 TLS 反向代理和强密码。
- 仓库里**不包含任何 QQ 数据、登录态、聊天记录或密钥**；`data/` 已被 `.gitignore` 忽略。

详见 [docs/SECURITY.md](docs/SECURITY.md)。

## 致谢

- [NapCat](https://github.com/NapNeko/NapCatQQ) — QQ 协议侧车
- [AstrBot](https://github.com/Soulter/AstrBot) — 消息传输与插件宿主
- [DeepSeek](https://platform.deepseek.com) — 模型
- [Pollinations](https://pollinations.ai) — 免费生图
- 中国新闻网 RSS、Open-Meteo — 新闻与天气

## License

MIT，见 [LICENSE](LICENSE)。
