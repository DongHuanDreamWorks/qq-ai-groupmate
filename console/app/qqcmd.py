"""Bridge to NapCat's internal WebApi via the plugin command file."""

from __future__ import annotations

import asyncio
import json
import os
import time

CMD_DIR = os.environ.get("QQCMD_DIR", "/qqcmd")
_lock = asyncio.Lock()


async def run(cmd: dict, timeout: float = 60.0) -> dict:
    async with _lock:
        seq = int(time.time() * 1000)
        payload = {"seq": seq, **cmd}
        cmd_path = os.path.join(CMD_DIR, "qqcmd.json")
        res_path = os.path.join(CMD_DIR, "qqcmd_result.json")
        with open(cmd_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(0.7)
            try:
                with open(res_path, encoding="utf-8") as fh:
                    result = json.load(fh)
            except Exception:  # noqa: BLE001
                continue
            if int(result.get("seq") or 0) == seq:
                return result
        raise TimeoutError("NapCat 插件没有在规定时间内返回结果")
