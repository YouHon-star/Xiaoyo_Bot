"""随机表情包：群里每条消息掷一次骰子，2% 中奖就发一张**账号收藏的表情**（QQ 表情面板「爱心」那栏）。

素材来源（按优先级）：
  1. 账号收藏表情 —— 走 OneBot `fetch_custom_face`（NapCat 已实现），结果缓存到
     storage/sticker_favorites.json，30 分钟过期后自动重取
  2. storage/stickers/ 本地图片（jpg/png/gif/webp…）—— 兜底，也可以自己往里丢
  3. 群友发过的表情 URL（storage/sticker_cache.json）—— 最后兜底

两道闸：
  · PROB = 0.02        —— 每条群消息 2%
  · COOLDOWN = 180 秒  —— 同一群最短间隔（想更频繁就调小；要「纯 2%」就设 0）

调试/自检：群里发「小幽表情」→ 立刻报收藏表情张数并发一张（同群 60 秒限一次）。
"""

import asyncio
import json
import random
import time
from pathlib import Path

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment

BASE_DIR = Path(__file__).resolve().parents[2]      # .../Xiaoyo_Bot
LOCAL_DIR = BASE_DIR / "storage" / "stickers"
CACHE_FILE = BASE_DIR / "storage" / "sticker_cache.json"        # 群友表情兜底
FAV_FILE = BASE_DIR / "storage" / "sticker_favorites.json"      # 账号收藏表情

PROB = 0.02            # 2%
COOLDOWN = 180.0       # 同群最短间隔（秒）
FAV_TTL = 1800.0       # 收藏表情多久重取一次（秒）
CMD_COOLDOWN = 60.0    # 「小幽表情」命令同群限频
CACHE_MAX = 300
SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

_last_send: dict[int, float] = {}
_last_cmd: dict[int, float] = {}
_cache: list[str] = []          # 群友表情 URL
_favs: list[str] = []           # 账号收藏表情 URL
_fav_ts: float = 0.0
_loaded = False
_warned = False
_warmed = False          # 是否已经预热过收藏表情列表


# ---------------- 收藏表情（主素材）----------------
def _extract_urls(res) -> list[str]:
    """兼容各种返回形状：["url"] / {"data": ["url"]} / {"data": [{"url": ...}]} / {"urls": ...}。"""
    items = res
    if isinstance(res, dict):
        for k in ("data", "urls", "list", "faces"):
            if isinstance(res.get(k), (list, tuple)):
                items = res[k]
                break
        else:
            items = [res]
    if not isinstance(items, (list, tuple)):
        items = [items]

    urls: list[str] = []
    for it in items:
        if isinstance(it, str):
            u = it
        elif isinstance(it, dict):
            u = it.get("url") or it.get("file") or it.get("resId") or ""
        else:
            u = str(it)
        u = str(u).strip()
        if u:
            urls.append(u)
    return urls


def _load_favs() -> None:
    global _favs, _fav_ts, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        d = json.loads(FAV_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            _favs = [u for u in d.get("urls", []) if isinstance(u, str)]
            _fav_ts = float(d.get("ts") or 0.0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        _favs, _fav_ts = [], 0.0


def _save_favs(urls: list[str]) -> None:
    global _favs, _fav_ts
    _favs = urls
    _fav_ts = time.time()
    try:
        FAV_FILE.parent.mkdir(parents=True, exist_ok=True)
        FAV_FILE.write_text(json.dumps({"ts": _fav_ts, "urls": _favs}, ensure_ascii=False),
                            encoding="utf-8")
    except OSError:
        pass


async def refresh_favorites(bot: Bot, force: bool = False) -> int:
    """拉一次账号收藏表情；返回拿到多少张。失败返回 -1（保留旧缓存）。"""
    global _warned
    _load_favs()
    if not force and _favs and time.time() - _fav_ts < FAV_TTL:
        return len(_favs)

    res = None
    last = None
    for params in ({}, {"count": 300}):
        try:
            res = await asyncio.wait_for(bot.call_api("fetch_custom_face", **params), timeout=10)
            break
        except asyncio.TimeoutError:
            if not _warned:
                print("[sticker] fetch_custom_face 超时", flush=True)
                _warned = True
            return -1
        except Exception as e:  # noqa: BLE001  参数不兼容时换一种再试
            last = e
            continue
    else:
        if not _warned:
            print(f"[sticker] fetch_custom_face 不可用: {last!r}", flush=True)
            _warned = True
        return -1

    urls = _extract_urls(res)
    if not urls:
        return -len(_favs) if _favs else 0
    _save_favs(urls)
    return len(urls)


# ---------------- 其它素材 ----------------
def _load_cache() -> None:
    global _cache, _loaded
    _load_favs()
    if _cache:
        return
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
    """('fav', url) → 账号收藏表情；('file', Path) → 本地；('url', url) → 群友表情。"""
    _load_cache()
    if _favs:
        return ("fav", random.choice(_favs))
    files = local_files()
    if files:
        return ("file", random.choice(files))
    if _cache:
        return ("url", random.choice(_cache))
    return None


# ---------------- 掷骰子 ----------------
def dice(gid: int, now: float | None = None, roll: float | None = None) -> bool:
    """这条消息要不要发（只判概率与冷却，不看素材）。"""
    now = time.time() if now is None else now
    if now - _last_send.get(gid, 0.0) < COOLDOWN:
        return False
    if (random.random() if roll is None else roll) >= PROB:
        return False
    _last_send[gid] = now
    return True


def should_send(gid: int, now: float | None = None, roll: float | None = None) -> bool:
    """掷骰子 + 有素材才发（离线回归用这个）。"""
    if pick_source() is None:
        return False
    return dice(gid, now, roll)


def is_sticker_segment(seg) -> bool:
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
    global _cache, _favs, _fav_ts, _loaded, _warned, _warmed
    _last_send.clear()
    _last_cmd.clear()
    _cache, _favs, _fav_ts, _loaded, _warned = [], [], 0.0, False, False
    _warmed = True            # 默认已预热，避免测试里起后台任务


def _forget(url: str) -> None:
    if url in _favs:
        _favs.remove(url)
        _save_favs(_favs)
    if url in _cache:
        _cache.remove(url)
        _save_cache()


async def _warm(bot: Bot) -> None:
    """后台预热：拉一次收藏表情并打到日志，方便确认这条链路通不通。"""
    try:
        n = await refresh_favorites(bot, force=True)
        print(f"[sticker] 账号收藏表情预热：{n} 张", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[sticker] 收藏表情预热失败：{e!r}", flush=True)


# ---------------- 挂载 ----------------
sticker_watch = on_message(priority=20, block=False)


@sticker_watch.handle()
async def handle_sticker(bot: Bot, event: GroupMessageEvent) -> None:
    global _warmed
    # 0) 第一次见到群消息时后台预热收藏表情（不阻塞、失败也不影响聊天）
    if not _warmed:
        _warmed = True
        asyncio.create_task(_warm(bot))

    # 1) 顺手收集群友的表情（兜底素材）
    try:
        for seg in event.get_message():
            if is_sticker_segment(seg):
                remember(str((seg.data or {}).get("url", "")))
    except Exception:  # noqa: BLE001
        pass

    if not dice(event.group_id):
        return

    await refresh_favorites(bot)          # 只有真要发的时候才去拉收藏列表
    src = pick_source()
    if src is None:
        return
    kind, val = src
    try:
        if kind == "file":
            await sticker_watch.send(MessageSegment.image(Path(val).read_bytes()))
        else:
            await sticker_watch.send(MessageSegment.image(val))
    except Exception:  # noqa: BLE001  QQ 图链会过期，失败就剔掉
        if kind != "file":
            _forget(str(val))


# ---------------- 自检命令 ----------------
fav_cmd = on_command("小幽表情", aliases={"表情", "表情包", "小幽表情包", "收藏表情"},
                     priority=5, block=True)


@fav_cmd.handle()
async def handle_fav(event: MessageEvent) -> None:
    gid = int(getattr(event, "group_id", 0) or 0)
    now = time.time()
    if now - _last_cmd.get(gid, 0.0) < CMD_COOLDOWN:
        return
    _last_cmd[gid] = now

    from nonebot import get_bot
    try:
        bot = get_bot()
    except Exception:  # noqa: BLE001
        bot = None
    if bot is None:
        await fav_cmd.finish("❌ 拿不到 bot 连接，稍后再试")
        return

    n = await refresh_favorites(bot, force=True)
    _load_favs()
    total = len(_favs)
    if total == 0:
        await fav_cmd.finish(
            "🎴 没读到账号收藏表情\n"
            "· 先在 QQ 表情面板「爱心」里收藏几个\n"
            f"· 或把图片丢进 {LOCAL_DIR}\n"
            "（fetch_custom_face 调用失败也会这样）"
        )
        return
    head = f"🎴 账号收藏表情 {total} 张（本次拉取 {n}）"
    src = pick_source()
    if src is None:
        await fav_cmd.finish(head)
        return
    await fav_cmd.send(head)          # 先报数量，再发一张
    kind, val = src
    if kind == "file":
        await fav_cmd.finish(MessageSegment.image(Path(val).read_bytes()))
    await fav_cmd.finish(MessageSegment.image(str(val)))
