"""Random-interval proactive chatter.

Every group gets its own random next-wake time inside [min_minutes, max_minutes].
When it fires we check quiet hours, idle window, and the daily cap, then ask the
brain for one short unprompted line and send it through NapCat.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime

from . import brain, napcat, store

_next_at: dict[str, float] = {}
_daily: dict[str, int] = {}
_daily_date = datetime.now(brain.TZ).strftime("%Y-%m-%d")
_last_error: str = ""


def _today() -> str:
    return datetime.now(brain.TZ).strftime("%Y-%m-%d")


def _target_groups(cfg: dict) -> list[str]:
    groups = [str(g).strip() for g in (cfg.get("groups") or []) if str(g).strip()]
    if groups:
        return groups
    return [str(g["group_id"]) for g in store.groups() if g["platform"] == "qq"][:5]


async def _send(game_group: str, payload: dict) -> bool:
    gid: int | str = int(game_group) if str(game_group).isdigit() else game_group
    for index, bubble in enumerate(payload["bubbles"]):
        await napcat.call(
            "send_group_msg",
            {"group_id": gid, "message": [{"type": "text", "data": {"text": bubble}}]},
        )
        if index < len(payload["bubbles"]) - 1:
            await asyncio.sleep(1.1)
    for face_id in payload.get("faces") or []:
        await asyncio.sleep(0.5)
        await napcat.call(
            "send_group_msg",
            {"group_id": gid, "message": [{"type": "face", "data": {"id": str(face_id)}}]},
        )
    for img in payload.get("images") or []:
        await asyncio.sleep(0.8)
        if isinstance(img, dict) and img.get("b64"):
            await napcat.call(
                "send_group_msg",
                {"group_id": gid, "message": [{"type": "image", "data": {"file": "base64://" + img["b64"]}}]},
            )
        elif isinstance(img, str):
            await napcat.call(
                "send_group_msg",
                {"group_id": gid, "message": [{"type": "image", "data": {"file": img}}]},
            )
    return True


async def send_now(gid: str) -> dict | None:
    """Compose and send one proactive message right now (used by scheduler and UI)."""
    payload = await brain.compose_proactive("qq", gid)
    if not payload:
        return None
    await _send(gid, payload)
    bot_name = store.get_section("persona").get("name") or "小语"
    today = _today()
    store.add_message(
        "qq", gid, "qq:self", bot_name, "assistant", payload["text"],
        {"faces": payload.get("faces") or [], "images_count": len(payload.get("images") or [])},
        int(time.time()),
    )
    brain.note_reply(f"qq:{gid}")
    _daily[f"{today}:{gid}"] = _daily.get(f"{today}:{gid}", 0) + 1
    store.log_event("info", "proactive", f"群 {gid} 主动发言: {payload['text'][:40]}")
    return payload


async def tick() -> None:
    global _daily_date, _last_error
    cfg = store.get_section("proactive")
    if not cfg.get("enabled"):
        return
    if brain.in_quiet_hours(cfg.get("quiet_hours") or ""):
        return
    if not napcat.STATE.get("connected"):
        return

    today = _today()
    if today != _daily_date:
        _daily_date = today
        _daily.clear()

    now = time.time()
    min_min = max(5.0, float(cfg.get("min_minutes") or 45))
    max_min = max(min_min + 1, float(cfg.get("max_minutes") or 180))
    idle_min = max(1.0, float(cfg.get("idle_min_minutes") or 10))
    idle_max = max(idle_min + 1, float(cfg.get("idle_max_minutes") or 240))
    cap = int(cfg.get("max_per_day") or 6)

    for gid in _target_groups(cfg):
        key = f"qq:{gid}"
        nxt = _next_at.get(key)
        if nxt is None:
            _next_at[key] = now + random.uniform(min_min, max_min) * 60
            store.log_event(
                "info", "proactive", f"群 {gid} 下次主动发言约 {int((_next_at[key] - now) / 60)} 分钟后"
            )
            continue
        if now < nxt:
            continue

        # reschedule before doing anything, so a failure never causes a hot loop
        _next_at[key] = now + random.uniform(min_min, max_min) * 60

        if _daily.get(f"{today}:{gid}", 0) >= cap:
            continue

        rows = store.recent_messages("qq", gid, 1)
        if rows:
            idle = now - int(rows[-1]["ts"])
            if idle < idle_min * 60:
                _next_at[key] = now + 600
                continue
            if idle > idle_max * 60:
                continue
        # no rows at all: cold-start group, let it open with a lore-based line

        try:
            await send_now(gid)
        except Exception as exc:  # noqa: BLE001
            _last_error = str(exc)
            store.log_event("warn", "proactive", f"群 {gid} 主动发言发送失败: {exc}")


async def run() -> None:
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            store.log_event("warn", "proactive", f"调度异常: {exc}")
        await asyncio.sleep(30)


def status() -> dict:
    cfg = store.get_section("proactive")
    now = time.time()
    groups = []
    for gid in _target_groups(cfg):
        key = f"qq:{gid}"
        groups.append(
            {
                "group_id": gid,
                "next_at": int(_next_at.get(key, 0)),
                "in_seconds": max(0, int(_next_at.get(key, 0) - now)) if _next_at.get(key) else None,
                "today": _daily.get(f"{_today()}:{gid}", 0),
            }
        )
    return {"enabled": bool(cfg.get("enabled")), "groups": groups, "last_error": _last_error}
