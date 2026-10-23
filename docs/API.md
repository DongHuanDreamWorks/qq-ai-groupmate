# HTTP API

控制台是一个 FastAPI 应用，默认监听 `:8088`。所有业务接口返回 JSON；
错误统一为 `{"ok": false, "error": "..."}`。

## 鉴权

两种方式，二选一：

1. **控制台会话**：先 `POST /api/login` 拿 `brain_session` Cookie（HMAC 签名，7 天有效）。
2. **内部令牌**：请求头 `X-Brain-Token: <brain_token>`。用于 AstrBot 插件、心跳等机器调用。
   `brain_token` 来自环境变量 `BRAIN_TOKEN`，没设则首次启动随机生成，可在控制台「模型」页或
   `GET /api/system` 查看。

登录限流：同一 IP 10 分钟内失败 6 次后返回 `429`。

```bash
curl -c cookie.txt -X POST http://127.0.0.1:8088/api/login \
  -H 'Content-Type: application/json' -d '{"password":"..."}'

curl -b cookie.txt http://127.0.0.1:8088/api/status
```

## 页面

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 未登录显示登录页，已登录显示控制台 |
| GET | `/app` | 控制台入口，未登录 302 到 `/` |
| GET | `/login` | 登录页 |
| GET | `/api/health` | 健康检查，无需鉴权：`{"ok":true,"service":"qqbrain-console"}` |
| GET | `/api/gen/{name}` | 生成的图片（给 NapCat 取头像用），无需鉴权 |

## 认证与状态

| 方法 | 路径 | Body / 参数 | 说明 |
|---|---|---|---|
| POST | `/api/login` | `{password}` | 成功设置 Cookie |
| POST | `/api/logout` | — | 清除 Cookie |
| POST | `/api/password` | `{old, new}` | 改密码，`new` 至少 6 位 |
| GET | `/api/status` | — | `{online, napcat, ...}` 机器人是否在线 |
| GET | `/api/overview` | — | 概览页数据：计数、最近事件、模型消耗 |
| GET | `/api/system` | — | `brain_token`、版本、启动时间、self_id |

## 设置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/settings` | 全部设置（敏感字段已剔除） |
| GET | `/api/settings?section=persona` | 单个 section |
| PUT | `/api/settings/{section}` | Body `{"value": {...}}`，与现有配置合并 |

section 列表：`persona` `context` `trigger` `model` `memory` `emoji` `safety`
`tools` `friend` `selfops` `proactive` `system`。

```bash
curl -b cookie.txt -X PUT http://127.0.0.1:8088/api/settings/persona \
  -H 'Content-Type: application/json' \
  -d '{"value":{"city":"上海"}}'
```

## 上下文 / 记忆

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/api/groups` | — | 有记录的群列表 |
| GET | `/api/context` | `platform, group_id, limit=60` | 最近消息 + 摘要 + 最近一次归档 |
| DELETE | `/api/context` | `platform, group_id` | 清空该群上下文 |
| POST | `/api/context/import` | `{group_id, count=50}` | 从 QQ 拉取最近历史 |
| GET | `/api/rotations` | — | 午夜归档记录 + 状态 |
| POST | `/api/rotation/run` | `{group_id?}` | 立即归档（留空=全部群） |
| GET | `/api/users` | `q=` | 用户列表（可按昵称/uid 搜） |
| GET | `/api/users/{uid}` | — | 单用户 + 印象 |
| PUT | `/api/users/{uid}` | `{summary, style, tags, facts, dims, display_name}` | 手动改印象 |
| POST | `/api/users/{uid}/refresh` | — | 让模型重新提炼印象 |
| DELETE | `/api/users/{uid}` | — | 删除该用户及其消息 |

`dims` 是四个 0–100 的整数：`familiarity` `affinity` `activity` `trust`。

## 人设 / 生活碎片

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/lore` | 生活碎片状态（城市、上次更新时间、错误） |
| POST | `/api/lore/refresh` | 立即抓天气+新闻生成碎片，返回 `{ok, weather, news, fragments}` |
| GET | `/api/people` | 所有人物动态摘要 + 名字 |

## QQ 连接

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/qq/state` | `{connected, qr_age, qr_url, kicked}` |
| GET | `/api/qq/qr.png` | 当前登录二维码 PNG（需登录） |
| POST | `/api/qq/restart` | 请求看门狗重启 NapCat，约 30 秒后出码 |

## 告警

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/api/alerts` | `only_open=true` | 告警列表 |
| POST | `/api/alerts/ack` | `{id?}` | 标记已读，不传 id = 全部 |

## 表情

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/stickers` | 表情库 + 同步状态 |
| POST | `/api/stickers/sync` | 从 QQ 收藏同步并识别新表情 |

## 自运营

| 方法 | 路径 | Body | 说明 |
|---|---|---|---|
| GET | `/api/self` | — | 资料、空间配置、最近说说 |
| POST | `/api/self/profile` | `{nickname, signature}` | 改昵称/签名 |
| POST | `/api/self/avatar` | `{url}` | 换头像（URL） |
| POST | `/api/self/qzone` | `{content, visibility}` | 发说说，`visibility`: 1 公开 / 4 好友 / 64 仅自己 |
| POST | `/api/self/qzone/draft` | — | 让模型起草一条说说 |
| POST | `/api/self/qzone/delete` | `{tid}` | 删除说说 |

## 主动发言

| 方法 | 路径 | Body | 说明 |
|---|---|---|---|
| GET | `/api/proactive/status` | — | 下次唤醒时间、今日次数、配置 |
| POST | `/api/proactive/trigger` | `{group_id}` | 立即发一条（不影响随机倒计时） |

## 模型 / 日志

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| POST | `/api/model/test` | `{prompt}` | 测一次模型，返回耗时、token、费用 |
| GET | `/api/logs` | `limit=100, level=` | 事件日志 |
| GET | `/api/requests` | `limit=50` | 最近的模型调用记录 |

## 备份

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/export` | 导出全部数据 JSON |
| POST | `/api/import` | Body `{"payload": <导出的 JSON>}`，覆盖导入 |

## 内部接口（AstrBot 使用）

### POST `/api/handle`

需要 `X-Brain-Token`。这是整条链路的入口。

```json
{
  "platform": "qq",
  "group_id": "123456789",
  "group_name": "测试群",
  "user_id": "10001",
  "nickname": "阿伟",
  "text": "在吗",
  "images": ["data:image/jpeg;base64,..."],
  "faces": ["14"],
  "at_me": true,
  "is_private": false,
  "msg_id": "123",
  "ts": 1730000000
}
```

返回：

```json
{
  "reply": "在，咋了",
  "bubbles": ["在，咋了", "说正事"],
  "faces": [14],
  "images": ["https://p.qpic.cn/.../0"],
  "reason": "random:1.0",
  "msg_id": "123"
}
```

可能为 `{"reply": null, "reason": "batched"}`（已并入合并窗口）或
`{"reply": null, "reason": "rate:0.25"}`（本轮不回）。

### POST `/api/chat`

同一个 Body，但强制 `platform=sim`、`at_me=true`，用于控制台「对话调试」页。
不会发到 QQ，只返回结果。

### POST `/api/heartbeat`

需要 `X-Brain-Token`。AstrBot 插件每分钟调用一次，用作存活心跳。

```json
{"self_id": "1000000000"}
```
