"""The brain: decides whether to speak, builds the prompt, calls DeepSeek,
and asynchronously maintains long-term impressions and group summaries."""

from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from . import media, qqcmd, stickers, store

TZ = timezone(timedelta(hours=8))

MAX_IMAGE_BYTES = 6 * 1024 * 1024
_digest_inflight: set[str] = set()
_batches: dict[str, float] = {}


async def _digest_job(uid: str) -> None:
    try:
        from . import digests

        await digests.refresh_user(uid)
    finally:
        _digest_inflight.discard(uid)


_used_lore: deque[str] = deque(maxlen=10)


def pick_lore(lore: list[str], k: int = 3) -> list[str]:
    """Sample lore lines, avoiding ones used recently, to stop the bot repeating itself."""
    if not lore:
        return []
    pool = [x for x in lore if x not in _used_lore] or list(lore)
    picks = random.sample(pool, min(k, len(pool)))
    _used_lore.extend(picks)
    return picks


_state_lock = asyncio.Lock()
_last_reply: dict[str, float] = {}
_reply_window: dict[str, deque[float]] = {}
_uid_counter: dict[str, int] = {}
_group_counter: dict[str, int] = {}
_task_sem = asyncio.Semaphore(2)
_inflight: set[str] = set()


# ---------------------------------------------------------------- utilities


def now_ts() -> int:
    return int(time.time())


def hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%H:%M")


def _parse_hours(spec: str) -> tuple[int, int] | None:
    try:
        a, b = spec.split("-")
        h1, m1 = [int(x) for x in a.split(":")]
        h2, m2 = [int(x) for x in b.split(":")]
        return h1 * 60 + m1, h2 * 60 + m2
    except Exception:
        return None


def in_quiet_hours(spec: str) -> bool:
    span = _parse_hours(spec or "")
    if not span:
        return False
    start, end = span
    cur = datetime.now(TZ)
    minutes = cur.hour * 60 + cur.minute
    if start <= end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def looks_url_only(text: str) -> bool:
    stripped = re.sub(r"https?://\S+", "", text).strip()
    return bool(re.search(r"https?://\S+", text)) and len(stripped) <= 2


def is_peak() -> bool:
    """DeepSeek peak: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri."""
    utc = datetime.now(timezone.utc)
    if utc.weekday() >= 5:
        return False
    minutes = utc.hour * 60 + utc.minute
    return (60 <= minutes < 240) or (360 <= minutes < 600)


def price_for(pt: int, ct: int, peak: bool) -> float:
    if peak:
        return pt / 1_000_000 * 0.30 + ct / 1_000_000 * 1.20
    return pt / 1_000_000 * 0.15 + ct / 1_000_000 * 0.60


def strip_markdown(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^\s*(?:小语|小肥鱼|「[^」]{1,8}」)\s*[:：]\s*", "", text)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"^[*\-•]\s+", "", text, flags=re.M)
    text = re.sub(r"\[(?:图片|语音|视频|文件|表情)[^\]]*\]", "", text)
    text = re.sub(r"<tool_result>.*?</tool_result>", "", text, flags=re.S)
    text = re.sub(r"<[^>\n]{1,20}>", "", text)
    text = re.sub(r"^你已发送表情[：:].*$", "", text, flags=re.M)
    return text.strip()


def split_bubbles(text: str, max_bubbles: int = 3, max_chars: int = 120) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = [p for p in re.split(r"(?<=[。！？!?~…\n])", text) if p.strip()]
    if not parts:
        parts = [text]
    bubbles: list[str] = []
    for part in parts:
        part = part.strip()
        while len(part) > max_chars:
            cut = max(part.rfind("，", 0, max_chars), part.rfind(",", 0, max_chars))
            cut = cut if cut > 10 else max_chars
            bubbles.append(part[: cut + 1].strip())
            part = part[cut + 1 :].strip()
        if part:
            bubbles.append(part)
        if len(bubbles) >= max_bubbles:
            break
    return [b for b in bubbles if b][:max_bubbles]


def fan_out_bubbles(bubbles: list[str], max_bubbles: int) -> list[str]:
    """Sometimes split a single long-ish line into two, the way people double-text."""
    if len(bubbles) != 1 or max_bubbles < 2:
        return bubbles
    text = bubbles[0]
    if len(text) < 14 or random.random() > 0.5:
        return bubbles
    for sep in ("，", "。", "！", "？", "~"):
        idx = text.find(sep, 6)
        if 0 <= idx < len(text) - 4:
            head = text[: idx + 1].strip()
            tail = text[idx + 1 :].strip()
            if head and tail:
                return [head, tail][:max_bubbles]
    return bubbles


def merge_faces(text: str, faces: list[Any], face_map: dict[str, str]) -> str:
    labels = []
    for face in faces or []:
        fid = str(face.get("id") if isinstance(face, dict) else face)
        name = face_map.get(fid)
        labels.append(f"[表情:{name}]" if name else f"[表情#{fid}]")
    if not labels:
        return text
    head = "".join(labels)
    return f"{text}{head}" if text else head


EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")

FACE_HINTS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("哈哈", "笑", "乐", "好笑", "逗", "梗"), ("13", "115", "105")),
    (("累", "困", "唉", "惨", "哭", "难受", "烦", "无语"), ("49", "5", "46")),
    (("？", "?", "吗", "呢", "啥", "怎么", "为什么"), ("31", "108")),
    (("牛", "强", "厉害", "赞", "棒", "好耶", "可以啊"), ("78", "79", "99")),
    (("晚安", "睡了", "拜拜"), ("74", "38")),
]


def pick_face(text: str, face_map: dict[str, str]) -> int | None:
    for keys, ids in FACE_HINTS:
        if any(k in text for k in keys):
            for fid in ids:
                if fid in face_map:
                    return int(fid)
    for fid in ("14", "13", "105"):
        if fid in face_map:
            return int(fid)
    return None


def extract_face_actions(text: str, face_map: dict[str, str]) -> tuple[str, list[int]]:
    reverse = {v: k for k, v in face_map.items()}
    ids: list[int] = []

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        fid = reverse.get(name)
        if fid is not None:
            try:
                ids.append(int(fid))
            except ValueError:
                pass
            return ""
        return match.group(0)

    cleaned = re.sub(r"\[表情[:：]([^\]]{1,10})\]", repl, text)
    return cleaned.strip(), ids


# ---------------------------------------------------------------- LLM call


async def call_llm(
    messages: list[dict[str, Any]],
    *,
    kind: str = "chat",
    max_tokens: int | None = None,
    temperature: float | None = None,
    json_mode: bool = False,
    timeout: float | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = store.get_settings()
    model_cfg = settings.get("model", {})
    api_key = model_cfg.get("api_key") or ""
    base = (model_cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    model = model_cfg.get("model") or "deepseek-flash"
    if not api_key:
        raise RuntimeError("DeepSeek API key 未配置")

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens or int(model_cfg.get("max_tokens") or 400),
        "temperature": temperature if temperature is not None else float(model_cfg.get("temperature") or 0.9),
        "stream": False,
    }
    if model_cfg.get("thinking"):
        payload["thinking"] = {"type": "enabled"}
        payload.pop("temperature", None)
    else:
        payload["thinking"] = {"type": "disabled"}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout or float(model_cfg.get("timeout") or 60)) as client:
            resp = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        latency = int((time.time() - started) * 1000)
        if resp.status_code != 200:
            body = resp.text[:400]
            store.log_llm(kind, model, 0, 0, 0.0, latency, False, f"HTTP {resp.status_code}: {body}")
            raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {body}")
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        cost = price_for(pt, ct, is_peak())
        store.log_llm(kind, model, pt, ct, cost, latency, True)
        return {
            "text": content,
            "message": choice.get("message") or {"role": "assistant", "content": content},
            "usage": usage,
            "cost": cost,
            "latency_ms": latency,
        }
    except Exception as exc:  # noqa: BLE001
        if not isinstance(exc, RuntimeError):
            latency = int((time.time() - started) * 1000)
            store.log_llm(kind, model, 0, 0, 0.0, latency, False, str(exc))
        raise


# ---------------------------------------------------------------- decision


def schedule_rate(trigger: dict[str, Any]) -> float:
    raw = trigger.get("schedule")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if not raw:
        return float(trigger.get("probability") or 0)
    now = datetime.now(TZ)
    minutes = now.hour * 60 + now.minute
    for slot in raw:
        try:
            sh, sm = [int(x) for x in str(slot.get("start", "00:00")).split(":")]
            eh, em = [int(x) for x in str(slot.get("end", "24:00")).split(":")]
            start, end = sh * 60 + sm, eh * 60 + em
            if start <= minutes < end:
                return float(slot.get("rate") or 0)
        except Exception:  # noqa: BLE001
            continue
    return 0.0


def decide(payload: dict[str, Any], settings: dict[str, Any]) -> tuple[bool, str]:
    trigger = settings.get("trigger", {})
    text = (payload.get("text") or "").strip()
    uid = f"{payload.get('platform', 'qq')}:{payload.get('user_id', '')}"
    group_key = f"{payload.get('platform', 'qq')}:{payload.get('group_id', '')}"

    if uid in set(trigger.get("blacklist_users") or []):
        return False, "blacklist"
    whitelist = [str(x) for x in (trigger.get("whitelist_groups") or [])]
    if whitelist and str(payload.get("group_id", "")) not in whitelist:
        return False, "group-not-whitelisted"

    rate = schedule_rate(trigger)
    if rate <= 0:
        return False, "quiet-hours"

    if payload.get("at_me") and trigger.get("reply_to_at", True):
        return True, "at"
    if payload.get("is_private") and trigger.get("reply_to_private", True):
        return True, "private"

    prefix = (trigger.get("prefix") or "").strip()
    if prefix and text.startswith(prefix):
        return True, "prefix"

    lowered = text.lower()
    for kw in trigger.get("keywords") or []:
        if kw and kw.lower() in lowered:
            return True, f"keyword:{kw}"

    if len(text) < int(trigger.get("min_chars") or 3):
        return False, "too-short"
    if trigger.get("ignore_url_only", True) and looks_url_only(text):
        return False, "url-only"
    for ip in trigger.get("ignore_prefixes") or []:
        if ip and text.startswith(ip):
            return False, "command"

    now = time.time()
    last = _last_reply.get(group_key, 0)
    if now - last < int(trigger.get("cooldown_sec") or 0):
        return False, "cooldown"
    window = _reply_window.setdefault(group_key, deque())
    while window and now - window[0] > 600:
        window.popleft()
    if len(window) >= int(trigger.get("max_per_10min") or 99):
        return False, "rate-limit"
    if random.random() > rate:
        return False, f"rate:{rate}"

    return True, f"random:{rate}"


def note_reply(group_key: str) -> None:
    now = time.time()
    _last_reply[group_key] = now
    _reply_window.setdefault(group_key, deque()).append(now)


STICKER_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("哈哈", "笑", "乐", "梗", "逗"), "爆笑 笑 搞笑 乐 开心"),
    (("无语", "离谱", "服了", "绝了", "绷不住"), "无语 无奈 呆滞 服了"),
    (("震惊", "卧槽", "居然", "真的假的"), "震惊 惊 吓 呆住"),
    (("累", "难受", "委屈", "想哭", "烦"), "委屈 难过 哭 安慰 累"),
    (("牛", "强", "厉害", "赞", "棒", "可以啊"), "赞 强 厉害 棒 牛"),
]


def maybe_auto_sticker(text: str, settings: dict[str, Any], images_out: list[Any]) -> None:
    cfg = settings.get("tools", {})
    if not cfg.get("sticker_enabled", True) or images_out:
        return
    raw_prob = cfg.get("sticker_auto_prob")
    prob = 0.55 if raw_prob is None else float(raw_prob)
    if random.random() > prob:
        return
    for keys, query in STICKER_HINTS:
        if any(k in text for k in keys):
            row = stickers.pick(query)
            if row and row.get("url"):
                images_out.append(row["url"])
                store.touch_sticker(str(row["res_id"]))
                store.log_event("info", "sticker", f"顺手发了个表情: {(row.get('desc') or '')[:30]}")
            return


# ---------------------------------------------------------------- prompt


def build_memory_block(user: dict[str, Any] | None, settings: dict[str, Any]) -> str:
    if not user:
        return ""
    mem_cfg = settings.get("memory", {})
    if not mem_cfg.get("enabled", True):
        return ""
    imp = user.get("impression") or {}
    dims = imp.get("dims") or {}
    familiarity = int(dims.get("familiarity") or 0)
    if not imp.get("summary") and familiarity < int(mem_cfg.get("recall_min_familiarity") or 0):
        return ""
    dim_names = {d.get("key"): d.get("name") for d in mem_cfg.get("dimensions") or []}
    dim_line = "，".join(
        f"{dim_names.get(k, k)} {int(v)}/100" for k, v in dims.items() if isinstance(v, (int, float))
    )
    display = user.get("display_name") or user.get("nickname") or user.get("uid")
    lines = [f"【你对 {display} 的了解】"]
    if dim_line:
        lines.append(dim_line)
    if imp.get("summary"):
        lines.append(f"印象：{imp['summary']}")
    if imp.get("style"):
        lines.append(f"TA 说话风格：{imp['style']}")
    if imp.get("tags"):
        lines.append("标签：" + "、".join(str(t) for t in imp["tags"][:6]))
    if imp.get("facts"):
        lines.append("记得的事：" + "；".join(str(f) for f in imp["facts"][:8]))
    lines.append("（这些是长期印象，不要逐条复述，自然一点）")
    return "\n".join(lines)


def rotation_block(gkey: str) -> str:
    rot = store.latest_rotation(gkey)
    if not rot:
        return ""
    when = datetime.fromtimestamp(int(rot.get("created_at") or 0), TZ).strftime("%m-%d %H:%M")
    parts = [f"【你上一次对话的存档 · {when}】", str(rot.get("summary") or "")]
    if rot.get("injection"):
        parts += ["【你当时留给新自己的话】", str(rot["injection"])]
    if rot.get("important"):
        parts.append("【你决定长期记住的事】")
        parts += [f"- {x}" for x in rot["important"]]
    return "\n".join(parts) + "\n\n"


def cross_context_block(text: str, gkey: str, settings: dict[str, Any]) -> str:
    cfg = settings.get("context", {})
    if not cfg.get("cross_enabled", True) or not text:
        return ""
    people: list[dict[str, Any]] = []
    for user in store.list_users(limit=200):
        name = (user.get("display_name") or user.get("nickname") or "").strip()
        if len(name) >= 2 and name in text:
            people.append(user)
        if len(people) >= 2:
            break
    if not people:
        return ""
    lines = ["【你想起了这些人（来自别的对话）】"]
    for user in people:
        name = user.get("display_name") or user.get("nickname") or user.get("uid")
        digest = store.get_person_digest(user["uid"]) or {}
        impression = user.get("impression") or {}
        parts: list[str] = []
        summary = digest.get("summary") or impression.get("summary")
        if summary:
            parts.append(str(summary))
        if digest.get("topics"):
            parts.append("最近在聊：" + "、".join(str(t) for t in digest["topics"]))
        if impression.get("facts"):
            parts.append("记得：" + "；".join(str(f) for f in impression["facts"][:3]))
        if parts:
            lines.append(f"- {name}：" + "。".join(parts))
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n\n"


def build_transcript(rows: list[dict[str, Any]], bot_name: str, current_id: int | None = None) -> str:
    lines = []
    for row in rows:
        if current_id is not None and row.get("id") == current_id:
            continue
        speaker = bot_name if row.get("role") == "assistant" else (row.get("nickname") or "群友")
        lines.append(f"[{hhmm(int(row.get('ts') or 0))}] {speaker}：{row.get('text') or ''}")
    return "\n".join(lines)


async def collect_images(payload: dict[str, Any], rows: list[dict[str, Any]], cfg: dict[str, Any]) -> list[str]:
    if not cfg.get("include_images", True):
        return []
    urls: list[str] = []
    for url in payload.get("images") or []:
        if url:
            urls.append(url)
    for row in reversed(rows[-4:]):
        extra = row.get("extra")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = {}
        for url in (extra or {}).get("images") or []:
            if url:
                urls.append(url)
    urls = list(dict.fromkeys(urls))[: int(cfg.get("max_images") or 2)]

    out: list[str] = []
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for url in urls:
            if url.startswith("data:"):
                if len(url) <= 12_000_000:
                    out.append(url)
                continue
            try:
                resp = await client.get(url)
                if resp.status_code != 200 or len(resp.content) > MAX_IMAGE_BYTES:
                    continue
                ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                if not ctype.startswith("image/"):
                    continue
                b64 = base64.b64encode(resp.content).decode()
                out.append(f"data:{ctype};base64,{b64}")
            except Exception as exc:  # noqa: BLE001
                store.log_event("warn", "images", f"下载图片失败 {url[:120]}: {exc}")
    return out


# ---------------------------------------------------------------- memory jobs


async def extract_impression(uid: str) -> None:
    async with _task_sem:
        settings = store.get_settings()
        mem_cfg = settings.get("memory", {})
        user = store.get_user(uid)
        if not user:
            return
        rows = store.messages_for_uid(uid, 24)
        if not rows:
            return
        transcript = "\n".join(f"{r.get('nickname')}：{r.get('text')}" for r in rows[-24:])
        dims = mem_cfg.get("dimensions") or []
        dim_desc = "\n".join(f'- {d.get("key")}（{d.get("name")}）: {d.get("desc")}' for d in dims)
        prompt = f"""你在维护一个 QQ 群聊机器人对群友的长期印象。根据下面的聊天片段，更新对这个人的印象。

聊天片段：
{transcript}

请只输出 JSON，格式：
{{
  "summary": "一句话概括这个人给你的整体印象，40字内",
  "style": "TA 的说话风格，20字内",
  "tags": ["2-5个标签"],
  "facts": ["值得长期记住的具体事实，最多8条，没有就空数组"],
  "dims": {{ {", ".join(f'"{d.get("key")}": 0-100 的整数' for d in dims)} }}
}}

维度含义：
{dim_desc}

要求：只写聊天里能看出来的，不要编造；不要输出 JSON 以外的内容。"""
        try:
            resp = await call_llm(
                [{"role": "user", "content": prompt}],
                kind="memory",
                max_tokens=500,
                temperature=0.3,
                json_mode=True,
            )
            data = _parse_json(resp["text"])
            if not data:
                return
            old = user.get("impression") or {}
            old_dims = old.get("dims") or {}
            new_dims = {}
            for d in dims:
                key = d.get("key")
                try:
                    newv = int(data.get("dims", {}).get(key, old_dims.get(key, 30)))
                except (TypeError, ValueError):
                    newv = int(old_dims.get(key, 30))
                newv = max(0, min(100, newv))
                if key in old_dims:
                    newv = round(int(old_dims[key]) * 0.6 + newv * 0.4)
                new_dims[key] = newv
            facts = list(dict.fromkeys([str(x) for x in (old.get("facts") or []) + (data.get("facts") or [])]))
            facts = facts[-int(mem_cfg.get("max_facts") or 12):]
            impression = {
                "summary": str(data.get("summary") or old.get("summary") or "")[: int(mem_cfg.get("summary_max_chars") or 80)],
                "style": str(data.get("style") or old.get("style") or "")[:40],
                "tags": [str(t) for t in (data.get("tags") or old.get("tags") or [])][:6],
                "facts": facts,
                "dims": new_dims,
                "updated_at": now_ts(),
                "samples": int(old.get("samples") or 0) + len(rows),
            }
            store.save_user_impression(uid, impression)
            store.log_event("info", "memory", f"更新印象 {user.get('nickname')} -> {impression['summary'][:30]}")
        except Exception as exc:  # noqa: BLE001
            store.log_event("warn", "memory", f"提取印象失败 {uid}: {exc}")


async def summarize_group(platform: str, group_id: str) -> None:
    gkey = f"{platform}:{group_id}"
    async with _task_sem:
        rows = store.recent_messages(platform, group_id, 30)
        if len(rows) < 10:
            return
        transcript = "\n".join(f"{r.get('nickname')}：{r.get('text')}" for r in rows)
        prompt = (
            "把下面这段 QQ 群聊压缩成一段 150 字以内的摘要，保留正在聊的话题、结论、约定和梗，"
            "不要逐句复述：\n\n" + transcript
        )
        try:
            resp = await call_llm(
                [{"role": "user", "content": prompt}], kind="summary", max_tokens=300, temperature=0.3
            )
            store.save_summary(gkey, resp["text"].strip(), rows[-1]["id"])
        except Exception as exc:  # noqa: BLE001
            store.log_event("warn", "summary", f"群摘要失败 {gkey}: {exc}")


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return {}
    return {}


def schedule(coro) -> None:
    task = asyncio.create_task(_guard(coro))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


async def _guard(coro) -> None:
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        store.log_event("error", "task", f"后台任务异常: {exc}")


# ---------------------------------------------------------------- main entry


# ---------------------------------------------------------------- tools

_tool_calls: deque[float] = deque()
_image_calls: deque[float] = deque()
_last_image_ts = 0.0
_last_avatar_ts = 0.0


async def _generate_image(prompt: str, settings: dict[str, Any]) -> tuple[bytes | None, str]:
    cfg = settings.get("tools", {})
    style = str(cfg.get("image_style") or "").strip()
    full = f"{prompt}, {style}" if style else prompt
    width = int(cfg.get("image_width") or 1024)
    height = int(cfg.get("image_height") or 1024)
    url = (
        "https://image.pollinations.ai/prompt/"
        + quote(full[:600])
        + f"?width={width}&height={height}&nologo=true&seed={random.randint(1, 999999)}"
    )
    last_err = ""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
                resp = await client.get(url)
            if resp.status_code == 200 and resp.content:
                return resp.content, ""
            last_err = f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        await asyncio.sleep(2 + attempt * 3)
    return None, last_err


def _budget(settings: dict[str, Any], kind: str) -> tuple[bool, str]:
    cfg = settings.get("tools", {})
    now = time.time()
    while _tool_calls and now - _tool_calls[0] > 86400:
        _tool_calls.popleft()
    while _image_calls and now - _image_calls[0] > 86400:
        _image_calls.popleft()
    if sum(1 for t in _tool_calls if now - t < 3600) >= int(cfg.get("max_calls_per_hour") or 6):
        return False, "这一小时调用外部工具的次数用完了"
    if len(_tool_calls) >= int(cfg.get("max_calls_per_day") or 30):
        return False, "今天调用外部工具的次数用完了"
    if kind == "image" and len(_image_calls) >= int(cfg.get("max_image_calls_per_day") or 10):
        return False, "今天画图的次数用完了"
    return True, ""


def tool_specs(settings: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = settings.get("tools", {})
    specs: list[dict[str, Any]] = []
    if cfg.get("image_enabled"):
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "draw_image",
                    "description": (
                        "根据提示词生成一张图片并发到群里。只在用户明确要图、"
                        "或者话题画面感很强、画出来确实有意思时使用；不要每条消息都画。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "画面描述，必须用英文写（这个模型看不懂中文），要具体：主体、动作、场景、光线、风格",
                            },
                            "reason": {"type": "string", "description": "为什么想画这张图"},
                        },
                        "required": ["prompt"],
                    },
                },
            }
        )
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "change_avatar",
                    "description": (
                        "给自己换一张新头像：根据你的提示词生成图片并直接设为 QQ 头像。"
                        "偶尔想换个样子时用，别频繁，几小时最多一次。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "英文，描述你想要的头像：主体、风格、配色"},
                            "reason": {"type": "string", "description": "为什么想换"},
                        },
                        "required": ["prompt"],
                    },
                },
            }
        )
    if cfg.get("sticker_enabled", True):
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "send_sticker",
                    "description": (
                        "从你的 QQ 表情收藏里挑一个最贴切的表情发出去。"
                        "想表达某种情绪/反应（比如无语、笑死、点赞、疑惑）时用，比打字更自然。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "你想要的感觉或画面，比如「无语」「笑到打滚」「点赞」"},
                        },
                        "required": ["query"],
                    },
                },
            }
        )
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "save_sticker",
                    "description": (
                        "把当前这条消息里的图片收进你的 QQ 表情收藏（云端），并记住它是什么表情。"
                        "用户说「存表情/收藏这个/以后用这个」时调用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "desc": {"type": "string", "description": "给这个表情的说明：画面+情绪+适用场景，40字以内"},
                        },
                    },
                },
            }
        )
    if cfg.get("qzone_enabled", True):
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "post_qzone",
                    "description": (
                        "发一条 QQ 空间说说。想记录点什么、或者用户让你发的时候用；"
                        "如果这一轮刚画了图，会把图一起发。别频繁，一天最多几条。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "说说正文，中文，40~80 字，像真人随手写的"},
                            "visibility": {
                                "type": "integer",
                                "enum": [1, 4, 64],
                                "description": "1 公开，4 好友可见，64 仅自己",
                            },
                        },
                        "required": ["content"],
                    },
                },
            }
        )
    if cfg.get("ask_enabled") and cfg.get("ask_providers"):
        ids = ", ".join(p.get("id", "") for p in cfg["ask_providers"])
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "ask_ai",
                    "description": (
                        "去问另一个 AI 模型，把它的回答拿回来。适合需要第二意见、"
                        "想看看别家模型怎么说、或者你自己拿不准的时候。别滥用，一次对话最多问一次。"
                        f"可用的 provider：{ids}。优先用第一个（免费额度），"
                        "除非问题很难、或者对方明显答得不好。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "provider": {"type": "string", "description": f"provider id，可选：{ids}"},
                            "question": {"type": "string", "description": "要问对方的问题，写完整"},
                        },
                        "required": ["question"],
                    },
                },
            }
        )
    return specs


async def execute_tool(
    name: str,
    args: dict[str, Any],
    settings: dict[str, Any],
    images_out: list[Any],
    incoming_images: list[str] | None = None,
) -> dict[str, Any]:
    cfg = settings.get("tools", {})
    global _last_image_ts, _last_avatar_ts
    if name == "change_avatar":
        ok, why = _budget(settings, "image")
        if not ok:
            return {"error": why}
        waited = time.time() - _last_avatar_ts
        if waited < 6 * 3600:
            return {"error": f"刚换过头像，过 {int((6 * 3600 - waited) // 60)} 分钟再说"}
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return {"error": "prompt 为空"}
        content, err = await _generate_image(prompt, settings)
        if not content:
            return {"error": f"生图失败（{err}），头像没换"}
        url = await media.set_avatar(content, "image/jpeg")
        _last_avatar_ts = time.time()
        _tool_calls.append(time.time())
        _image_calls.append(time.time())
        return {"ok": True, "note": "头像已经换成新生成的那张了，群里刷新一下就能看到", "url": url}

    if name == "draw_image":
        ok, why = _budget(settings, "image")
        if not ok:
            return {"error": why}
        since = time.time() - _last_image_ts
        if since < 45:
            return {"error": f"刚画过一张，{45 - int(since)} 秒后才能再画"}
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return {"error": "prompt 为空"}
        content, err = await _generate_image(prompt, settings)
        if not content:
            return {"error": f"生图服务暂时不可用（{err}），过一会儿再试"}
        images_out.append({"b64": base64.b64encode(content).decode(), "mime": "image/jpeg"})
        _last_image_ts = time.time()
        _tool_calls.append(time.time())
        _image_calls.append(time.time())
        store.log_event("info", "tool", f"生图: {prompt[:60]}")
        return {"ok": True, "note": "图片已经生成好，会跟你的回复一起发出去，不用描述图片内容"}

    if name == "send_sticker":
        if not cfg.get("sticker_enabled", True):
            return {"error": "表情库暂时没开"}
        row = stickers.pick(str(args.get("query") or ""))
        if not row:
            return {"error": "表情收藏里还没有表情"}
        url = str(row.get("url") or "")
        if not url:
            return {"error": "这个表情没有可用地址"}
        images_out.append(url)
        store.touch_sticker(str(row["res_id"]))
        return {"ok": True, "desc": row.get("desc"), "note": "表情会跟你的回复一起发出去，不用再描述它"}

    if name == "save_sticker":
        if not incoming_images:
            return {"error": "这条消息里没有图片，没法存"}
        data_url = incoming_images[0]
        b64 = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
        desc = str(args.get("desc") or "").strip()
        result = await stickers.save_from_b64(b64, desc)
        if result.get("error"):
            return {"error": result["error"]}
        return {"ok": True, "note": "已经存进 QQ 表情收藏了，以后发的时候用 send_sticker"}

    if name == "post_qzone":
        if not cfg.get("qzone_enabled", True):
            return {"error": "空间功能没开"}
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        todays = [
            p
            for p in store.list_qzone_posts(30)
            if datetime.fromtimestamp(p["ts"], TZ).strftime("%Y-%m-%d") == today
        ]
        if len(todays) >= int(cfg.get("max_qzone_per_day") or 3):
            return {"error": "今天说说发得够多了，明天再说"}
        content = str(args.get("content") or "").strip()
        if not content:
            return {"error": "内容为空"}
        visibility = int(args.get("visibility") or 1)
        if visibility not in (1, 4, 64):
            visibility = 1
        image_b64 = [i["b64"] for i in images_out if isinstance(i, dict) and i.get("b64")][:3]
        try:
            res = await qqcmd.run(
                {"action": "qzone_publish", "content": content, "ugc_right": visibility, "images": image_b64},
                timeout=120,
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"发空间失败: {exc}"}
        if not res.get("ok"):
            return {"error": res.get("error") or "发空间失败"}
        tid = str((res.get("result") or {}).get("tid") or "")
        store.add_qzone_post(int(time.time()), content, tid, visibility, False)
        store.log_event("info", "qzone", f"AI 自己发说说: {content[:40]}")
        return {"ok": True, "tid": tid, "note": "已经发到 QQ 空间了，不用再重复"}

    if name == "ask_ai":
        ok, why = _budget(settings, "ask")
        if not ok:
            return {"error": why}
        providers = cfg.get("ask_providers") or []
        if not providers:
            return {"error": "还没有配置外部模型"}
        pid = str(args.get("provider") or providers[0].get("id"))
        prov = next((p for p in providers if str(p.get("id")) == pid), None)
        if not prov:
            return {"error": f"没有 {pid} 这个 provider", "available": [p.get("id") for p in providers]}
        question = str(args.get("question") or "").strip()
        if not question:
            return {"error": "问题为空"}
        key = prov.get("api_key") or ""
        if not key and prov.get("id") == "deepseek":
            key = settings.get("model", {}).get("api_key") or ""
        headers = {"Content-Type": "application/json"}
        extra_headers = prov.get("headers") or {}
        if isinstance(extra_headers, str):
            try:
                extra_headers = json.loads(extra_headers)
            except json.JSONDecodeError:
                extra_headers = {}
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items() if str(k).strip()})
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": prov.get("model") or "openai",
            "messages": [{"role": "user", "content": question}],
            "max_tokens": 600,
        }
        if prov.get("stream"):
            payload["stream"] = True
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(prov["endpoint"], headers=headers, json=payload)
            if resp.status_code != 200:
                return {"error": f"{prov.get('name')} HTTP {resp.status_code}: {resp.text[:160]}"}
            body = resp.text
            if body.lstrip().startswith("data:"):
                chunks = []
                for line in body.splitlines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for choice in obj.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            chunks.append(delta["content"])
                answer = "".join(chunks)
            else:
                data = json.loads(body)
                answer = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        except Exception as exc:  # noqa: BLE001
            return {"error": f"调用 {prov.get('name')} 失败: {exc}"}
        _tool_calls.append(time.time())
        store.log_event("info", "tool", f"问 {prov.get('name')}: {question[:50]}")
        return {"provider": prov.get("name"), "answer": answer[:900]}

    return {"error": f"未知工具 {name}"}


async def compose_proactive(platform: str, group_id: str) -> dict[str, Any] | None:
    """Generate one unprompted message from the recent group context."""
    settings = store.get_settings()
    persona = settings.get("persona", {})
    bot_name = persona.get("name") or "小语"
    ctx_cfg = settings.get("context", {})
    gkey = f"{platform}:{group_id}"
    rows = store.recent_messages(platform, group_id, int(ctx_cfg.get("max_turns") or 24))
    transcript = build_transcript(rows, bot_name) if rows else "（群里还没人说话）"
    recent_self = [r for r in rows if r.get("role") == "assistant"][-3:]

    system_prompt = (persona.get("system_prompt") or "").format(
        name=bot_name, identity=persona.get("identity") or ""
    )
    taboos = "、".join(persona.get("taboos") or [])
    if taboos:
        system_prompt += f"\n绝对不聊：{taboos}。"
    backstory = (persona.get("backstory") or "").strip()
    if backstory:
        system_prompt += "\n【你的背景】\n" + backstory
    emoji_cfg = settings.get("emoji", {})
    if emoji_cfg.get("enabled", True):
        system_prompt += "\n想带表情时，在句尾写一个 [表情:名称]，一条回复最多一个。"
    now_cn = datetime.now(TZ)
    system_prompt += f"\n当前时间：{now_cn.strftime('%Y-%m-%d %H:%M')}（Asia/Shanghai）。"

    wake = (settings.get("proactive", {}).get("prompt") or "").replace(
        "{time}", now_cn.strftime("%H:%M")
    ).replace("{name}", bot_name)
    user_prompt = rotation_block(gkey)
    user_prompt += f"最近的群聊记录：\n{transcript}\n\n"
    if rows:
        last = rows[-1]
        last_speaker = bot_name if last.get("role") == "assistant" else (last.get("nickname") or "群友")
        user_prompt += f"群里最后一条是 {last_speaker} 说的：{last.get('text')}\n\n"
    if recent_self:
        user_prompt += "你最近在群里说过：\n" + "\n".join(f"- {r['text']}" for r in recent_self) + "\n\n"
    user_prompt += wake + "\n\n"
    lore = persona.get("lore") or []
    if lore and rows:
        picks = pick_lore(lore)
        user_prompt += (
            "只有当你完全接不上上面的话题时，才从这些生活碎片里挑一条具体的小事来说：\n"
            + "\n".join(f"- {x}" for x in picks)
            + "\n\n"
        )
    elif lore:
        picks = pick_lore(lore)
        user_prompt += "群里还没人说话，从这些生活碎片里挑一条具体的小事开口：\n" + "\n".join(f"- {x}" for x in picks) + "\n\n"
    user_prompt += "直接输出你要发到群里的那句话，不要加名字、不要解释。"

    tools = tool_specs(settings)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    images_out: list[Any] = []
    text = ""
    try:
        for _ in range(3):
            resp = await call_llm(messages, kind="proactive", max_tokens=260, tools=tools or None)
            message = resp["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                text = message.get("content") or ""
                break
            messages.append(message)
            for call in calls:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await execute_tool(str(fn.get("name")), args, settings, images_out, [])
                messages.append(
                    {"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)}
                )
    except Exception as exc:  # noqa: BLE001
        store.log_event("error", "proactive", f"生成主动消息失败: {exc}")
        return None

    raw = strip_markdown(text)
    blocked = [w for w in (settings.get("safety", {}).get("blocked_words") or []) if w]
    if any(w in raw for w in blocked):
        store.log_event("warn", "proactive", "主动消息命中屏蔽词，已丢弃")
        return None
    raw, face_ids = extract_face_actions(raw, emoji_cfg.get("map") or {})
    if emoji_cfg.get("enabled", True) and not face_ids and not EMOJI_RE.search(raw):
        if random.random() < float(emoji_cfg.get("send_probability") or 0):
            picked = pick_face(raw, emoji_cfg.get("map") or {})
            if picked is not None:
                face_ids = [picked]
    bubbles = split_bubbles(
        raw,
        max_bubbles=int(settings.get("safety", {}).get("max_bubbles") or 3),
        max_chars=int(settings.get("safety", {}).get("max_reply_chars") or 120),
    )
    if not bubbles and not images_out:
        return None
    return {
        "bubbles": bubbles,
        "faces": face_ids,
        "images": images_out,
        "text": "\n".join(bubbles),
    }


async def handle(payload: dict[str, Any]) -> dict[str, Any]:
    settings = store.get_settings()
    platform = payload.get("platform") or "qq"
    group_id = str(payload.get("group_id") or "")
    user_id = str(payload.get("user_id") or "")
    nickname = (payload.get("nickname") or "群友")[:40]
    text = (payload.get("text") or "").strip()
    ts = int(payload.get("ts") or now_ts())

    emoji_cfg = settings.get("emoji", {})
    faces_in = payload.get("faces") or [] if settings.get("context", {}).get("include_faces", True) else []
    text = merge_faces(text, faces_in, emoji_cfg.get("map") or {})
    images = [u for u in (payload.get("images") or []) if u]
    if images and not text:
        text = "[发了一张图片]"

    uid = store.touch_user(platform, user_id, nickname, ts)
    msg_id = store.add_message(
        platform, group_id, uid, nickname, "user", text, {"images": images, "faces": payload.get("faces") or [], "msg_id": payload.get("msg_id")}, ts
    )
    gkey = f"{platform}:{group_id}"
    _group_counter[gkey] = _group_counter.get(gkey, 0) + 1
    _uid_counter[uid] = _uid_counter.get(uid, 0) + 1

    mem_cfg = settings.get("memory", {})
    if mem_cfg.get("enabled", True):
        every = max(2, int(mem_cfg.get("extract_every") or 8))
        if _uid_counter[uid] % every == 0 and uid not in _inflight:
            _inflight.add(uid)
            schedule(_extract_and_release(uid))
        if settings.get("context", {}).get("summary_enabled", True) and _group_counter[gkey] % int(
            settings.get("context", {}).get("summary_every", 80)
        ) == 0:
            schedule(summarize_group(platform, group_id))

        user_row = store.get_user(uid)
        total = int(user_row.get("msg_count") or 0) if user_row else 0
        digest_row = store.get_person_digest(uid)
        base = int(digest_row.get("msg_count") or 0) if digest_row else 0
        recent_digest = digest_row and time.time() - int(digest_row.get("updated_at") or 0) < 1200
        if total - base >= 5 and not recent_digest and uid not in _digest_inflight:
            _digest_inflight.add(uid)
            schedule(_digest_job(uid))

    should, reason = decide(payload, settings)
    if not should:
        return {"reply": None, "reason": reason, "msg_id": msg_id}

    prefix = (settings.get("trigger", {}).get("prefix") or "").strip()
    if reason == "prefix" and prefix and text.startswith(prefix):
        text = text[len(prefix):].strip()

    trigger_cfg = settings.get("trigger", {})
    batch_mode = False
    if trigger_cfg.get("batch_enabled", True):
        window = float(trigger_cfg.get("batch_window") or 7)
        active = _batches.get(gkey)
        if active and time.time() - active < window + 5:
            return {"reply": None, "reason": "batched", "msg_id": msg_id}
        if random.random() >= float(trigger_cfg.get("single_reply_ratio") or 0.25):
            _batches[gkey] = time.time()
            await asyncio.sleep(window)
            _batches.pop(gkey, None)
            batch_mode = True

    ctx_cfg = settings.get("context", {})
    limit = max(4, int(ctx_cfg.get("max_turns") or 24))
    rows = store.recent_messages(platform, group_id, limit)

    memory_block = ""
    user = store.get_user(uid)
    if user:
        memory_block = build_memory_block(user, settings)

    bot_name = settings.get("persona", {}).get("name") or "小语"
    summary = store.get_summary(gkey)
    transcript = build_transcript(rows, bot_name, current_id=msg_id)
    max_chars = int(ctx_cfg.get("max_chars") or 3500)
    if len(transcript) > max_chars:
        kept: list[str] = []
        total = 0
        for line in reversed(transcript.splitlines()):
            total += len(line) + 1
            if total > max_chars:
                break
            kept.append(line)
        transcript = "\n".join(reversed(kept))
        transcript = "（更早的消息已省略）\n" + transcript
    if summary and len(rows) >= limit:
        transcript = f"（更早的聊天摘要：{summary['summary']}）\n" + transcript

    persona = settings.get("persona", {})
    system_prompt = (persona.get("system_prompt") or "").format(
        name=bot_name, identity=persona.get("identity") or ""
    )
    taboos = "、".join(persona.get("taboos") or [])
    if taboos:
        system_prompt += f"\n绝对不聊：{taboos}。"
    backstory = (persona.get("backstory") or "").strip()
    if backstory:
        system_prompt += "\n【你的背景】\n" + backstory
    system_prompt += (
        "\n注意区分说话人：聊天记录里哪些话是谁说的要分清，"
        "不要把别人的经历、喜好安到当前说话人头上。"
    )
    if emoji_cfg.get("enabled", True):
        system_prompt += (
            "\n想带表情时，在句尾写一个 [表情:名称]（例如 [表情:笑哭]），系统会转成真正的 QQ 表情；"
            "一条回复最多一个，不要每句都带。"
        )
    weekdays = "一二三四五六日"
    now_cn = datetime.now(TZ)
    user_prompt = (
        f"当前时间：{now_cn.strftime('%Y-%m-%d %H:%M')} 星期{weekdays[now_cn.weekday()]}"
        "（Asia/Shanghai）。\n\n"
    )
    lore = persona.get("lore") or []
    if lore:
        picks = pick_lore(lore)
        user_prompt += "你最近的生活碎片（合适就自然提起，不用硬塞）：\n" + "\n".join(f"- {x}" for x in picks) + "\n\n"
    if len(rows) <= 4:
        user_prompt += (
            "群里还没什么上下文，别硬接话；可以说一件你自己刚遇到的、具体的、有点意思的小事，"
            "或者对眼前的东西吐槽一句。\n\n"
        )
    rotate_text = rotation_block(gkey)
    if rotate_text:
        user_prompt += rotate_text
    cross_text = cross_context_block(text, gkey, settings)
    if cross_text:
        user_prompt += cross_text
    if memory_block:
        user_prompt += memory_block + "\n\n"
    user_prompt += f"最近的群聊记录：\n{transcript or '（暂无）'}\n\n"
    quote = (payload.get("quote") or "").strip()
    if quote:
        user_prompt += f"（{nickname} 刚刚引用了这条消息：{quote[:120]}）\n"
    if batch_mode:
        user_prompt += (
            "刚才这一小会儿，群里连着来了好几条消息（见上面的记录）。"
            "你可以把它们当成一件事一起回应，也可以挑其中一条接。\n\n"
        )
    else:
        user_prompt += f"现在 {nickname} 说：{text}\n\n"
    user_prompt += (
        f"请以「{bot_name}」的身份，直接说你要发到群里的话。"
        "只要回复内容本身，不要加名字前缀、不要解释、不要旁白。"
    )

    content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    image_urls = await collect_images(payload, rows, ctx_cfg)
    if settings.get("model", {}).get("vision", True):
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})

    tools = tool_specs(settings)
    if tools:
        names = []
        if any(t["function"]["name"] == "draw_image" for t in tools):
            names.append("draw_image 画图")
        if any(t["function"]["name"] == "ask_ai" for t in tools):
            names.append("ask_ai 问别的模型")
        system_prompt += (
            "\n你可以用工具：" + "、".join(names) + "。只在真的需要时用，别每条消息都画图或问人。"
        )
        if any(t["function"]["name"] == "send_sticker" for t in tools):
            hint = stickers.prompt_hint()
            if hint:
                system_prompt += (
                    "\n"
                    + hint
                    + "\n规则：对方情绪很明显时（爆笑、无语、震惊、委屈、求安慰、点赞），"
                    "先用 send_sticker 发一个表情，再补一句短话；表情和文字可以一起。"
                    "不要连续两条都发表情。"
                )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content if len(content) > 1 else user_prompt},
    ]
    images_out: list[Any] = []
    final_text = ""
    try:
        for _ in range(3):
            resp = await call_llm(messages, kind="chat", tools=tools or None)
            message = resp["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                final_text = message.get("content") or ""
                break
            messages.append(message)
            for call in calls:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await execute_tool(str(fn.get("name")), args, settings, images_out, image_urls)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        store.log_event("error", "chat", f"生成回复失败: {exc}")
        return {"reply": None, "reason": "llm-error", "error": str(exc), "msg_id": msg_id}

    raw = strip_markdown(final_text)
    blocked = [w for w in (settings.get("safety", {}).get("blocked_words") or []) if w]
    if any(w in raw for w in blocked):
        store.log_event("warn", "safety", f"回复命中屏蔽词，已丢弃: {raw[:60]}")
        return {"reply": None, "reason": "blocked-word", "msg_id": msg_id}
    raw, face_ids = extract_face_actions(raw, emoji_cfg.get("map") or {})
    if (
        emoji_cfg.get("enabled", True)
        and not face_ids
        and not EMOJI_RE.search(raw)
        and random.random() < float(emoji_cfg.get("send_probability") or 0)
    ):
        picked = pick_face(raw, emoji_cfg.get("map") or {})
        if picked is not None:
            face_ids = [picked]
    bubbles = split_bubbles(
        raw,
        max_bubbles=4 if batch_mode else int(settings.get("safety", {}).get("max_bubbles") or 3),
        max_chars=int(settings.get("safety", {}).get("max_reply_chars") or 120),
    )
    bubbles = fan_out_bubbles(bubbles, 2)
    maybe_auto_sticker(text, settings, images_out)

    if not bubbles and not images_out:
        return {"reply": None, "reason": "empty-reply", "msg_id": msg_id}

    reply_text = "\n".join(bubbles)
    store.add_message(
        platform, group_id, f"{platform}:self", bot_name, "assistant", reply_text,
        {"faces": face_ids, "images_count": len(images_out)}, now_ts(),
    )
    store.bump_reply_count(uid)
    note_reply(gkey)

    delay = 900 + min(2500, len(reply_text) * 25) if settings.get("trigger", {}).get("typing_simulation", True) else 0
    return {
        "reply": reply_text,
        "bubbles": bubbles,
        "faces": face_ids,
        "reason": reason,
        "delay_ms": delay,
        "memory_used": bool(memory_block),
        "context_turns": len(rows),
        "images": images_out,
        "input_images": len(image_urls),
        "msg_id": msg_id,
        "quote": bool(settings.get("trigger", {}).get("quote_reply", True)),
        "cost": resp.get("cost", 0),
    }


async def _extract_and_release(uid: str) -> None:
    try:
        await extract_impression(uid)
    finally:
        _inflight.discard(uid)
