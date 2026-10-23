"""Self-management: profile (nickname / signature / avatar) and Qzone posts.

Profile calls go straight through NapCat's OneBot actions.
Qzone calls are handed to the NapCat plugin via a shared command file, because
Qzone publishing lives on NapCat's internal WebApi, not on OneBot.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

from . import brain, napcat, qqcmd, store

_last_error = ""

VIS_LABELS = {1: "公开", 4: "好友可见", 64: "仅自己"}


async def _run_plugin_cmd(cmd: dict, timeout: float = 45.0) -> dict:
    return await qqcmd.run(cmd, timeout)


async def get_profile() -> dict:
    info = await napcat.call("get_login_info", {})
    data = info.get("data") or {}
    cfg = store.get_section("selfops")
    return {
        "user_id": data.get("user_id"),
        "nickname": data.get("nickname"),
        "signature": cfg.get("signature") or "",
        "avatar_url": cfg.get("avatar_url") or "",
    }


async def set_profile(nickname: str, signature: str) -> dict:
    payload = {}
    if nickname:
        payload["nickname"] = nickname
    if signature:
        payload["personal_note"] = signature
    result = await napcat.call("set_qq_profile", payload) if payload else {}
    cfg = store.get_section("selfops")
    if nickname:
        cfg["nickname"] = nickname
    if signature:
        cfg["signature"] = signature
    store.save_section("selfops", cfg)
    return result


async def set_avatar(url: str) -> dict:
    result = await napcat.call("set_qq_avatar", {"file": url})
    cfg = store.get_section("selfops")
    cfg["avatar_url"] = url
    store.save_section("selfops", cfg)
    return result


async def qzone_publish(content: str, visibility: int = 1, auto: bool = False) -> str:
    content = (content or "").strip()
    if not content:
        raise ValueError("内容不能为空")
    result = await _run_plugin_cmd(
        {"action": "qzone_publish", "content": content, "ugc_right": int(visibility)}
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "发布失败")
    tid = str((result.get("result") or {}).get("tid") or "")
    store.add_qzone_post(int(time.time()), content, tid, int(visibility), auto)
    store.log_event("info", "qzone", f"发布说说 {VIS_LABELS.get(int(visibility), visibility)}: {content[:40]}")
    return tid


async def qzone_delete(tid: str) -> None:
    result = await _run_plugin_cmd({"action": "qzone_delete", "tid": tid})
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "删除失败")
    store.mark_qzone_deleted(tid)


async def draft() -> str:
    settings = store.get_settings()
    persona = settings.get("persona", {})
    name = persona.get("name") or "小肥鱼"
    identity = persona.get("identity") or ""
    lore = brain.pick_lore(persona.get("lore") or [], 2)
    now = datetime.now(brain.TZ)
    prompt = (
        f"你是「{name}」，{identity}。\n"
        + (f"你最近的生活碎片：\n- " + "\n- ".join(lore) + "\n" if lore else "")
        + f"\n现在是 {now.strftime('%m月%d日 %H:%M')}，你要发一条 QQ 空间说说。\n"
        "要求：\n"
        "- 40~80 字，1~3 句，口语，像真人在自己空间随手写的\n"
        "- 具体、有细节，可以吐槽、自嘲、记录一件小事，或者一句突然想到的话\n"
        "- 不要鸡汤、不要励志、不要营销号腔、不要「今天也要加油鸭」\n"
        "- 最多一个表情符号\n"
        "- 不要提自己是 AI，不要提群\n"
        "直接输出说说正文，不要标题、不要引号。"
    )
    resp = await brain.call_llm(
        [{"role": "user", "content": prompt}], kind="qzone_draft", max_tokens=300, temperature=1.0
    )
    return brain.strip_markdown(resp["text"]).strip()


def _minutes(hhmm: str) -> int:
    try:
        hh, mm = hhmm.split(":")
        return int(hh) * 60 + int(mm)
    except Exception:  # noqa: BLE001
        return 20 * 60 + 30


async def tick() -> None:
    global _last_error
    cfg = store.get_section("selfops")
    if not cfg.get("auto_qzone"):
        return
    if not napcat.STATE.get("connected"):
        return
    now = datetime.now(brain.TZ)
    if now.hour * 60 + now.minute < _minutes(cfg.get("qzone_time") or "20:30"):
        return
    today = now.strftime("%Y-%m-%d")
    for post in store.list_qzone_posts(20):
        if post.get("auto") and datetime.fromtimestamp(post["ts"], brain.TZ).strftime("%Y-%m-%d") == today:
            return
    try:
        text = await draft()
        await qzone_publish(text, int(cfg.get("qzone_visibility") or 1), auto=True)
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        store.log_event("warn", "qzone", f"自动说说失败: {exc}")


async def run() -> None:
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            store.log_event("warn", "qzone", f"调度异常: {exc}")
        await asyncio.sleep(120)


def status() -> dict:
    return {"last_error": _last_error, "posts": store.list_qzone_posts(20), "vis": VIS_LABELS}
