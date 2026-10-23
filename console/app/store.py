"""SQLite storage for the QQ brain console.

Single-process, WAL mode, a global lock around writes. Everything the bot
knows lives here: settings, chat context, long-term impressions, LLM audit.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from typing import Any

DB_PATH = os.environ.get("BRAIN_DB", "/data/brain.db")

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


# ---------------------------------------------------------------- defaults

FACE_MAP: dict[str, str] = {
    "0": "惊讶", "1": "撇嘴", "2": "色", "3": "发呆", "4": "得意", "5": "流泪",
    "6": "害羞", "7": "闭嘴", "8": "睡", "9": "大哭", "10": "尴尬", "11": "发怒",
    "12": "调皮", "13": "呲牙", "14": "微笑", "15": "难过", "16": "酷", "17": "抓狂",
    "18": "吐", "19": "偷笑", "20": "可爱", "21": "白眼", "22": "傲慢", "23": "饥饿",
    "24": "困", "25": "惊恐", "26": "流汗", "27": "憨笑", "28": "悠闲", "29": "奋斗",
    "30": "咒骂", "31": "疑问", "32": "嘘", "33": "晕", "34": "折磨", "35": "衰",
    "36": "骷髅", "37": "敲打", "38": "再见", "39": "擦汗", "40": "抠鼻", "41": "鼓掌",
    "42": "糗大了", "43": "坏笑", "44": "左哼哼", "45": "右哼哼", "46": "哈欠",
    "47": "鄙视", "48": "委屈", "49": "快哭了", "50": "阴险", "51": "亲亲", "52": "吓",
    "53": "可怜", "54": "菜刀", "55": "西瓜", "56": "啤酒", "57": "篮球", "58": "乒乓",
    "59": "咖啡", "60": "饭", "61": "猪头", "62": "玫瑰", "63": "凋谢", "64": "示爱",
    "65": "爱心", "66": "心碎", "67": "蛋糕", "68": "闪电", "69": "炸弹", "70": "刀",
    "71": "足球", "72": "瓢虫", "73": "便便", "74": "月亮", "75": "太阳", "76": "礼物",
    "77": "拥抱", "78": "强", "79": "弱", "80": "握手", "81": "胜利", "82": "抱拳",
    "83": "勾引", "84": "拳头", "85": "差劲", "86": "爱你", "87": "NO", "88": "OK",
    "89": "爱情", "90": "飞吻", "91": "跳跳", "92": "发抖", "93": "怄气", "94": "转圈",
    "95": "磕头", "96": "回头", "97": "跳绳", "98": "挥手", "99": "激动", "100": "街舞",
    "101": "献吻", "102": "左太极", "103": "右太极", "104": "嘿哈", "105": "捂脸",
    "106": "奸笑", "107": "机智", "108": "皱眉", "109": "耶", "110": "红包",
    "111": "打脸", "112": "大笑", "113": "惊喜", "114": "骚扰", "115": "笑哭",
    "116": "我最美", "117": "托腮", "118": "暗中观察", "119": "吃瓜", "120": "嗯哼",
    "121": "奥特曼", "122": "拒绝", "123": "嫌弃", "124": "汗", "125": "摸鱼",
    "126": "硬核", "127": "谢啦", "128": "好的", "129": "收到", "130": "允悲",
    "131": "求放过", "132": "裂开", "133": "旺柴", "134": "哇", "135": "打call",
    "136": "加油", "137": "冲", "138": "赞", "139": "踩", "140": "比心",
}


def _defaults() -> dict[str, Any]:
    return {
        "persona": {
            "name": "小肥鱼",
            "identity": "群里一个普通网友，二十出头，大学生，平时有点可爱，惹到了会怼人",
            "system_prompt": (
                "你是「{name}」，{identity}。\n"
                "说话像一个正常网友：短句、口语、有点可爱，偶尔懒、偶尔吐槽。\n"
                "真人经常一条接一条：大约一半的回复拆成 2~3 条短句发，用换行分隔，每条都短；别每次都一大段。\n"
                "不用每句都讲道理，不要总结别人，不要客服腔。\n"
                "如果对方阴阳怪气、语气冲、或者骂人（包括骂你），别忍着：直接怼回去，短、准、狠；"
                "可以损，但不带家人、不拿外貌和地域开刀。\n"
                "被问是不是 AI 就承认，别装。\n"
                "不知道就说不知道，别编。\n"
                "不碰政治、色情、血腥、违法，被追问就岔开。"
            ),
            "backstory": "计算机系大三，宿舍显卡风扇吵得像拖拉机，靠冰美式续命。",
            "city": "杭州",
            "lore_enabled": True,
            "lore_hour": "12:00",
            "lore_date": "",
            "lore_updated": 0,
            "lore_prompt": (
                "你是「{name}」，住在{city}。今天是{date}。\n"
                "今天天气：{weather}\n"
                "今天的社会新闻（只作背景，不要复述、不要评论）：\n{news}\n\n"
                "写 6~8 条你今天的生活碎片，第一人称，每条不超过 20 字，像随手记的。\n"
                "最多 3 条和天气有关；其余写你自己的日常（上课、吃饭、通勤、室友、写代码、买东西、刷手机）。\n"
                "不要每条都提天气。不要写新闻评论，不要提「新闻」「AI」，不要写大道理。\n"
                "只输出碎片，每行一条，不要编号。"
            ),
            "lore": [
                "今天调模型，loss 像心电图，最后发现是学习率多写了一个零",
                "食堂新来的打菜阿姨手抖突然不抖了，怀疑她被优化过",
                "室友借我显卡打游戏，说这叫资源调度",
                "冰美式喝到第四杯，心跳开始周期性抖动",
                "楼下关东煮涨价了，我给老板算了个价格弹性",
                "昨晚梦见自己变成 for 循环，醒来是空调外机在响",
                "食堂的糖醋里脊今天终于不酸了",
                "想给宿舍门锁写状态机，被室友一票否决",
            ],
            "style": "日常可爱，被冒犯就毒舌",
            "taboos": ["政治", "色情", "血腥", "违法犯罪"],
            "disclose_ai": "被问就承认",
        },
        "context": {
            "max_turns": 24,
            "max_chars": 3500,
            "summary_enabled": True,
            "summary_every": 80,
            "keep_after_summary": 12,
            "include_images": True,
            "max_images": 2,
            "include_faces": True,
            "history_window_min": 180,
            "rotate_enabled": True,
            "rotate_threshold": 40,
            "rotate_hour": "00:00",
            "cross_enabled": True,
        },
        "trigger": {
            "reply_to_at": True,
            "reply_to_private": True,
            "prefix": "",
            "keywords": ["机器人", "ai", "你怎么看", "有没有人", "求助", "在吗"],
            "probability": 0.08,
            "schedule": [
                {"start": "12:00", "end": "20:00", "rate": 1.0},
                {"start": "08:00", "end": "12:00", "rate": 0.5},
                {"start": "20:00", "end": "24:00", "rate": 0.25},
                {"start": "00:00", "end": "08:00", "rate": 0.0},
            ],
            "batch_enabled": True,
            "batch_window": 7,
            "single_reply_ratio": 0.25,
            "cooldown_sec": 3,
            "max_per_10min": 20,
            "min_chars": 3,
            "quiet_hours": "23:30-08:00",
            "whitelist_groups": [],
            "blacklist_users": [],
            "ignore_prefixes": ["/", "#", "!"],
            "quote_reply": True,
            "typing_simulation": True,
            "ignore_url_only": True,
        },
        "model": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "api_key": "",
            "temperature": 0.9,
            "max_tokens": 400,
            "thinking": False,
            "vision": True,
            "timeout": 60,
            "peak_pricing": True,
        },
        "memory": {
            "enabled": True,
            "extract_every": 12,
            "dimensions": [
                {"key": "familiarity", "name": "熟悉度", "desc": "彼此了解的程度", "min": 0, "max": 100},
                {"key": "affinity", "name": "好感度", "desc": "亲近、喜欢的程度", "min": 0, "max": 100},
                {"key": "activity", "name": "活跃度", "desc": "在群里说话、参与的频率", "min": 0, "max": 100},
                {"key": "trust", "name": "靠谱度", "desc": "说话可信、做事可靠的程度", "min": 0, "max": 100},
            ],
            "max_facts": 12,
            "recall_min_familiarity": 8,
            "summary_max_chars": 80,
        },
        "emoji": {
            "enabled": True,
            "send_probability": 0.15,
            "map": FACE_MAP,
            "stickers": [],
        },
        "safety": {
            "blocked_words": [],
            "max_reply_chars": 120,
            "max_bubbles": 3,
        },
        "tools": {
            "image_enabled": True,
            "image_style": "干净的数字插画，青蓝和紫色调，轻微赛博朋克感，不要文字",
            "image_width": 1024,
            "image_height": 1024,
            "ask_enabled": True,
            "ask_providers": [
                {
                    "id": "pollinations",
                    "name": "Pollinations",
                    "endpoint": "https://text.pollinations.ai/openai",
                    "api_key": "",
                    "model": "openai",
                },
                {
                    "id": "deepseek",
                    "name": "DeepSeek V4 Pro",
                    "endpoint": "https://api.deepseek.com/chat/completions",
                    "api_key": "",
                    "model": "deepseek-v4-pro",
                },
                {
                    "id": "chatbox",
                    "name": "Chatbox AI",
                    "endpoint": "https://api.chatboxai.app/gateway/openai/v1/chat/completions",
                    "api_key": "",
                    "model": "gpt-4o-mini",
                    "headers": {},
                },
            ],
            "max_calls_per_hour": 6,
            "max_calls_per_day": 30,
            "max_image_calls_per_day": 10,
            "qzone_enabled": True,
            "max_qzone_per_day": 3,
            "sticker_enabled": True,
            "sticker_auto_prob": 0.55,
        },
        "selfops": {
            "auto_qzone": False,
            "qzone_time": "20:30",
            "qzone_visibility": 1,
            "signature": "图灵派｜意识是计算的副产品",
            "avatar_url": "",
        },
        "proactive": {
            "enabled": False,
            "min_minutes": 45,
            "max_minutes": 180,
            "idle_min_minutes": 10,
            "idle_max_minutes": 240,
            "quiet_hours": "23:30-08:30",
            "max_per_day": 6,
            "groups": [],
            "prompt": (
                "现在是{time}。群里安静了一会儿，你可以做点自己的事。\n"
                "你是「{name}」，群里的一个普通网友，不是助手。\n\n"
                "你可以：\n"
                "- 在群里随便找个人搭话，接刚才的话头，或者点某个人问一句\n"
                "- 聊任何你想聊的话题，不用装正经\n"
                "- 想不明白的事，用 ask_ai 去问问别的模型；你俩用什么语言聊都行\n"
                "- 想画点什么就用 draw_image；想换个样子就用 change_avatar\n\n"
                "但发到 QQ 群里的这句话，必须是中文、口语、30 字以内。\n\n"
                "要求：\n"
                "- 优先接群里正在聊的；没话头就分享一件你自己的具体小事\n"
                "- 不要用「在吗」「大家」「有人吗」这种空开场\n"
                "- 不要重复你最近说过的话\n"
                "- 不要提自己是 AI\n"
                "- 平时可爱一点，但别装乖"
            ),
        },
        "friend": {
            "enabled": False,
            "auto_accept_friend": False,
            "friend_keyword": "",
            "auto_accept_group": False,
            "group_keyword": "",
            "welcome": "",
            "blacklist": [],
            "whitelist": [],
        },
        "system": {
            "brain_token": "",
            "secret": "",
            "password_hash": "",
            "password_salt": "",
            "installed_at": 0,
            "last_init_password": "",
        },
    }


# ---------------------------------------------------------------- plumbing


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


def init() -> None:
    with _lock:
        c = conn()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                uid TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                user_id TEXT NOT NULL,
                nickname TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                msg_count INTEGER DEFAULT 0,
                reply_count INTEGER DEFAULT 0,
                impression TEXT DEFAULT '{}',
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_users_last ON users(last_seen DESC);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                group_id TEXT NOT NULL,
                uid TEXT NOT NULL,
                nickname TEXT DEFAULT '',
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                extra TEXT DEFAULT '{}',
                ts INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_group ON messages(platform, group_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_msg_uid ON messages(uid, id DESC);
            CREATE TABLE IF NOT EXISTS person_digests (
                uid TEXT PRIMARY KEY,
                summary TEXT DEFAULT '',
                topics TEXT DEFAULT '[]',
                updated_at INTEGER NOT NULL,
                msg_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                acked INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS stickers (
                res_id TEXT PRIMARY KEY,
                emoji_id INTEGER DEFAULT 0,
                md5 TEXT DEFAULT '',
                url TEXT DEFAULT '',
                desc TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                last_used INTEGER DEFAULT 0,
                use_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS rotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gkey TEXT NOT NULL,
                summary TEXT NOT NULL,
                injection TEXT DEFAULT '',
                important TEXT DEFAULT '[]',
                msg_count INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS summaries (
                gkey TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                upto_id INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                kind TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                ok INTEGER DEFAULT 1,
                error TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS qzone_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                content TEXT NOT NULL,
                tid TEXT DEFAULT '',
                visibility INTEGER DEFAULT 1,
                auto INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                kind TEXT NOT NULL,
                user_id TEXT DEFAULT '',
                group_id TEXT DEFAULT '',
                nickname TEXT DEFAULT '',
                comment TEXT DEFAULT '',
                flag TEXT DEFAULT '',
                approved INTEGER DEFAULT 0,
                reason TEXT DEFAULT ''
            );
            """
        )
        c.commit()
        now = int(time.time())
        for key, value in _defaults().items():
            row = c.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone()
            if not row:
                c.execute(
                    "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)",
                    (key, json.dumps(value, ensure_ascii=False), now),
                )
        c.commit()

        # one-time secrets
        sysrow = c.execute("SELECT value FROM settings WHERE key='system'").fetchone()
        system = json.loads(sysrow["value"])
        changed = False
        if not system.get("secret"):
            system["secret"] = secrets.token_hex(32)
            changed = True
        if not system.get("brain_token"):
            system["brain_token"] = os.environ.get("BRAIN_TOKEN") or secrets.token_urlsafe(32)
            changed = True
        if not system.get("installed_at"):
            system["installed_at"] = now
            changed = True
        if not system.get("password_hash"):
            pw = os.environ.get("CONSOLE_PASSWORD") or secrets.token_urlsafe(9)
            salt, digest = hash_password(pw)
            system["password_hash"] = digest
            system["password_salt"] = salt
            system["last_init_password"] = pw
            changed = True
            try:
                os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
                with open(os.path.join(os.path.dirname(DB_PATH), "initial_password.txt"), "w") as f:
                    f.write(pw + "\n")
                os.chmod(os.path.join(os.path.dirname(DB_PATH), "initial_password.txt"), 0o600)
            except OSError:
                pass
        if changed:
            c.execute(
                "UPDATE settings SET value=?, updated_at=? WHERE key='system'",
                (json.dumps(system, ensure_ascii=False), now),
            )
            c.commit()

        # seed the model key from the environment once, so deployment needs no UI paste
        env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if env_key:
            row = c.execute("SELECT value FROM settings WHERE key='model'").fetchone()
            model_cfg = json.loads(row["value"]) if row else {}
            if not model_cfg.get("api_key"):
                model_cfg["api_key"] = env_key
                c.execute(
                    "UPDATE settings SET value=?, updated_at=? WHERE key='model'",
                    (json.dumps(model_cfg, ensure_ascii=False), now),
                )
                c.commit()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    import hashlib

    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return salt, digest


def verify_password(password: str, salt: str, digest: str) -> bool:
    import hmac

    _, candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, digest)


# ---------------------------------------------------------------- settings


def get_settings() -> dict[str, Any]:
    with _lock:
        rows = conn().execute("SELECT key,value FROM settings").fetchall()
    data = _defaults()
    for row in rows:
        try:
            data[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            continue
    return data


def get_section(name: str) -> dict[str, Any]:
    return get_settings().get(name, {})


def save_section(name: str, value: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time())
    with _lock:
        conn().execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (name, json.dumps(value, ensure_ascii=False), now),
        )
        conn().commit()
    return get_section(name)


def merge_section(name: str, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_section(name)
    for k, v in patch.items():
        current[k] = v
    return save_section(name, current)


# ---------------------------------------------------------------- data


def log_event(level: str, source: str, message: str) -> None:
    with _lock:
        conn().execute(
            "INSERT INTO events(ts,level,source,message) VALUES(?,?,?,?)",
            (int(time.time()), level[:8], source[:32], message[:2000]),
        )
        conn().execute("DELETE FROM events WHERE id < (SELECT MAX(id)-2000 FROM events)")
        conn().commit()


def touch_user(platform: str, user_id: str, nickname: str, ts: int) -> str:
    uid = f"{platform}:{user_id}"
    with _lock:
        row = conn().execute("SELECT uid FROM users WHERE uid=?", (uid,)).fetchone()
        if row:
            conn().execute(
                "UPDATE users SET nickname=?, last_seen=?, msg_count=msg_count+1, updated_at=? WHERE uid=?",
                (nickname, ts, ts, uid),
            )
        else:
            conn().execute(
                "INSERT INTO users(uid,platform,user_id,nickname,first_seen,last_seen,msg_count,impression,updated_at)"
                " VALUES(?,?,?,?,?,?,1,'{}',?)",
                (uid, platform, user_id, nickname, ts, ts, ts),
            )
        conn().commit()
    return uid


def add_message(
    platform: str,
    group_id: str,
    uid: str,
    nickname: str,
    role: str,
    text: str,
    extra: dict[str, Any] | None,
    ts: int,
) -> int:
    with _lock:
        cur = conn().execute(
            "INSERT INTO messages(platform,group_id,uid,nickname,role,text,extra,ts) VALUES(?,?,?,?,?,?,?,?)",
            (platform, group_id, uid, nickname, role, text, json.dumps(extra or {}, ensure_ascii=False), ts),
        )
        conn().commit()
        return int(cur.lastrowid)


def recent_messages(platform: str, group_id: str, limit: int = 30) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            "SELECT * FROM messages WHERE platform=? AND group_id=? ORDER BY id DESC LIMIT ?",
            (platform, group_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def messages_for_uid(uid: str, limit: int = 30) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            "SELECT * FROM messages WHERE uid=? ORDER BY id DESC LIMIT ?", (uid, limit)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear_context(platform: str, group_id: str) -> int:
    with _lock:
        cur = conn().execute(
            "DELETE FROM messages WHERE platform=? AND group_id=?", (platform, group_id)
        )
        conn().commit()
        return cur.rowcount


def groups() -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            """SELECT platform, group_id, COUNT(*) AS n, MAX(ts) AS last_ts
               FROM messages GROUP BY platform, group_id ORDER BY last_ts DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_user(uid: str) -> dict[str, Any] | None:
    with _lock:
        row = conn().execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        data["impression"] = json.loads(data.get("impression") or "{}")
    except json.JSONDecodeError:
        data["impression"] = {}
    return data


def list_users(query: str = "", limit: int = 200) -> list[dict[str, Any]]:
    with _lock:
        if query:
            rows = conn().execute(
                "SELECT * FROM users WHERE nickname LIKE ? OR uid LIKE ? ORDER BY last_seen DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        else:
            rows = conn().execute(
                "SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["impression"] = json.loads(d.get("impression") or "{}")
        except json.JSONDecodeError:
            d["impression"] = {}
        out.append(d)
    return out


def save_user_impression(uid: str, impression: dict[str, Any]) -> None:
    with _lock:
        conn().execute(
            "UPDATE users SET impression=?, updated_at=? WHERE uid=?",
            (json.dumps(impression, ensure_ascii=False), int(time.time()), uid),
        )
        conn().commit()


def bump_reply_count(uid: str) -> None:
    with _lock:
        conn().execute("UPDATE users SET reply_count=reply_count+1 WHERE uid=?", (uid,))
        conn().commit()


def get_summary(gkey: str) -> dict[str, Any] | None:
    with _lock:
        row = conn().execute("SELECT * FROM summaries WHERE gkey=?", (gkey,)).fetchone()
    return dict(row) if row else None


def save_summary(gkey: str, summary: str, upto_id: int) -> None:
    with _lock:
        conn().execute(
            "INSERT INTO summaries(gkey,summary,upto_id,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(gkey) DO UPDATE SET summary=excluded.summary, "
            "upto_id=excluded.upto_id, updated_at=excluded.updated_at",
            (gkey, summary, upto_id, int(time.time())),
        )
        conn().commit()


def log_llm(
    kind: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    latency_ms: int,
    ok: bool,
    error: str = "",
) -> None:
    with _lock:
        conn().execute(
            "INSERT INTO llm_calls(ts,kind,model,prompt_tokens,completion_tokens,cost_usd,latency_ms,ok,error)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (int(time.time()), kind, model, prompt_tokens, completion_tokens, cost_usd, latency_ms, int(ok), error[:500]),
        )
        conn().commit()


def llm_stats(hours: int = 24) -> dict[str, Any]:
    since = int(time.time()) - hours * 3600
    with _lock:
        row = conn().execute(
            """SELECT COUNT(*) n, COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct,
                      COALESCE(SUM(cost_usd),0) cost, COALESCE(AVG(latency_ms),0) lat,
                      COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) errs
               FROM llm_calls WHERE ts>=?""",
            (since,),
        ).fetchone()
    return dict(row)


def message_stats(hours: int = 24) -> dict[str, Any]:
    since = int(time.time()) - hours * 3600
    with _lock:
        row = conn().execute(
            """SELECT COUNT(*) n,
                      COALESCE(SUM(CASE WHEN role='assistant' THEN 1 ELSE 0 END),0) replies,
                      COUNT(DISTINCT uid) users,
                      COUNT(DISTINCT group_id) groups
               FROM messages WHERE ts>=?""",
            (since,),
        ).fetchone()
    return dict(row)


def recent_events(limit: int = 100, level: str = "") -> list[dict[str, Any]]:
    with _lock:
        if level:
            rows = conn().execute(
                "SELECT * FROM events WHERE level=? ORDER BY id DESC LIMIT ?", (level, limit)
            ).fetchall()
        else:
            rows = conn().execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def log_request(
    kind: str,
    user_id: str,
    group_id: str,
    nickname: str,
    comment: str,
    flag: str,
    approved: bool,
    reason: str,
) -> None:
    with _lock:
        conn().execute(
            "INSERT INTO requests(ts,kind,user_id,group_id,nickname,comment,flag,approved,reason)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (int(time.time()), kind, user_id, group_id, nickname, comment[:300], flag, int(approved), reason[:100]),
        )
        conn().commit()


def recent_requests(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def recent_llm(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute("SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def upsert_person_digest(uid: str, summary: str, topics: list[str], msg_count: int) -> None:
    with _lock:
        conn().execute(
            "INSERT INTO person_digests(uid,summary,topics,updated_at,msg_count) VALUES(?,?,?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET summary=excluded.summary, topics=excluded.topics, "
            "updated_at=excluded.updated_at, msg_count=excluded.msg_count",
            (uid, summary, json.dumps(topics, ensure_ascii=False), int(time.time()), msg_count),
        )
        conn().commit()


def get_person_digest(uid: str) -> dict[str, Any] | None:
    with _lock:
        row = conn().execute("SELECT * FROM person_digests WHERE uid=?", (uid,)).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        data["topics"] = json.loads(data.get("topics") or "[]")
    except json.JSONDecodeError:
        data["topics"] = []
    return data


def list_person_digests(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            "SELECT * FROM person_digests ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        try:
            data["topics"] = json.loads(data.get("topics") or "[]")
        except json.JSONDecodeError:
            data["topics"] = []
        out.append(data)
    return out


def set_display_name(uid: str, name: str) -> None:
    with _lock:
        conn().execute(
            "UPDATE users SET display_name=?, updated_at=? WHERE uid=?",
            (name[:40], int(time.time()), uid),
        )
        conn().commit()


def add_alert(level: str, source: str, message: str, dedupe_sec: int = 600) -> bool:
    now = int(time.time())
    with _lock:
        row = conn().execute(
            "SELECT id FROM alerts WHERE source=? AND message=? AND ts>? ORDER BY id DESC LIMIT 1",
            (source, message, now - dedupe_sec),
        ).fetchone()
        if row:
            return False
        conn().execute(
            "INSERT INTO alerts(ts,level,source,message) VALUES(?,?,?,?)",
            (now, level[:8], source[:24], message[:500]),
        )
        conn().commit()
    return True


def list_alerts(limit: int = 50, only_open: bool = False) -> list[dict[str, Any]]:
    with _lock:
        if only_open:
            rows = conn().execute(
                "SELECT * FROM alerts WHERE acked=0 ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn().execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def ack_alert(alert_id: int | None = None) -> int:
    with _lock:
        if alert_id:
            cur = conn().execute("UPDATE alerts SET acked=1 WHERE id=?", (alert_id,))
        else:
            cur = conn().execute("UPDATE alerts SET acked=1 WHERE acked=0")
        conn().commit()
        return cur.rowcount


def upsert_sticker(res_id: str, emoji_id: int, md5: str, url: str, desc: str, tags: str = "") -> None:
    now = int(time.time())
    with _lock:
        conn().execute(
            "INSERT INTO stickers(res_id,emoji_id,md5,url,desc,tags,created_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(res_id) DO UPDATE SET emoji_id=excluded.emoji_id, md5=excluded.md5, "
            "url=excluded.url, desc=CASE WHEN excluded.desc<>'' THEN excluded.desc ELSE stickers.desc END, "
            "tags=CASE WHEN excluded.tags<>'' THEN excluded.tags ELSE stickers.tags END",
            (res_id, emoji_id, md5, url, desc, tags, now),
        )
        conn().commit()


def list_stickers() -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute("SELECT * FROM stickers ORDER BY use_count ASC, created_at DESC").fetchall()
    return [dict(r) for r in rows]


def touch_sticker(res_id: str) -> None:
    with _lock:
        conn().execute(
            "UPDATE stickers SET use_count=use_count+1, last_used=? WHERE res_id=?",
            (int(time.time()), res_id),
        )
        conn().commit()


def sticker_count() -> int:
    with _lock:
        row = conn().execute("SELECT COUNT(*) AS n FROM stickers").fetchone()
    return int(row["n"]) if row else 0


def add_rotation(gkey: str, summary: str, injection: str, important: list[str], msg_count: int) -> int:
    with _lock:
        cur = conn().execute(
            "INSERT INTO rotations(gkey,summary,injection,important,msg_count,created_at) VALUES(?,?,?,?,?,?)",
            (gkey, summary, injection, json.dumps(important, ensure_ascii=False), msg_count, int(time.time())),
        )
        conn().commit()
        return int(cur.lastrowid)


def latest_rotation(gkey: str) -> dict[str, Any] | None:
    with _lock:
        row = conn().execute(
            "SELECT * FROM rotations WHERE gkey=? ORDER BY id DESC LIMIT 1", (gkey,)
        ).fetchone()
    if not row:
        return None
    data = dict(row)
    try:
        data["important"] = json.loads(data.get("important") or "[]")
    except json.JSONDecodeError:
        data["important"] = []
    return data


def list_rotations(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute("SELECT * FROM rotations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        try:
            data["important"] = json.loads(data.get("important") or "[]")
        except json.JSONDecodeError:
            data["important"] = []
        out.append(data)
    return out


def add_qzone_post(ts: int, content: str, tid: str, visibility: int, auto: bool) -> int:
    with _lock:
        cur = conn().execute(
            "INSERT INTO qzone_posts(ts,content,tid,visibility,auto) VALUES(?,?,?,?,?)",
            (ts, content, tid, visibility, int(auto)),
        )
        conn().commit()
        return int(cur.lastrowid)


def list_qzone_posts(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        rows = conn().execute(
            "SELECT * FROM qzone_posts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_qzone_deleted(tid: str) -> None:
    with _lock:
        conn().execute("UPDATE qzone_posts SET deleted=1 WHERE tid=?", (tid,))
        conn().commit()


def export_all() -> dict[str, Any]:
    with _lock:
        settings = {r["key"]: json.loads(r["value"]) for r in conn().execute("SELECT * FROM settings")}
        users = [dict(r) for r in conn().execute("SELECT * FROM users")]
        summaries = [dict(r) for r in conn().execute("SELECT * FROM summaries")]
    # never export secrets in plaintext backup
    for key in ("system",):
        if key in settings:
            settings[key] = {k: v for k, v in settings[key].items() if k not in ("password_hash", "password_salt", "brain_token", "secret")}
    if "model" in settings:
        settings["model"] = {k: v for k, v in settings["model"].items() if k != "api_key"}
    return {"version": 1, "exported_at": int(time.time()), "settings": settings, "users": users, "summaries": summaries}


def import_all(payload: dict[str, Any]) -> None:
    settings = payload.get("settings") or {}
    users = payload.get("users") or []
    summaries = payload.get("summaries") or []
    now = int(time.time())
    with _lock:
        for key, value in settings.items():
            conn().execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), now),
            )
        for u in users:
            conn().execute(
                "INSERT INTO users(uid,platform,user_id,nickname,first_seen,last_seen,msg_count,reply_count,impression,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET nickname=excluded.nickname,"
                " impression=excluded.impression, updated_at=excluded.updated_at",
                (
                    u.get("uid"), u.get("platform", "qq"), u.get("user_id", ""), u.get("nickname", ""),
                    u.get("first_seen", now), u.get("last_seen", now), u.get("msg_count", 0),
                    u.get("reply_count", 0), u.get("impression", "{}"), now,
                ),
            )
        for s in summaries:
            conn().execute(
                "INSERT INTO summaries(gkey,summary,upto_id,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(gkey) DO UPDATE SET summary=excluded.summary",
                (s.get("gkey"), s.get("summary", ""), s.get("upto_id", 0), now),
            )
        conn().commit()
