"""Midnight context rotation.

Once a day (default 00:00) each active group's transcript is archived:
the model summarises it, writes a note to its future self, and picks the
facts worth keeping. The transcript is then cleared and the next session
starts with that note injected.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from . import brain, store

_last_date = ""
_last_error = ""


def _minutes(hhmm: str) -> int:
    try:
        hh, mm = hhmm.split(":")
        return int(hh) * 60 + int(mm)
    except Exception:  # noqa: BLE001
        return 0


async def rotate_group(gkey: str, rows: list[dict]) -> str | None:
    platform, group_id = gkey.split(":", 1)
    persona = store.get_section("persona")
    name = persona.get("name") or "小肥鱼"
    transcript = "\n".join(f"{r.get('nickname')}：{(r.get('text') or '')[:200]}" for r in rows[-200:])
    prompt = (
        f"你是「{name}」。现在是午夜，群里今天的对话要归档了，明天开始是新的一段对话。\n\n"
        f"今天群里的聊天记录：\n{transcript}\n\n"
        "请你做三件事，只输出 JSON：\n"
        "{\n"
        '  "summary": "150 字以内，总结今天聊了什么、气氛如何、有没有没说完的事",\n'
        '  "injection": "100 字以内，写给明天刚醒来的自己：哪些人和事值得注意、哪些话题别主动提、'
        '你明天该用什么姿态出现。用第一人称，像自言自语。",\n'
        '  "important": ["3-8 条你认为值得长期记住的事，每条一句话，人物/关系/约定/梗都行"]\n'
        "}\n\n"
        "不要输出 JSON 以外的内容。"
    )
    resp = await brain.call_llm(
        [{"role": "user", "content": prompt}],
        kind="rotation",
        max_tokens=900,
        temperature=0.4,
        json_mode=True,
    )
    data = brain._parse_json(resp["text"])
    summary = str(data.get("summary") or "").strip()
    if not summary:
        return None
    injection = str(data.get("injection") or "").strip()
    important = [str(x).strip() for x in (data.get("important") or []) if str(x).strip()][:8]
    store.add_rotation(gkey, summary, injection, important, len(rows))
    store.clear_context(platform, group_id)
    store.log_event("info", "rotation", f"{gkey} 归档 {len(rows)} 条：{summary[:40]}")
    return summary


async def tick() -> None:
    global _last_date, _last_error
    cfg = store.get_section("context")
    if not cfg.get("rotate_enabled", True):
        return
    now = datetime.now(brain.TZ)
    target = _minutes(cfg.get("rotate_hour") or "00:00")
    minute_of_day = now.hour * 60 + now.minute
    # only run in the two hours after the target, so a midday restart never rotates
    if not (target <= minute_of_day < target + 120):
        return
    today = now.strftime("%Y-%m-%d")
    if _last_date == today:
        return
    _last_date = today
    threshold = int(cfg.get("rotate_threshold") or 40)
    for group in store.groups():
        if group.get("platform") != "qq":
            continue
        gkey = f"{group['platform']}:{group['group_id']}"
        last = store.latest_rotation(gkey)
        if last and datetime.fromtimestamp(int(last["created_at"]), brain.TZ).strftime("%Y-%m-%d") == today:
            continue
        rows = store.recent_messages(group["platform"], group["group_id"], 200)
        if len(rows) < threshold:
            continue
        try:
            await rotate_group(gkey, rows)
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
            store.log_event("warn", "rotation", f"{gkey} 归档失败: {exc}")


async def run() -> None:
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            store.log_event("warn", "rotation", f"调度异常: {exc}")
        await asyncio.sleep(120)


def status() -> dict:
    return {"last_date": _last_date, "last_error": _last_error, "rotations": store.list_rotations(10)}
