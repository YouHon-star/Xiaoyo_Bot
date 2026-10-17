"""随机表情包：群里每条消息掷一次骰子，2% 中奖就丢一张表情包。

素材来源（两个都支持，自动挑）：
  1. storage/stickers/ 下的本地图片（jpg/jpeg/png/gif/webp）—— 你往这里丢就行
  2. 群友自己发过的表情 —— 自动收集，存在 storage/sticker_cache.json（环形，最多 300 条）

两道闸：
  · PROB = 0.02        —— 每条群消息 2%
  · COOLDOWN = 180 秒  —— 同一群最短间隔，避免群里刷屏（想更频繁就调小）

只统计「表情类」图片（sub_type=1 或 summary 里带「表情」），普通照片不进缓存。
"""

import json
import random
import time
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

BASE_DIR = Path(__file__).resolve().parents[2]      # .../Xiaoyo_Bot
LOCAL_DIR = BASE_DIR / "storage" / "stickers"
CACHE_FILE = BASE_DIR / "storage" / "sticker_cache.json"

PROB = 0.02            # 2%
COOLDOWN = 180.0       # 同群最短间隔（秒）
CACHE_MAX = 300        # URL 缓存上限
SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

_last_send: dict[int, float] = {}
_cache: list[str] = []
_loaded = False


# ---------------- 素材 ----------------
def _load_cache() -> None:
    global _cache, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            _cache = [u for u in data if isinstance(u, str) and u.startswith("http")]
    except (OSError, json.JSONDecodeError):
        _cache = []


def _save_cache() -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(_cache[-CACHE_MAX:], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def remember(url: str) -> None:
    """记一个群友发过的表情 URL（去重 + 环形）。"""
    _load_cache()
    if not url or not url.startswith("http") or url in _cache:
        return
    _cache.append(url)
    del _cache[:-CACHE_MAX]
    _save_cache()


def local_files() -> list[Path]:
    if not LOCAL_DIR.is_dir():
        return []
    return sorted(p for p in LOCAL_DIR.iterdir() if p.suffix.lower() in SUFFIXES)


def pick_source() -> tuple[str, Path | str] | None:
    """返回 ('file', Path) / ('url', str) / None。本地图优先。"""
    _load_cache()
    files = local_files()
    if files:
        return ("file", random.choice(files))
    if _cache:
        return ("url", random.choice(_cache))
    return None


# ---------------- 掷骰子 ----------------
def should_send(gid: int, now: float | None = None, roll: float | None = None) -> bool:
    """这一条消息要不要发表情包。"""
    now = time.time() if now is None else now
    if now - _last_send.get(gid, 0.0) < COOLDOWN:
        return False
    if (random.random() if roll is None else roll) >= PROB:
        return False
    if pick_source() is None:          # 没素材就别空转
        return False
    _last_send[gid] = now
    return True


def is_sticker_segment(seg) -> bool:
    """QQ 表情包/动画表情判定。"""
    try:
        if seg.type != "image":
            return False
        d = seg.data or {}
        if str(d.get("sub_type", "0")) == "1":
            return True
        return "表情" in str(d.get("summary", ""))
    except AttributeError:
        return False


def reset() -> None:
    """测试用。"""
    global _cache, _loaded
    _last_send.clear()
    _cache = []
    _loaded = False


# ---------------- 挂载 ----------------
sticker_watch = on_message(priority=20, block=False)


def _forget(url: str) -> None:
    if url in _cache:
        _cache.remove(url)
        _save_cache()


@sticker_watch.handle()
async def handle_sticker(bot: Bot, event: GroupMessageEvent) -> None:
    # 1) 顺手收集群友的表情
    try:
        for seg in event.get_message():
            if is_sticker_segment(seg):
                remember(str((seg.data or {}).get("url", "")))
    except Exception:  # noqa: BLE001  收集失败不影响主流程
        pass

    # 2) 掷骰子
    gid = event.group_id
    if not should_send(gid):
        return

    src = pick_source()
    if src is None:
        return
    kind, val = src
    try:
        if kind == "file":
            await sticker_watch.send(MessageSegment.image(Path(val).read_bytes()))
        else:
            await sticker_watch.send(MessageSegment.image(val))
    except Exception:  # noqa: BLE001  QQ 图片 URL 会过期，失败就从缓存里剔掉
        if kind == "url":
            _forget(str(val))
