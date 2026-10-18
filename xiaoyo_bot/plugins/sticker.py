"""随机表情包：群里每条消息掷一次骰子，2% 中奖就发一张**账号收藏的表情**（QQ 表情面板「爱心」那栏）。

素材来源（按优先级）：
  1. 账号收藏表情 —— OneBot `fetch_custom_face`（NapCat）取 URL，**下载到本地并做画质增强**，
     存 storage/sticker_favorites/，30 分钟过期后重新同步
  2. storage/stickers/ 本地图片（jpg/png/gif/webp…）—— 兜底，也可以自己往里丢
  3. 群友发过的表情 URL（storage/sticker_cache.json）—— 最后兜底

两道闸：
  · PROB = 0.02        —— 每条群消息 2%
  · COOLDOWN = 180 秒  —— 同一群最短间隔（想更频繁就调小；要「纯 2%」就设 0）

画质：QQ 表情面板的原始文件只有 328×328、8~9KB 的重压缩 JPEG，直接转发在 2x/3x 屏上
会被客户端放大而发糊。这里在本地做无损放大（Lanczos ≤3x、边长上限 1024）+ 轻度 USM，
再以 baseline JPEG q96 4:4:4 落盘，客户端变成「缩小」渲染，观感明显更锐。
动图（GIF / 动画 WEBP）和带透明通道的图**一律原样保留**，不做重编码。
"""

import asyncio
import hashlib
import io
import json
import random
import time
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

try:
    from PIL import Image, ImageFilter
except ImportError:  # pragma: no cover
    Image = None
    ImageFilter = None

BASE_DIR = Path(__file__).resolve().parents[2]      # .../Xiaoyo_Bot
LOCAL_DIR = BASE_DIR / "storage" / "stickers"
FAV_DIR = BASE_DIR / "storage" / "sticker_favorites"            # 收藏表情本地副本（增强后）
CACHE_FILE = BASE_DIR / "storage" / "sticker_cache.json"        # 群友表情兜底
FAV_FILE = BASE_DIR / "storage" / "sticker_favorites.json"      # 收藏表情索引

PROB = 0.02            # 2%
COOLDOWN = 180.0       # 同群最短间隔（秒）
FAV_TTL = 1800.0       # 收藏表情多久重取一次（秒）
CACHE_MAX = 300
SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# 画质增强
ENHANCE_MIN_EDGE = 512      # 短边小于它就放大
ENHANCE_MAX_EDGE = 1024     # 放大后长边上限
ENHANCE_MAX_UPSCALE = 3.0   # 最多放大 3 倍
JPEG_QUALITY = 96

_last_send: dict[int, float] = {}
_cache: list[str] = []          # 群友表情 URL
_favs: list[str] = []           # 收藏表情：本地绝对路径优先，下载失败时退化为 URL
_fav_ts: float = 0.0
_loaded = False
_warned = False
_warmed = False          # 是否已经预热过收藏表情列表

STICKER_SUMMARY = "[动画表情]"

try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9.1
    _LANCZOS = getattr(Image, "LANCZOS", 1)


# ---------------- 图片工具 ----------------
def sniff_ext(data: bytes) -> str:
    """按文件头判断格式（QQ 表情 URL 结尾没有扩展名，只能靠 magic）。"""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    return ""


def is_animated(data: bytes, ext: str) -> bool:
    """GIF 一律当动图；WEBP 看 ANIM 块。动图不重编码，免得掉帧。"""
    if ext == "gif":
        return True
    if ext == "webp":
        return b"ANIM" in data[:64] or b"ANMF" in data[:64]
    return False


def _has_alpha(im) -> bool:
    if im.mode in ("RGBA", "LA"):
        return True
    return im.mode == "P" and "transparency" in im.info


def enhance(data: bytes) -> tuple[bytes, str]:
    """静态图做「无损放大 + 轻度锐化」后重编码；动图原样返回。

    - 不透明静态图 → baseline JPEG q96 4:4:4（无二次渐进解码模糊、无色度抽样）
    - 带透明通道的图 → 放大后仍存 PNG，alpha 通道单独保留，不锐化（避免白边）
    - GIF / 动画 WEBP → 一个字节都不动（重编码会掉帧、变色）
    返回 (bytes, ext)。没有 Pillow 时退化为原样返回。
    """
    ext = sniff_ext(data)
    if not ext:
        return data, "jpg"
    if is_animated(data, ext):
        return data, ext
    if Image is None:
        return data, ext
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001  坏图就原样发
        return data, ext

    w, h = im.size
    edge = max(w, h)
    scale = 1.0
    if edge < ENHANCE_MIN_EDGE:
        scale = min(ENHANCE_MAX_UPSCALE, ENHANCE_MAX_EDGE / max(edge, 1))
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))

    if _has_alpha(im):
        rgba = im.convert("RGBA")
        if scale > 1.01:
            rgba = rgba.resize(new_size, _LANCZOS)
            alpha = rgba.getchannel("A")
            rgb = rgba.convert("RGB").filter(
                ImageFilter.UnsharpMask(radius=1.2, percent=45, threshold=3))
            rgba = rgb.convert("RGBA")
            rgba.putalpha(alpha)
        buf = io.BytesIO()
        rgba.save(buf, "PNG", optimize=True)
        out = buf.getvalue()
        return (out if len(out) < 4 * 1024 * 1024 else data), "png"

    im = im.convert("RGB")
    if scale > 1.01:
        im = im.resize(new_size, _LANCZOS)
        im = im.filter(ImageFilter.UnsharpMask(radius=1.4, percent=55, threshold=3))
    else:
        im = im.filter(ImageFilter.UnsharpMask(radius=1.0, percent=35, threshold=3))

    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True,
            progressive=False)      # baseline，避免二次渐进解码发糊
    out = buf.getvalue()
    return (out if len(out) < 4 * 1024 * 1024 else data), "jpg"


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
            entries = d.get("files") or d.get("urls") or []
            _favs = [str(u) for u in entries if isinstance(u, str)]
            _fav_ts = float(d.get("ts") or 0.0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        _favs, _fav_ts = [], 0.0
    # 本地文件被删掉了就别再引用它
    _favs = [e for e in _favs if e.startswith("http") or Path(e).is_file()]


def _save_favs(entries: list[str]) -> None:
    global _favs, _fav_ts
    _favs = entries
    _fav_ts = time.time()
    try:
        FAV_FILE.parent.mkdir(parents=True, exist_ok=True)
        FAV_FILE.write_text(
            json.dumps({"ts": _fav_ts, "files": _favs}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


async def _download(url: str) -> bytes | None:
    if httpx is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.content
    except Exception as e:  # noqa: BLE001
        print(f"[sticker] 下载失败 {url[:60]}: {e!r}", flush=True)
        return None


async def localize(urls: list[str]) -> list[str]:
    """把收藏表情 URL 落成本地文件（顺手做画质增强）；返回可用的条目列表。

    本地路径优先；单张下载失败就保留 URL 兜底。同步后清掉已经不存在的旧文件。
    """
    FAV_DIR.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    keep: set[str] = set()
    for u in urls:
        name = hashlib.md5(u.encode()).hexdigest()
        exist = None
        for p in FAV_DIR.glob(name + ".*"):
            if p.is_file():
                exist = p
                break
        if exist is not None:
            out.append(str(exist))
            keep.add(exist.name)
            continue
        data = await _download(u)
        if data is None:
            out.append(u)             # 兜底：直接发 URL
            continue
        data, ext = enhance(data)
        p = FAV_DIR / f"{name}.{ext}"
        try:
            p.write_bytes(data)
        except OSError:
            out.append(u)
            continue
        out.append(str(p))
        keep.add(p.name)

    for old in FAV_DIR.iterdir():
        if old.is_file() and old.name not in keep and not old.name.endswith(".json"):
            try:
                old.unlink()
            except OSError:
                pass
    return out


async def refresh_favorites(bot: Bot, force: bool = False) -> int:
    """同步一次账号收藏表情；返回可用张数。失败返回 -1（保留旧缓存）。"""
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
    entries = await localize(urls)
    if not entries:
        return -1
    _save_favs(entries)
    return len(entries)


# ---------------- 其它素材 ----------------
def _load_cache() -> None:
    global _cache
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
    """('file', Path) → 本地文件（收藏表情副本或 storage/stickers 里的图）；('url', url) → 群友表情。"""
    _load_cache()
    if _favs:
        e = random.choice(_favs)
        if e.startswith("http"):
            return ("url", e)
        p = Path(e)
        if p.is_file():
            return ("file", p)
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


STICKER_SUMMARY = "[动画表情]"


def sticker_segment(kind: str, val) -> MessageSegment:
    """把素材打成「表情」段，而不是普通图片。

    NapCat 用 picElement.picSubType 区分渲染方式：
        0 = 普通图片（出现在聊天里是一张图）
        1 = 表情 / 收藏表情（QQ 按表情样式渲染，点开是大图）
    OneBot 侧对应 image 段的 sub_type 字段。只传 file 时 sub_type 缺省为 0，
    收藏表情会被当成图片发出去。
    """
    if kind == "file":
        seg = MessageSegment.image(Path(val).read_bytes())
    else:
        seg = MessageSegment.image(str(val))
    seg.data["sub_type"] = 1
    seg.data["summary"] = STICKER_SUMMARY
    return seg


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
    _cache, _favs, _fav_ts, _loaded, _warned = [], [], 0.0, False, False
    _warmed = True            # 默认已预热，避免测试里起后台任务


def _forget(entry: str) -> None:
    if entry in _favs:
        _favs.remove(entry)
        _save_favs(_favs)
    if entry in _cache:
        _cache.remove(entry)
        _save_cache()


async def _warm(bot: Bot) -> None:
    """后台预热：同步一次收藏表情并打日志，方便确认这条链路通不通。"""
    try:
        n = await refresh_favorites(bot, force=True)
        print(f"[sticker] 收藏表情已就绪：{n} 张", flush=True)
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

    await refresh_favorites(bot)          # 只有真要发的时候才去同步素材
    src = pick_source()
    if src is None:
        return
    kind, val = src
    try:
        await sticker_watch.send(sticker_segment(kind, val))
    except Exception:  # noqa: BLE001  QQ 图链会过期，失败就剔掉
        if kind == "url":
            _forget(str(val))
        elif Path(val).parent == FAV_DIR:
            _forget(str(val))
