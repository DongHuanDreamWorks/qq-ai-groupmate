# 架构与运行原理

## 1. 组件

| 组件 | 镜像 / 代码 | 职责 |
|---|---|---|
| NapCat | `mlikiowa/napcat-docker` | 在容器里跑官方 Linux QQ，注入 OneBot v11 协议；提供 Qzone、头像、好友等内部 WebApi |
| AstrBot | `soulter/astrbot` | 只做消息传输：把 QQ 事件交给 `qqbrain` 插件，把插件的回复发回 QQ；**LLM 已关闭** |
| qqbrain 插件 | `astrbot-plugin/qqbrain` | 把消息整理成 JSON POST 给 Console，把返回的文字/表情/图片发回群 |
| Console | `console/app`（FastAPI） | 大脑：拼提示词、查记忆、调模型、执行工具、调度任务、提供控制台 |
| NapCat 桥插件 | `napcat-plugin/` | 通过共享文件轮询，调用 NapCat 内部 WebApi（发说说、换头像、加好友等） |

端口：`8088` 控制台，`6099` NapCat WebUI（仅本机），`6185` AstrBot WebUI（仅本机），
Console 通过 `ws://napcat:3001` 接事件通道。

## 2. 一条消息的旅程

```
QQ 群
  │  1. 有人说话
  ▼
NapCat ──OneBot 事件──► AstrBot ──qqbrain.on_message──► POST /api/handle
  │                                                      │
  │                                                      ▼
  │                                            Console: brain.handle()
  │                                              2. 落库 messages
  │                                              3. decide() 判断回不回
  │                                              4. 拼 system + user 提示词
  │                                              5. 调 DeepSeek（可带图）
  │                                              6. 工具循环（最多 3 步）
  │                                              7. 拆气泡 / 发表情 / 配图
  │                                                      │
  ◄──────────── 8. 返回 {reply, bubbles, faces, images} ◄┘
  │
  ▼
qqbrain 依次发送：引用回复 + 文字气泡 + Face + Image
```

关键点：

- AstrBot 的 LLM 必须关闭（`provider_settings.enable=false`），否则会自己抢答。
- qqbrain 发送完成后调用 `event.stop_event()`，避免 AstrBot 再处理一遍。
- 图片由 Console 下载后以 base64 回传，QQ 侧不再拉取，避免图片链接过期。

## 3. 回不回：`decide()`

按顺序判断：

1. 黑名单群 / 非白名单群 → 不回。
2. 当前时段回复率 = 0（深夜）→ 不回，消息照样入库。
3. `@` 我 / 私聊 / 前缀触发 / 命中关键词 → 回。
4. 太短、纯图片、纯链接、连续复读 → 不回。
5. 冷却时间内、10 分钟超过上限 → 不回。
6. 掷骰子：`random() < 当前时段回复率`。

时段默认：

| 时段 | 回复率 |
|---|---|
| 12:00–20:00 | 100% |
| 08:00–12:00 | 50% |
| 20:00–24:00 | 25% |
| 00:00–08:00 | 0% |

另有兜底：冷却 3 秒、每 10 分钟最多 20 条。

## 4. 合并回复与多气泡

- 命中回复后，25% 概率只回当前这条；75% 概率进入 7 秒合并窗口。
- 窗口内其它消息的请求会直接返回 `batched`，不重复回复。
- 窗口结束后重新取最近消息，生成一次“对一轮消息的整体回应”。
- 回复按换行/标点拆成 1–4 条气泡，气泡间随机停 1–2.2 秒，模拟真人连发。

## 5. 提示词组装

### system

```
人设 system_prompt（{name} {identity} 占位）
+ 背景故事
+ 工具清单 & 使用规则
+ 表情库摘要（描述列表）
+ 禁忌
```

### user（按顺序）

```
【上一次对话的存档】    ← 午夜归档的 summary / injection / important
【你想起了这些人】      ← 跨对话人物动态（只在提到人名时出现）
【你对 XXX 的了解】     ← 当前说话人的印象：维度 + 摘要 + 事实
【生活碎片】            ← 随机 3 条当天碎片
【最近的群聊记录】      ← 最多 24 条 / 3500 字，带时间、昵称、图片占位
【被引用的消息】        ← 可选
现在 XXX 说：...
```

这样分层的目的：**长期记忆、跨人记忆、当前对话、临时素材，各自有独立的预算和寿命**，
不会互相挤占。

## 6. 记忆的五层

| 层 | 存储 | 更新时机 | 用途 |
|---|---|---|---|
| 上下文 | `messages` | 每条消息 | 最近 24 条对话 |
| 群摘要 | `summaries` | 每 80 条 | 上下文过长时的压缩 |
| 人物动态 | `person_digests` | 每人每 +5 条（20 分钟冷却） | 跨对话提到某人时召回 |
| 长期印象 | `users.impression` | 每人每 12 条 | 熟悉度/好感度/事实 |
| 午夜归档 | `rotations` | 每天 00:00 | 开启新一天，清空上下文 |

生活碎片（`persona.lore`）每天 12:00 由天气 + 社会新闻生成，属于“当天的素材”，
不属于长期记忆。

## 7. 工具

| 工具 | 触发 | 限制 |
|---|---|---|
| `draw_image` | 想画图时 | Pollinations 免费额度 1 张 / 45 秒 / IP |
| `change_avatar` | 想把画设成头像 | 6 小时一次 |
| `post_qzone` | 想发说说 | 每天最多 3 条，可带刚画的图 |
| `ask_ai` | 想请教别的模型 | 每家 provider 有每小时/每天上限 |
| `send_sticker` | 想发表情 | 从 QQ 表情收藏里按描述挑 |
| `save_sticker` | 用户说“存表情” | 存进 QQ 云端收藏 + 自动识别描述 |

工具调用循环最多 3 步，结果以 `role=tool` 回灌给模型。

## 8. 后台任务

| 任务 | 周期 | 耗 token |
|---|---|---|
| 主动发言 | 每群 180–420 分钟，每天 ≤3 次 | 是 |
| 自动说说 | 每天固定时间，1 条 | 是 |
| 午夜归档 | 每天 00:00（2 小时补跑窗口） | 是 |
| 人物动态 | 消息驱动，+5 条/人，20 分钟冷却 | 是 |
| 生活碎片 | 每天 12:00 | 是 |
| 表情同步 | 每 6 小时（只对新表情识别） | 少量 |
| 告警检查 | 每分钟 | 否 |
| 看门狗 | 每分钟（cron） | 否 |

## 9. 数据表

`messages` `users` `summaries` `settings` `events` `llm_calls` `qzone_posts`
`rotations` `stickers` `alerts` `person_digests`

SQLite WAL 模式，单文件 `data/console/brain.db`。

## 10. 失败与恢复

- **NapCat 掉线**：Console 的 WebSocket 断开 → 告警 → 概览页出现二维码卡片。
- **强制下线**：NapCat 重启 Worker；compose 里的 `NAPCAT_QUICK_ACCOUNT` 让它尝试免密登录，
  失败才落二维码。
- **容器挂了**：`restart: unless-stopped` + systemd + cron 看门狗三重兜底。
- **控制台挂了**：看门狗每分鐘健康检查，失败自动重启。
- **模型失败**：调用记录进 `llm_calls`，失败率高时告警；单次失败会返回“卡住了”之类的兜底话术。

## 11. 安全模型

- 控制台：密码登录（scrypt 哈希）+ HMAC 签名 Cookie；登录限流 6 次 / 10 分钟。
- 内部接口：`X-Brain-Token` 头，值来自 `BRAIN_TOKEN` 或首次启动随机生成。
- 生成图片的 `/api/gen/{name}` 不鉴权，只为让 NapCat 能取图；文件名是随机的。
- Console 不挂 Docker socket；需要重启 NapCat 时只写一个请求文件，由宿主机看门狗执行。
