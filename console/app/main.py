"""FastAPI control console for the QQ AI group member."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import alerts, brain, digests, lore, media, napcat, proactive, rotation, selfops, stickers, store

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
COOKIE = "brain_session"
SESSION_TTL = 7 * 24 * 3600

app = FastAPI(title="QQ Brain Console", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
async def _startup() -> None:
    store.init()
    store.log_event("info", "console", "console started")
    asyncio.create_task(napcat.run())
    asyncio.create_task(proactive.run())
    asyncio.create_task(selfops.run())
    asyncio.create_task(rotation.run())
    asyncio.create_task(stickers.run())
    asyncio.create_task(alerts.run())
    asyncio.create_task(digests.run())
    asyncio.create_task(lore.run())


# ---------------------------------------------------------------- auth


def _sign(data: str) -> str:
    secret = store.get_section("system").get("secret") or ""
    return hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()


def _issue_session() -> str:
    raw = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + SESSION_TTL}).encode()).decode()
    # no "=" padding: a padded value gets quoted in the Set-Cookie header and
    # browsers send the quotes back, which breaks the signature check.
    payload = raw.rstrip("=")
    return f"{payload}.{_sign(payload)}"


def _valid_session(token: str) -> bool:
    token = (token or "").strip().strip('"')
    try:
        payload, sig = token.split(".", 1)
    except ValueError:
        return False
    payload = payload.strip('"')
    sig = sig.strip('"')
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception:
        return False
    return int(data.get("exp", 0)) > time.time()


def _authed(request: Request) -> bool:
    token = request.headers.get("x-brain-token")
    if token and token == store.get_section("system").get("brain_token"):
        return True
    cookie = request.cookies.get(COOKIE, "")
    return bool(cookie) and _valid_session(cookie)


def require(request: Request) -> None:
    if not _authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")


# ---------------------------------------------------------------- pages


@app.get("/")
async def index(request: Request):
    if not _authed(request):
        return FileResponse(STATIC_DIR / "login.html")
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/app")
async def app_page(request: Request):
    if not _authed(request):
        return RedirectResponse(url="/", status_code=302)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
async def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "qqbrain-console", "time": int(time.time())}


@app.get("/api/gen/{name}")
async def gen_image(name: str):
    """Serve generated images so NapCat can fetch them for avatar updates."""
    path = media.image_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)


class LoginBody(BaseModel):
    password: str


_login_fails: dict[str, list[float]] = {}


@app.post("/api/login")
async def login(body: LoginBody, response: Response, request: Request):
    ip = request.client.host if request.client else "?"
    now = time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < 600]
    _login_fails[ip] = fails
    if len(fails) >= 6:
        store.log_event("warn", "auth", f"登录被限流 ip={ip}")
        raise HTTPException(status_code=429, detail="尝试次数过多，请 10 分钟后再试")
    system = store.get_section("system")
    if not store.verify_password(body.password, system.get("password_salt", ""), system.get("password_hash", "")):
        fails.append(now)
        _login_fails[ip] = fails
        store.log_event("warn", "auth", f"登录失败 ip={ip}")
        raise HTTPException(status_code=403, detail="密码不对")
    _login_fails.pop(ip, None)
    response.set_cookie(COOKIE, _issue_session(), httponly=True, samesite="lax", max_age=SESSION_TTL)
    store.log_event("info", "auth", f"登录成功 ip={ip}")
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


class PasswordBody(BaseModel):
    old: str
    new: str = Field(min_length=6)


@app.post("/api/password")
async def change_password(body: PasswordBody, request: Request):
    require(request)
    system = store.get_section("system")
    if not store.verify_password(body.old, system.get("password_salt", ""), system.get("password_hash", "")):
        raise HTTPException(status_code=403, detail="原密码不对")
    salt, digest = store.hash_password(body.new)
    system["password_salt"] = salt
    system["password_hash"] = digest
    system.pop("last_init_password", None)
    store.save_section("system", system)
    store.log_event("info", "auth", "修改控制台密码")
    return {"ok": True}


# ---------------------------------------------------------------- overview


@app.get("/api/status")
async def status(request: Request):
    require(request)
    events = store.recent_events(50)
    last_beat = next((e["ts"] for e in events if e["source"] == "heartbeat"), 0)
    online = (time.time() - last_beat) < 120 if last_beat else False
    return {
        "online": online,
        "last_heartbeat": last_beat,
        "napcat": napcat.STATE,
        "now": int(time.time()),
    }


@app.get("/api/overview")
async def overview(request: Request):
    require(request)
    settings = store.get_settings()
    events = store.recent_events(50)
    last_beat = next((e["ts"] for e in events if e["source"] == "heartbeat"), 0)
    return {
        "messages": store.message_stats(24),
        "llm": store.llm_stats(24),
        "groups": store.groups()[:20],
        "users_total": len(store.list_users(limit=1000)),
        "online": (time.time() - last_beat) < 120 if last_beat else False,
        "last_heartbeat": last_beat,
        "model": {
            "model": settings.get("model", {}).get("model"),
            "vision": settings.get("model", {}).get("vision"),
            "thinking": settings.get("model", {}).get("thinking"),
            "key_set": bool(settings.get("model", {}).get("api_key")),
        },
        "persona": settings.get("persona", {}).get("name"),
        "events": events[:20],
    }


# ---------------------------------------------------------------- settings


@app.get("/api/settings")
async def get_settings(request: Request, section: str = ""):
    require(request)
    data = store.get_settings()
    data["model"] = dict(data.get("model", {}))
    if data["model"].get("api_key"):
        key = data["model"]["api_key"]
        data["model"]["api_key_masked"] = key[:6] + "..." + key[-4:]
        data["model"].pop("api_key", None)
    data.pop("system", None)
    if section:
        return {section: data.get(section, {})}
    return data


class SectionBody(BaseModel):
    value: dict[str, Any]


@app.put("/api/settings/{section}")
async def put_settings(section: str, body: SectionBody, request: Request):
    require(request)
    if section not in {"persona", "context", "trigger", "model", "memory", "emoji", "safety", "friend", "proactive", "selfops", "tools"}:
        raise HTTPException(status_code=400, detail="unknown section")
    patch = dict(body.value)
    if section == "model" and "api_key" not in patch:
        patch["api_key"] = store.get_section("model").get("api_key", "")
    saved = store.save_section(section, {**store.get_section(section), **patch})
    store.log_event("info", "settings", f"更新配置 {section}")
    return {"ok": True, "value": saved}


# ---------------------------------------------------------------- context


@app.get("/api/groups")
async def groups(request: Request):
    require(request)
    return {"groups": store.groups()}


@app.get("/api/context")
async def context(request: Request, platform: str, group_id: str, limit: int = 60):
    require(request)
    rows = store.recent_messages(platform, group_id, max(1, min(limit, 300)))
    gkey = f"{platform}:{group_id}"
    return {"messages": rows, "summary": store.get_summary(gkey), "rotation": store.latest_rotation(gkey)}


QR_PATH = "/napcat-cache/qrcode.png"
KICKED_PATH = "/run/qqbot/kicked"
RUN_DIR = "/run/qqbot"


@app.get("/api/lore")
async def lore_state(request: Request):
    require(request)
    return {"status": lore.status()}


@app.post("/api/lore/refresh")
async def lore_refresh(request: Request):
    require(request)
    return await lore.refresh()


@app.get("/api/people")
async def people(request: Request):
    require(request)
    out = []
    for item in store.list_person_digests(50):
        user = store.get_user(item["uid"]) or {}
        item["name"] = user.get("display_name") or user.get("nickname") or item["uid"]
        out.append(item)
    return {"digests": out, "status": digests.status()}


@app.get("/api/qq/state")
async def qq_state(request: Request):
    require(request)
    connected = bool(napcat.STATE.get("connected"))
    age = None
    if os.path.exists(QR_PATH):
        age = int(time.time() - os.path.getmtime(QR_PATH))
    kicked = False
    if os.path.exists(KICKED_PATH):
        kicked = (time.time() - os.path.getmtime(KICKED_PATH)) < 1800
    return {
        "connected": connected,
        "qr_age": age,
        "qr_url": f"/api/qq/qr.png?t={int(time.time())}" if age is not None else None,
        "kicked": kicked,
    }


@app.get("/api/qq/qr.png")
async def qq_qr(request: Request):
    require(request)
    if not os.path.exists(QR_PATH):
        raise HTTPException(status_code=404, detail="还没有二维码")
    return FileResponse(QR_PATH, headers={"Cache-Control": "no-store"})


@app.post("/api/qq/restart")
async def qq_restart(request: Request):
    require(request)
    os.makedirs(RUN_DIR, exist_ok=True)
    with open(os.path.join(RUN_DIR, "restart-napcat"), "w", encoding="utf-8") as fh:
        fh.write(str(int(time.time())))
    store.log_event("info", "qq", "控制台请求重启 NapCat 以刷新二维码")
    return {"ok": True, "message": "已提交重启，约 30 秒后刷新二维码"}


@app.get("/api/alerts")
async def alert_list(request: Request, only_open: bool = True):
    require(request)
    return {"alerts": store.list_alerts(50, only_open)}


class AckBody(BaseModel):
    id: int | None = None


@app.post("/api/alerts/ack")
async def alert_ack(body: AckBody, request: Request):
    require(request)
    return {"ok": True, "count": store.ack_alert(body.id)}


@app.get("/api/stickers")
async def sticker_list(request: Request):
    require(request)
    return {"stickers": store.list_stickers(), "status": stickers.status()}


@app.post("/api/stickers/sync")
async def sticker_sync(request: Request):
    require(request)
    return await stickers.sync()


@app.get("/api/rotations")
async def rotations(request: Request):
    require(request)
    return {"rotations": store.list_rotations(20), "status": rotation.status()}


class RotateBody(BaseModel):
    group_id: str = ""


@app.post("/api/rotation/run")
async def run_rotation(body: RotateBody, request: Request):
    require(request)
    groups = store.groups()
    targets = [g for g in groups if g["platform"] == "qq" and (not body.group_id or str(g["group_id"]) == body.group_id)]
    done = 0
    for group in targets:
        rows = store.recent_messages(group["platform"], group["group_id"], 200)
        if not rows:
            continue
        await rotation.rotate_group(f"{group['platform']}:{group['group_id']}", rows)
        done += 1
    return {"ok": True, "rotated": done}


@app.delete("/api/context")
async def clear_context(request: Request, platform: str, group_id: str):
    require(request)
    n = store.clear_context(platform, group_id)
    store.log_event("info", "context", f"清空上下文 {platform}:{group_id} ({n})")
    return {"ok": True, "deleted": n}


def _parse_history(message: list[dict[str, Any]], face_map: dict[str, str]) -> tuple[str, list[str], list[Any]]:
    parts: list[str] = []
    images: list[str] = []
    faces: list[Any] = []
    for seg in message or []:
        kind = seg.get("type")
        data = seg.get("data") or {}
        if kind == "text":
            parts.append(str(data.get("text") or ""))
        elif kind == "face":
            fid = str(data.get("id"))
            name = face_map.get(fid)
            parts.append(f"[表情:{name}]" if name else f"[表情#{fid}]")
            faces.append(data.get("id"))
        elif kind == "image":
            url = str(data.get("url") or "")
            if url:
                images.append(url)
            parts.append("[图片]")
        elif kind == "at":
            parts.append(f"@{data.get('qq')}")
        elif kind == "mface":
            parts.append(f"[表情:{data.get('summary') or '商城表情'}]")
        elif kind == "record":
            parts.append("[语音]")
    return "".join(parts).strip(), images, faces


class ImportHistoryBody(BaseModel):
    group_id: str
    count: int = 50


@app.post("/api/context/import")
async def import_history(body: ImportHistoryBody, request: Request):
    require(request)
    if not napcat.STATE.get("connected"):
        raise HTTPException(status_code=503, detail="NapCat 事件通道未连接")
    info = await napcat.call("get_login_info", {})
    self_id = str((info.get("data") or {}).get("user_id") or "")
    resp = await napcat.call(
        "get_group_msg_history",
        {"group_id": int(body.group_id) if body.group_id.isdigit() else body.group_id, "count": min(body.count, 100)},
    )
    messages = (resp.get("data") or {}).get("messages") or []
    face_map = store.get_section("emoji").get("map") or {}
    imported = 0
    for msg in messages:
        sender = msg.get("sender") or {}
        uid = str(sender.get("user_id") or "")
        if not uid or uid == self_id:
            continue
        text, images, faces = _parse_history(msg.get("message") or [], face_map)
        if not text and not images:
            continue
        mid = str(msg.get("message_id") or "")
        with store._lock:  # noqa: SLF001
            exists = store.conn().execute(
                "SELECT 1 FROM messages WHERE platform='qq' AND group_id=? AND json_extract(extra,'$.msg_id')=?",
                (body.group_id, mid),
            ).fetchone()
        if exists:
            continue
        nickname = sender.get("card") or sender.get("nickname") or "群友"
        ts = int(msg.get("time") or time.time())
        store.touch_user("qq", uid, nickname, ts)
        store.add_message(
            "qq", body.group_id, f"qq:{uid}", nickname, "user", text,
            {"images": images, "faces": faces, "msg_id": mid}, ts,
        )
        imported += 1
    store.log_event("info", "context", f"导入群 {body.group_id} 历史 {imported} 条")
    return {"ok": True, "imported": imported, "total": len(messages)}


# ---------------------------------------------------------------- users


@app.get("/api/users")
async def users(request: Request, q: str = ""):
    require(request)
    return {"users": store.list_users(q)}


@app.get("/api/users/{uid}")
async def user_detail(uid: str, request: Request):
    require(request)
    user = store.get_user(uid)
    if not user:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "user": user,
        "messages": store.messages_for_uid(uid, 40),
    }


class ImpressionBody(BaseModel):
    summary: str | None = None
    style: str | None = None
    tags: list[str] | None = None
    facts: list[str] | None = None
    dims: dict[str, int] | None = None
    display_name: str | None = None


@app.put("/api/users/{uid}")
async def update_user(uid: str, body: ImpressionBody, request: Request):
    require(request)
    user = store.get_user(uid)
    if not user:
        raise HTTPException(status_code=404, detail="not found")
    imp = user.get("impression") or {}
    if body.summary is not None:
        imp["summary"] = body.summary
    if body.style is not None:
        imp["style"] = body.style
    if body.tags is not None:
        imp["tags"] = body.tags
    if body.facts is not None:
        imp["facts"] = body.facts
    if body.dims is not None:
        imp["dims"] = {k: max(0, min(100, int(v))) for k, v in body.dims.items()}
    imp["updated_at"] = int(time.time())
    store.save_user_impression(uid, imp)
    if body.display_name is not None:
        store.set_display_name(uid, body.display_name.strip())
    return {"ok": True, "impression": imp}


@app.post("/api/users/{uid}/refresh")
async def refresh_user(uid: str, request: Request):
    require(request)
    if not store.get_user(uid):
        raise HTTPException(status_code=404, detail="not found")
    asyncio.create_task(brain.extract_impression(uid))
    return {"ok": True, "message": "已在后台重新提炼印象，几秒后刷新"}


@app.delete("/api/users/{uid}")
async def delete_user(uid: str, request: Request):
    require(request)
    with store._lock:  # noqa: SLF001
        store.conn().execute("DELETE FROM users WHERE uid=?", (uid,))
        store.conn().execute("DELETE FROM messages WHERE uid=?", (uid,))
        store.conn().commit()
    return {"ok": True}


# ---------------------------------------------------------------- handling


class HandleBody(BaseModel):
    platform: str = "qq"
    group_id: str = ""
    group_name: str = ""
    user_id: str = ""
    nickname: str = "群友"
    text: str = ""
    images: list[str] = Field(default_factory=list)
    faces: list[Any] = Field(default_factory=list)
    at_me: bool = False
    is_private: bool = False
    msg_id: str = ""
    ts: int | None = None


@app.post("/api/handle")
async def handle(body: HandleBody, request: Request):
    if not _authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    payload = body.model_dump()
    payload["ts"] = payload.get("ts") or int(time.time())
    return await brain.handle(payload)


@app.post("/api/chat")
async def chat(body: HandleBody, request: Request):
    """Simulator: same pipeline, forced platform=sim, always answers."""
    require(request)
    payload = body.model_dump()
    payload["platform"] = "sim"
    payload["group_id"] = payload.get("group_id") or "demo"
    payload["user_id"] = payload.get("user_id") or "10001"
    payload["nickname"] = payload.get("nickname") or "测试群友"
    payload["at_me"] = True
    payload["ts"] = int(time.time())
    return await brain.handle(payload)


@app.post("/api/heartbeat")
async def heartbeat(request: Request, body: dict[str, Any] | None = None):
    if not _authed(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    store.log_event("info", "heartbeat", (body or {}).get("self_id") or "alive")
    return {"ok": True}


@app.get("/api/system")
async def system_info(request: Request):
    require(request)
    system = store.get_section("system")
    events = store.recent_events(50)
    beat = next((e for e in events if e["source"] == "heartbeat"), None)
    return {
        "brain_token": system.get("brain_token", ""),
        "version": "1.0.0",
        "self_id": (beat or {}).get("message", ""),
        "installed_at": system.get("installed_at", 0),
    }


# ---------------------------------------------------------------- logs / model


@app.get("/api/logs")
async def logs(request: Request, limit: int = 100, level: str = ""):
    require(request)
    return {"events": store.recent_events(min(limit, 500), level), "llm": store.recent_llm(50)}


@app.get("/api/requests")
async def requests_list(request: Request, limit: int = 50):
    require(request)
    return {"requests": store.recent_requests(min(limit, 200))}


@app.get("/api/self")
async def self_info(request: Request):
    require(request)
    try:
        profile = await selfops.get_profile()
    except Exception as exc:  # noqa: BLE001
        profile = {"error": str(exc)}
    return {"profile": profile, "config": store.get_section("selfops"), **selfops.status()}


class ProfileBody(BaseModel):
    nickname: str = ""
    signature: str = ""


@app.post("/api/self/profile")
async def self_profile(body: ProfileBody, request: Request):
    require(request)
    try:
        await selfops.set_profile(body.nickname.strip(), body.signature.strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


class AvatarBody(BaseModel):
    url: str


@app.post("/api/self/avatar")
async def self_avatar(body: AvatarBody, request: Request):
    require(request)
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="图片地址不能为空")
    try:
        await selfops.set_avatar(body.url.strip())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


class QzoneBody(BaseModel):
    content: str = ""
    visibility: int = 1


@app.post("/api/self/qzone")
async def self_qzone(body: QzoneBody, request: Request):
    require(request)
    try:
        tid = await selfops.qzone_publish(body.content, body.visibility)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "tid": tid}


@app.post("/api/self/qzone/draft")
async def self_qzone_draft(request: Request):
    require(request)
    try:
        text = await selfops.draft()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "text": text}


class QzoneDeleteBody(BaseModel):
    tid: str


@app.post("/api/self/qzone/delete")
async def self_qzone_delete(body: QzoneDeleteBody, request: Request):
    require(request)
    try:
        await selfops.qzone_delete(body.tid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


@app.get("/api/proactive/status")
async def proactive_status(request: Request):
    require(request)
    cfg = store.get_section("proactive")
    data = proactive.status()
    data["config"] = cfg
    data["napcat_connected"] = bool(napcat.STATE.get("connected"))
    return data


class ProactiveBody(BaseModel):
    group_id: str


@app.post("/api/proactive/trigger")
async def proactive_trigger(body: ProactiveBody, request: Request):
    require(request)
    if not napcat.STATE.get("connected"):
        raise HTTPException(status_code=503, detail="NapCat 事件通道未连接")
    try:
        payload = await proactive.send_now(body.group_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    if not payload:
        raise HTTPException(status_code=400, detail="没有足够上下文或生成失败")
    return {"ok": True, "text": payload["text"], "faces": payload.get("faces") or []}


class TestBody(BaseModel):
    prompt: str = "用一句话介绍一下你自己"


@app.post("/api/model/test")
async def model_test(body: TestBody, request: Request):
    require(request)
    started = time.time()
    try:
        resp = await brain.call_llm(
            [{"role": "user", "content": body.prompt}], kind="test", max_tokens=120, temperature=0.7
        )
        return {
            "ok": True,
            "text": resp["text"],
            "latency_ms": int((time.time() - started) * 1000),
            "usage": resp.get("usage"),
            "cost": resp.get("cost"),
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


# ---------------------------------------------------------------- backup


@app.get("/api/export")
async def export(request: Request):
    require(request)
    data = store.export_all()
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="qqbrain-backup.json"'},
    )


class ImportBody(BaseModel):
    payload: dict[str, Any]


@app.post("/api/import")
async def import_data(body: ImportBody, request: Request):
    require(request)
    store.import_all(body.payload)
    store.log_event("info", "backup", "导入备份")
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    store.log_event("error", "api", f"{request.url.path}: {type(exc).__name__}: {exc}")
    return JSONResponse(status_code=500, content={"ok": False, "error": f"{type(exc).__name__}: {exc}"})
