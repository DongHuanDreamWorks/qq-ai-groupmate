"""QQ 表情收藏（自定义表情）同步、识别与挑选。"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import Any

import httpx

from . import brain, napcat, qqcmd, store

_last_sync = 0.0
_last_error = ""
_syncing = False

DESC_PROMPT = (
    "这是 QQ 群里常用的表情包。用一句中文说清楚：画面是什么、是什么情绪、"
    "适合在什么语境下发。40 字以内，不要解释，不要用引号。"
)


async def _describe(data: bytes, mime: str) -> str:
    b64 = base64.b64encode(data).decode()
    resp = await brain.call_llm(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": DESC_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ],
        kind="sticker",
        max_tokens=120,
        temperature=0.4,
    )
    return brain.strip_markdown(resp["text"]).strip()[:60]


async def sync() -> dict[str, Any]:
    global _last_sync, _last_error, _syncing
    if _syncing:
        return {"ok": False, "error": "正在同步中"}
    _syncing = True
    try:
        resp = await napcat.call("fetch_custom_face_detail", {"count": 200})
        items = resp.get("data") or []
        described = 0
        for item in items:
            res_id = str(item.get("resId") or "")
            if not res_id:
                continue
            url = str(item.get("url") or "")
            md5 = str(item.get("md5") or "")
            emoji_id = int(item.get("emoId") or 0)
            desc = str(item.get("desc") or "").strip()
            if not desc and url:
                try:
                    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                        img = await client.get(url)
                    if img.status_code == 200 and img.content:
                        desc = await _describe(img.content, img.headers.get("content-type", "image/jpeg"))
                        described += 1
                        try:
                            await napcat.call(
                                "set_custom_face_desc",
                                {"emoji_id": emoji_id, "res_id": res_id, "md5": md5, "desc": desc},
                            )
                        except Exception as exc:  # noqa: BLE001
                            store.log_event("warn", "sticker", f"描述写回 QQ 失败 {res_id}: {exc}")
                except Exception as exc:  # noqa: BLE001
                    store.log_event("warn", "sticker", f"识别表情失败 {res_id}: {exc}")
            store.upsert_sticker(res_id, emoji_id, md5, url, desc)
        _last_sync = time.time()
        store.log_event("info", "sticker", f"表情同步完成：{len(items)} 个，新识别 {described} 个")
        return {"ok": True, "total": len(items), "described": described}
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        store.log_event("warn", "sticker", f"表情同步失败: {exc}")
        return {"ok": False, "error": str(exc)}
    finally:
        _syncing = False


def pick(query: str = "") -> dict[str, Any] | None:
    rows = store.list_stickers()
    if not rows:
        return None
    q = (query or "").strip().lower()
    if not q:
        return rows[0]

    def score(row: dict[str, Any]) -> int:
        text = f"{row.get('desc') or ''} {row.get('tags') or ''}".lower()
        total = 5 if q and q in text else 0
        for token in re.split(r"[\s,，。;；]+", q):
            if not token:
                continue
            if token in text:
                total += 2
                continue
            for i in range(len(token) - 1):
                if token[i : i + 2] in text:
                    total += 1
        return total

    ranked = sorted(rows, key=lambda r: (score(r), -int(r.get("use_count") or 0)), reverse=True)
    return ranked[0] if ranked else None


async def save_from_b64(b64: str, desc: str = "") -> dict[str, Any]:
    res = await qqcmd.run(
        {"action": "add_custom_face", "b64": b64, "desc": desc},
        timeout=60,
    )
    if not res.get("ok"):
        return {"error": res.get("error") or "收藏失败"}
    await asyncio.sleep(2)
    await sync()
    return {"ok": True}


def prompt_hint(limit: int = 18) -> str:
    rows = store.list_stickers()[:limit]
    if not rows:
        return ""
    lines = [f"- {r.get('desc') or '（还没描述）'}" for r in rows]
    return "你收藏的表情（要发的时候用 send_sticker，描述你想表达的感觉即可）：\n" + "\n".join(lines)


async def run() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            if time.time() - _last_sync > 6 * 3600:
                await sync()
        except Exception as exc:  # noqa: BLE001
            store.log_event("warn", "sticker", f"定时同步异常: {exc}")
        await asyncio.sleep(1800)


def status() -> dict[str, Any]:
    return {"count": store.sticker_count(), "last_sync": _last_sync, "last_error": _last_error}
