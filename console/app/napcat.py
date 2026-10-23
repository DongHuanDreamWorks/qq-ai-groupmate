"""Sidecar: listens to NapCat's forward WebSocket for request events and
auto-approves friend / group invitations according to console policy.

AstrBot does not forward OneBot `request` events to plugins, so this runs
independently and only needs the same docker network.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import aiohttp

from . import store

NAPCAT_WS = os.environ.get("NAPCAT_WS", "ws://napcat:3001")
_seen: dict[str, float] = {}
STATE: dict[str, Any] = {"connected": False, "last_event": 0, "last_error": ""}
_ws: aiohttp.ClientWebSocketResponse | None = None
_pending: dict[str, asyncio.Future] = {}


async def call(action: str, params: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
    """Call a OneBot action over the existing NapCat WebSocket."""
    ws = _ws
    if ws is None or ws.closed:
        raise RuntimeError("NapCat 事件通道未连接")
    echo = f"{action}-{time.time_ns()}"
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending[echo] = future
    try:
        await ws.send_str(json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False))
        return await asyncio.wait_for(future, timeout)
    finally:
        _pending.pop(echo, None)


async def _call(ws: aiohttp.ClientWebSocketResponse, action: str, params: dict[str, Any]) -> None:
    payload = {"action": action, "params": params, "echo": f"brain-{int(time.time() * 1000)}"}
    await ws.send_str(json.dumps(payload, ensure_ascii=False))


async def _handle_request(ws: aiohttp.ClientWebSocketResponse, event: dict[str, Any]) -> None:
    cfg = store.get_section("friend")
    flag = str(event.get("flag") or "")
    if not flag:
        return
    if flag in _seen and time.time() - _seen[flag] < 600:
        return
    _seen[flag] = time.time()

    kind = str(event.get("request_type") or "")
    user_id = str(event.get("user_id") or "")
    group_id = str(event.get("group_id") or "")
    comment = str(event.get("comment") or "")

    if not cfg.get("enabled"):
        store.log_request(kind, user_id, group_id, "", comment, flag, False, "auto-approve disabled")
        return

    blacklist = [str(x) for x in (cfg.get("blacklist") or [])]
    if user_id in blacklist:
        store.log_request(kind, user_id, group_id, "", comment, flag, False, "blacklist")
        return

    whitelist = [str(x) for x in (cfg.get("whitelist") or [])]
    approved = False
    reason = ""
    if kind == "friend":
        keyword = str(cfg.get("friend_keyword") or "").strip()
        if whitelist:
            # strict mode: only listed QQ numbers may be approved
            approved = bool(cfg.get("auto_accept_friend")) and user_id in whitelist
        else:
            approved = bool(cfg.get("auto_accept_friend")) and (not keyword or keyword in comment)
        reason = "" if approved else ("not-in-whitelist" if whitelist else "policy")
    elif kind == "group":
        keyword = str(cfg.get("group_keyword") or "").strip()
        if whitelist:
            approved = bool(cfg.get("auto_accept_group")) and user_id in whitelist
        else:
            approved = bool(cfg.get("auto_accept_group")) and (not keyword or keyword in comment)
        reason = "" if approved else ("not-in-whitelist" if whitelist else "policy")
    else:
        reason = "unknown-type"

    try:
        if kind == "friend":
            await _call(ws, "set_friend_add_request", {"flag": flag, "approve": approved, "remark": ""})
        elif kind == "group":
            await _call(
                ws,
                "set_group_add_request",
                {
                    "flag": flag,
                    "sub_type": event.get("sub_type") or "add",
                    "approve": approved,
                    "reason": "管理员未放行" if not approved else "",
                },
            )
    except Exception as exc:  # noqa: BLE001
        store.log_event("warn", "napcat", f"处理请求失败 {kind}/{user_id}: {exc}")
        return

    store.log_request(kind, user_id, group_id, "", comment, flag, approved, reason)
    store.log_event(
        "info",
        "napcat",
        f"{'同意' if approved else '拒绝'}{'好友' if kind == 'friend' else '入群'}请求 user={user_id} group={group_id} comment={comment[:40]}",
    )

    welcome = str(cfg.get("welcome") or "").strip()
    if approved and kind == "friend" and welcome:
        await _call(ws, "send_private_msg", {"user_id": int(user_id) if user_id.isdigit() else user_id, "message": welcome})


async def run() -> None:
    global _ws
    backoff = 5
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(NAPCAT_WS, heartbeat=30, max_msg_size=0) as ws:
                    _ws = ws
                    if not STATE["connected"]:
                        store.log_event("info", "napcat", f"已连接 NapCat 事件通道 {NAPCAT_WS}")
                    STATE["connected"] = True
                    STATE["last_error"] = ""
                    backoff = 5
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        STATE["last_event"] = int(time.time())
                        try:
                            event = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        echo = event.get("echo")
                        if echo and echo in _pending:
                            fut = _pending.get(echo)
                            if fut and not fut.done():
                                fut.set_result(event)
                            continue
                        if event.get("post_type") == "request":
                            await _handle_request(ws, event)
                    _ws = None
                    STATE["connected"] = False
                    for fut in list(_pending.values()):
                        if not fut.done():
                            fut.set_exception(RuntimeError("NapCat 连接关闭"))
                    _pending.clear()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            for fut in list(_pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError(f"NapCat 连接断开: {exc}"))
            _pending.clear()
            was_connected = STATE["connected"]
            STATE["connected"] = False
            message = str(exc)
            if was_connected or message != STATE.get("last_error"):
                store.log_event("warn", "napcat", f"事件通道未连接（{message}），{backoff}s 后重试")
            STATE["last_error"] = message
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)
