"""Health checks and console alerts."""

from __future__ import annotations

import asyncio
import os
import time

from . import napcat, store


def _disk_usage() -> float:
    try:
        st = os.statvfs("/")
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        total = st.f_blocks * st.f_frsize
        return used / total if total else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


async def tick() -> None:
    now = time.time()

    if not napcat.STATE.get("connected"):
        store.add_alert(
            "error", "qq", "QQ 掉线或被强制下线：去「概览」页扫码重登，或点「重新生成二维码」"
        )

    events = store.recent_events(80)
    beat = next((e["ts"] for e in events if e["source"] == "heartbeat"), 0)
    if beat and now - beat > 300:
        store.add_alert("error", "heartbeat", f"机器人心跳停止 {int((now - beat) / 60)} 分钟，AstrBot 可能挂了")

    calls = store.recent_llm(20)
    if len(calls) >= 6:
        failures = sum(1 for c in calls if not c.get("ok"))
        if failures / len(calls) > 0.5:
            store.add_alert("warn", "llm", f"最近 {len(calls)} 次模型调用失败 {failures} 次，检查 Key/余额/网络")

    usage = _disk_usage()
    if usage > 0.9:
        store.add_alert("warn", "disk", f"磁盘使用率 {usage * 100:.0f}%，快满了")


async def run() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            store.log_event("warn", "alerts", f"检查异常: {exc}")
        await asyncio.sleep(60)
