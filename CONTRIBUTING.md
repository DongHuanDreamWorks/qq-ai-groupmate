# 参与开发

欢迎 PR，但请先读这几条。

## 不要提交

- 任何真实 QQ 号、密码、Cookie、二维码
- 任何 API Key、Token、License
- `data/` 下的数据库、日志、聊天记录
- 服务器 IP、域名、SSH 凭据

提交前跑一遍：

```bash
git status
git grep -nE 'sk-[A-Za-z0-9]{16,}|[0-9]{9,}|BEGIN (RSA|OPENSSH)'
```

## 本地开发

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

BRAIN_DB=./data/dev.db BRAIN_TOKEN=dev \
DEEPSEEK_API_KEY=sk-... \
python -m uvicorn console.app.main:app --reload --port 8088
```

控制台不依赖 QQ 也能跑：用「对话调试」页的模拟器测完整流程。

## 代码风格

- Python 3.11，标准库优先；类型标注尽量写。
- 所有会写数据库的路径统一走 `store` 里的函数，不要到处裸 SQL。
- 新的后台任务要有：启动入口、频率、失败日志、token 成本说明（写进 docs/ARCHITECTURE.md）。
- 新的提示词要短，能在 `docs/ARCHITECTURE.md` 里解释清楚“为什么这么写”。

## 文档

改行为就改文档，尤其是：

- `docs/API.md`（接口变了）
- `docs/PANEL.md`（页面变了）
- `docs/ARCHITECTURE.md`（结构/调度变了）
