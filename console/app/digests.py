"""Per-person rolling digests, used for cross-context recall."""

from __future__ import annotations

import asyncio

from . import brain, store

_last_error = ""


async def refresh_user(uid: str) -> None:
    user = store.get_user(uid)
    if not user:
        return
    total = int(user.get("msg_count") or 0)
    if total < 5:
        return
    old = store.get_person_digest(uid)
    if old and total < int(old.get("msg_count") or 0) + 5:
        return
    rows = store.messages_for_uid(uid, 40)
    if len(rows) < 5:
        return
    name = user.get("display_name") or user.get("nickname") or uid
    transcript = "\n".join(f"- {(r.get('text') or '')[:120]}" for r in rows[-40:])
    prompt = (
        f"下面是群友「{name}」最近在群里说的话：\n{transcript}\n\n"
        "请输出 JSON：\n"
        '{"summary":"80 字以内，概括 TA 最近在关心什么、情绪如何、和谁互动多",'
        '"topics":["3 个话题标签，每个不超过 6 字"]}'
    )
    resp = await brain.call_llm(
        [{"role": "user", "content": prompt}],
        kind="digest",
        max_tokens=300,
        temperature=0.3,
        json_mode=True,
    )
    data = brain._parse_json(resp["text"])
    summary = str(data.get("summary") or "").strip()
    if not summary:
        return
    topics = [str(x).strip() for x in (data.get("topics") or []) if str(x).strip()][:4]
    store.upsert_person_digest(uid, summary, topics, total)


async def tick() -> None:
    global _last_error
    for user in store.list_users(limit=100):
        if int(user.get("msg_count") or 0) < 8:
            continue
        try:
            await refresh_user(user["uid"])
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
            store.log_event("warn", "digest", f"{user['uid']} 人物摘要失败: {exc}")


async def run() -> None:
    await asyncio.sleep(40)
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            store.log_event("warn", "digest", f"调度异常: {exc}")
        await asyncio.sleep(6 * 3600)


def status() -> dict:
    return {"digests": store.list_person_digests(50), "last_error": _last_error}
