"""Generated-image storage and avatar updates."""

from __future__ import annotations

import os
import secrets
import time

from . import napcat, store

GEN_DIR = os.environ.get("GEN_DIR", "/data/gen")


def save_image(data: bytes, mime: str = "image/jpeg") -> str:
    os.makedirs(GEN_DIR, exist_ok=True)
    ext = "png" if "png" in mime else "webp" if "webp" in mime else "jpg"
    name = f"{int(time.time())}-{secrets.token_hex(4)}.{ext}"
    with open(os.path.join(GEN_DIR, name), "wb") as fh:
        fh.write(data)
    return name


def image_path(name: str) -> str | None:
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    path = os.path.join(GEN_DIR, name)
    return path if os.path.isfile(path) else None


async def set_avatar(data: bytes, mime: str = "image/jpeg") -> str:
    name = save_image(data, mime)
    url = f"http://console:8088/api/gen/{name}"
    await napcat.call("set_qq_avatar", {"file": url})
    cfg = store.get_section("selfops")
    cfg["avatar_url"] = url
    store.save_section("selfops", cfg)
    store.log_event("info", "selfops", f"头像已更新 {name}")
    return url
