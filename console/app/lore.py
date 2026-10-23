"""Daily life fragments: local weather + social news -> short first-person notes."""

from __future__ import annotations

import asyncio
import email.utils
import html
import re
import time
from datetime import datetime

import httpx

from . import brain, store

CITY_COORDS: dict[str, tuple[float, float]] = {
    "杭州": (30.27, 120.15),
    "北京": (39.90, 116.40),
    "上海": (31.23, 121.47),
    "深圳": (22.54, 114.06),
    "广州": (23.13, 113.26),
    "成都": (30.57, 104.07),
    "武汉": (30.59, 114.31),
    "南京": (32.06, 118.80),
    "西安": (34.34, 108.94),
    "重庆": (29.56, 106.55),
    "长沙": (28.23, 112.94),
    "苏州": (31.30, 120.58),
}

WEATHER_CODES: dict[int, str] = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "起雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨",
    56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}

NEWS_FEEDS = [
    "https://www.chinanews.com.cn/rss/society.xml",
    "https://www.chinanews.com.cn/rss/scroll-news.xml",
]

_last_error = ""


async def _weather(city: str) -> str:
    lat, lon = CITY_COORDS.get(city, CITY_COORDS["杭州"])
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min"
        "&timezone=Asia%2FShanghai&forecast_days=1"
    )
    async with httpx.AsyncClient(timeout=12) as client:
        data = (await client.get(url)).json()
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    code = int((daily.get("weather_code") or [0])[0])
    hi = (daily.get("temperature_2m_max") or [0])[0]
    lo = (daily.get("temperature_2m_min") or [0])[0]
    now = cur.get("temperature_2m")
    desc = WEATHER_CODES.get(code, "多云")
    return f"{desc}，{round(lo)}~{round(hi)}℃，现在 {round(now)}℃"


async def _news(limit: int = 6) -> list[str]:
    titles: list[str] = []
    cutoff = time.time() - 36 * 3600
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        for feed in NEWS_FEEDS:
            try:
                text = (await client.get(feed)).text
            except Exception:  # noqa: BLE001
                continue
            for item in re.findall(r"<item>(.*?)</item>", text, flags=re.S):
                m = re.search(r"<title>(.*?)</title>", item, flags=re.S)
                if not m:
                    continue
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))
                title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
                title = re.sub(r"\s+", " ", title)
                dm = re.search(r"<pubDate>(.*?)</pubDate>", item, flags=re.S)
                if dm:
                    try:
                        if email.utils.parsedate_to_datetime(dm.group(1).strip()).timestamp() < cutoff:
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                if 8 <= len(title) <= 60 and "中新网" not in title[:5] and title not in titles:
                    titles.append(title)
            if len(titles) >= limit:
                break
    return titles[:limit]


def _clean_fragment(line: str) -> str:
    line = re.sub(r"^\s*(?:[-*•·]|\d+[.、)])\s*", "", line).strip()
    line = line.strip("「」\"'“”")
    return line[:30]


async def refresh() -> dict:
    global _last_error
    persona = store.get_section("persona")
    name = persona.get("name") or "小肥鱼"
    city = (persona.get("city") or "杭州").strip()
    today = datetime.now(brain.TZ).strftime("%Y-%m-%d")
    try:
        weather = await _weather(city)
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        return {"ok": False, "error": f"天气获取失败: {exc}"}
    try:
        news = await _news()
    except Exception as exc:  # noqa: BLE001
        _last_error = str(exc)
        news = []
    news_block = "\n".join(f"- {t}" for t in news) if news else "（今天没有抓到新闻，只写日常）"
    template = persona.get("lore_prompt") or store._defaults()["persona"]["lore_prompt"]
    try:
        prompt = template.format(name=name, city=city, date=today, weather=weather, news=news_block)
    except KeyError as exc:
        return {"ok": False, "error": f"提示词占位符错误: {exc}"}
    resp = await brain.call_llm(
        [{"role": "user", "content": prompt}],
        kind="lore",
        max_tokens=400,
        temperature=0.85,
    )
    lines = [_clean_fragment(x) for x in (resp["text"] or "").splitlines()]
    lines = [x for x in lines if 4 <= len(x) <= 30][:8]
    if not lines:
        return {"ok": False, "error": "模型没写出可用的碎片"}
    keep = [
        x
        for x in (persona.get("lore") or [])
        if x.startswith(("有个人说", "他问我", "我跟他说"))
    ]
    persona["lore"] = lines + keep[:2]
    persona["lore_date"] = today
    persona["lore_updated"] = int(datetime.now(brain.TZ).timestamp())
    store.save_section("persona", persona)
    store.log_event("info", "lore", f"生活碎片已更新（{city}，{weather}）：{lines[0]}")
    return {"ok": True, "weather": weather, "news": news, "fragments": lines}


async def tick() -> None:
    persona = store.get_section("persona")
    if not persona.get("lore_enabled", True):
        return
    now = datetime.now(brain.TZ)
    try:
        hh, mm = [int(x) for x in str(persona.get("lore_hour") or "12:00").split(":")]
    except ValueError:
        hh, mm = 12, 0
    if now.hour * 60 + now.minute < hh * 60 + mm:
        return
    today = now.strftime("%Y-%m-%d")
    if persona.get("lore_date") == today:
        return
    await refresh()


async def run() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            store.log_event("warn", "lore", f"更新失败: {exc}")
        await asyncio.sleep(60)


def status() -> dict:
    persona = store.get_section("persona")
    return {
        "city": persona.get("city"),
        "lore_date": persona.get("lore_date"),
        "lore_updated": persona.get("lore_updated"),
        "last_error": _last_error,
    }
