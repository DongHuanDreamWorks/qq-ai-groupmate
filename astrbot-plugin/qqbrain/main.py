"""AstrBot bridge plugin: QQ transport only, all thinking happens in the console.

Flow:
    NapCat -> AstrBot (this plugin) -> console /api/handle -> DeepSeek
                                  <- reply bubbles / faces
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import random
import time
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Face, Image, Plain, Reply
from astrbot.api.star import Context, Star

BRAIN_URL = os.environ.get("BRAIN_URL", "http://console:8088").rstrip("/")
BRAIN_TOKEN = os.environ.get("BRAIN_TOKEN", "")
HTTP_TIMEOUT = float(os.environ.get("BRAIN_TIMEOUT", "90"))


class QQBrainBridge(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._session: aiohttp.ClientSession | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._seen: dict[str, float] = {}
        self._hb_task: asyncio.Task | None = None
        self.self_id = ""

    # ------------------------------------------------------------ plumbing

    async def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
                headers={"X-Brain-Token": BRAIN_TOKEN, "Content-Type": "application/json"},
            )
        return self._session

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        try:
            session = await self._client()
            async with session.post(f"{BRAIN_URL}{path}", json=payload) as resp:
                body = await resp.text()
                if resp.status != 200:
                    logger.warning(f"[qqbrain] {path} -> HTTP {resp.status}: {body[:300]}")
                    return None
                return await resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[qqbrain] 请求大脑失败 {path}: {exc}")
            return None

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    # ------------------------------------------------------------ lifecycle

    @filter.on_astrbot_loaded()
    async def _on_loaded(self) -> None:
        logger.info(f"[qqbrain] bridge ready, brain={BRAIN_URL}")
        self._hb_task = asyncio.create_task(self._heartbeat())

    async def _heartbeat(self) -> None:
        while True:
            try:
                await self._post("/api/heartbeat", {"self_id": self.self_id})
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(60)

    async def terminate(self) -> None:
        if self._hb_task:
            self._hb_task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------ messages

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        message_id = str(getattr(event.message_obj, "message_id", "") or "")
        now = time.time()
        if message_id:
            if message_id in self._seen:
                return
            self._seen[message_id] = now
            if len(self._seen) > 500:
                cutoff = now - 600
                self._seen = {k: v for k, v in self._seen.items() if v > cutoff}

        self.self_id = str(event.get_self_id() or self.self_id)
        group_id = str(event.get_group_id() or "")
        is_private = not group_id

        text = (event.message_str or "").strip()
        images: list[str] = []
        faces: list[Any] = []
        at_me = False
        quote_text = ""

        for comp in event.message_obj.message:
            if isinstance(comp, Image):
                url = str(comp.url or "")
                file_ref = str(comp.file or "")
                path = str(getattr(comp, "path", "") or "")
                if url.startswith("http"):
                    images.append(url)
                elif file_ref.startswith("http"):
                    images.append(file_ref)
                elif file_ref.startswith("base64://"):
                    images.append("data:image/jpeg;base64," + file_ref[len("base64://"):])
                else:
                    candidate = path or (file_ref[len("file://"):] if file_ref.startswith("file://") else file_ref)
                    try:
                        if candidate and os.path.isfile(candidate):
                            size = os.path.getsize(candidate)
                            if size <= 8 * 1024 * 1024:
                                with open(candidate, "rb") as fh:
                                    raw = fh.read()
                                mime = mimetypes.guess_type(candidate)[0] or "image/jpeg"
                                images.append(f"data:{mime};base64,{base64.b64encode(raw).decode()}")
                            else:
                                logger.warning(f"[qqbrain] 图片过大已跳过: {candidate}")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[qqbrain] 读取图片失败 {candidate}: {exc}")
            elif isinstance(comp, Face):
                faces.append(comp.id)
            elif isinstance(comp, At):
                if str(getattr(comp, "qq", "")) == str(event.get_self_id()):
                    at_me = True
            elif isinstance(comp, Reply):
                quote_text = str(
                    getattr(comp, "message_str", "")
                    or getattr(comp, "text", "")
                    or ""
                )[:200]

        if at_me and not text and not images:
            text = "（@了你一下）"
        if not text and not images and not faces:
            return

        payload = {
            "platform": "qq",
            "group_id": group_id or f"private:{event.get_sender_id()}",
            "group_name": "",
            "user_id": str(event.get_sender_id()),
            "nickname": str(event.get_sender_name() or "群友"),
            "text": text,
            "images": images,
            "faces": faces,
            "at_me": at_me,
            "is_private": is_private,
            "msg_id": message_id,
            "quote": quote_text,
            "ts": int(getattr(event.message_obj, "timestamp", 0) or time.time()),
        }

        # This plugin owns every reply. Stop AstrBot's own LLM/command pipeline
        # so the same message never gets two answers.
        event.stop_event()

        result = await self._post("/api/handle", payload)

        if not result:
            return
        if not result.get("reply") and not result.get("images"):
            return

        delay = min(float(result.get("delay_ms") or 0) / 1000.0, 3.0)
        if delay > 0:
            await asyncio.sleep(delay)

        bubbles = [b for b in (result.get("bubbles") or [result.get("reply") or ""]) if str(b).strip()]
        faces = result.get("faces") or []
        for index, bubble in enumerate(bubbles):
            chain: list[Any] = []
            if index == 0 and message_id and result.get("quote", True):
                try:
                    chain.append(Reply(id=message_id))
                except Exception:  # noqa: BLE001
                    pass
            chain.append(Plain(str(bubble)))
            if index == len(bubbles) - 1:
                for face_id in faces:
                    try:
                        chain.append(Face(id=int(face_id)))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[qqbrain] 表情段构造失败 {face_id}: {exc}")
            yield event.chain_result(chain)
            if index < len(bubbles) - 1:
                await asyncio.sleep(random.uniform(1.0, 2.2))

        for item in result.get("images") or []:
            try:
                await asyncio.sleep(0.6)
                if isinstance(item, dict) and item.get("b64"):
                    img = Image.fromBase64(item["b64"])
                else:
                    img = Image(file=str(item))
                yield event.chain_result([img])
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[qqbrain] 发送图片失败: {exc}")
