"""小幽查询收藏品：按「类型 + 名称/别名/ID」查藏品，返回 ID 和图片。

用法：
  小幽查询收藏品+姓名框+ちほー
  小幽查询收藏品+头像+乙姫
  小幽查询收藏品+背景+100101          （纯数字 = 按 ID 查）
  小幽查询收藏品+徽章                 （不给类型 = 六类全搜）

数据来源：
  · ID / 名称：`data/collections_full.json`（本仓库自带；本机部署时若存在
    `/root/mai_research/data/collections_full.json` 则优先用实时表）
    （由 tools/fetch_collections.py 合并上游 Flora-Software/maimai-resourse 与旧表）
  · 字形折叠：`data/char_fold.json`
    （由 tools/build_char_fold.py 从 Unihan 变体表 + opencc 生成，日文新字体/繁体统一折到简体，
     所以「黑姬」能命中「黒姫」）
  · 图片：公开资源库 basilomorireal/maimai-asset-browser 的 assets/ 目录
    文件名规则 `UI_{Plate|Icon|Chara|Frame}_%06d.png`，首次用到时下载并缓存到
    `storage/item_assets/<分类>/<id>.png`，之后直接读缓存，不再联网。

覆盖：姓名框/头像/背景/旅行伙伴 有图；称号是纯文字、搭档上游没有图 → 只回文字。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.log import logger

BASE_DIR = Path(__file__).resolve().parents[2]
# 仓库自带数据优先（克隆即可用），本机部署时回落到 mai_research 的实时表
_REPO_DATA = BASE_DIR / "data"
TABLE = _REPO_DATA / "collections_full.json"
if not TABLE.exists():
    TABLE = Path("/root/mai_research/data/collections_full.json")
FOLD_TABLE = _REPO_DATA / "char_fold.json"
if not FOLD_TABLE.exists():
    FOLD_TABLE = Path("/root/mai_research/data/char_fold.json")
CACHE_DIR = BASE_DIR / "storage" / "item_assets"
ASSET_BASE = ("https://raw.githubusercontent.com/basilomorireal/"
              "maimai-asset-browser/main/assets/{cat}/{prefix}{iid:06d}.png")

# itemKind → (中文名, 表 key, 图片分类, 文件名前缀)；没有图的类型后两项为 None
KINDS: dict[int, tuple[str, str, str | None, str | None]] = {
    1:  ("姓名框",   "姓名框 (1)",   "plates", "UI_Plate_"),
    2:  ("称号",     "称号 (2)",     None,     None),
    3:  ("头像",     "头像 (3)",     "icons",  "UI_Icon_"),
    9:  ("旅行伙伴", "旅行伙伴 (9)", "charas", "UI_Chara_"),
    10: ("搭档",     "搭档 (10)",    None,     None),
    11: ("背景",     "背景 (11)",    "frames", "UI_Frame_"),
}

# 用户可能写的类型名 → itemKind
KIND_ALIAS: dict[str, int] = {
    "姓名框": 1, "框": 1, "边框": 1, "plate": 1, "nameplate": 1,
    "称号": 2, "title": 2,
    "头像": 3, "图标": 3, "icon": 3,
    "旅行伙伴": 9, "伙伴": 9, "角色": 9, "chara": 9, "character": 9,
    "搭档": 10, "partner": 10,
    "背景": 11, "背景板": 11, "frame": 11,
}

MAX_LIST = 120         # 模糊命中时最多列几条（QQ 单条约 4500 字，够放一百多条）
_SEP_RE = re.compile(r"[+＋,，、\s]+")
_CMD_WORDS = ("查询收藏品", "查收藏品", "查藏品", "查询藏品")

_table_cache: tuple[float, dict] | None = None
_fold_cache: tuple[float, dict[str, str]] | None = None


def load_table() -> dict:
    """按 mtime 缓存读取藏品表（刷新表后不用重启 bot）。"""
    global _table_cache
    try:
        mtime = TABLE.stat().st_mtime
    except OSError:
        return {}
    if _table_cache and _table_cache[0] == mtime:
        return _table_cache[1]
    try:
        data = json.loads(TABLE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"[item_query] 读藏品表失败：{exc}")
        return {}
    _table_cache = (mtime, data)
    return data


def load_fold() -> dict[str, str]:
    """字形折叠表（日文新字体/繁体 → 简体），按 mtime 缓存。"""
    global _fold_cache
    try:
        mtime = FOLD_TABLE.stat().st_mtime
    except OSError:
        return {}
    if _fold_cache and _fold_cache[0] == mtime:
        return _fold_cache[1]
    try:
        data = json.loads(FOLD_TABLE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"[item_query] 读字形折叠表失败：{exc}")
        return {}
    _fold_cache = (mtime, data)
    return data


def _norm(s: str) -> str:
    """归一化：全角转半角、去空格、小写（中文/日文原样）。"""
    out = []
    for ch in str(s or ""):
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return re.sub(r"\s+", "", "".join(out)).lower()


def _fold(s: str) -> str:
    """把日文新字体/繁体字逐个折到简体（黒姫 → 黑姬），查不到的交给 t2s 之外的规则。"""
    m = load_fold()
    if not m:
        return s
    return "".join(m.get(ch, ch) for ch in s)


def rows_of(kind: int) -> list[tuple[int, str]]:
    spec = KINDS.get(kind)
    if not spec:
        return []
    table = load_table()
    return [(int(i), str(n)) for i, n in (table.get(spec[1]) or [])]


def parse_query(text: str) -> tuple[int | None, str]:
    """把「小幽查询收藏品+姓名框+关键词」拆成 (kind|None, 关键词)。"""
    raw = str(text or "").strip()
    for w in _CMD_WORDS:
        if raw.startswith("小幽"):
            raw = raw[2:]
        if raw.startswith(w):
            raw = raw[len(w):]
            break
    parts = [p for p in _SEP_RE.split(raw) if p]
    kind = None
    rest: list[str] = []
    for p in parts:
        k = KIND_ALIAS.get(_norm(p))
        if k is not None and kind is None:
            kind = k
        else:
            rest.append(p)
    return kind, " ".join(rest).strip()


def _score(kw: str, kwf: str, iid: int, name: str) -> tuple[int, int]:
    """给一条候选打分；0 = 不命中。返回 (分数, 重合字数)，分数越大越贴近。

    命中规则：关键词里**只要有一个字**出现在名称里就算候选（用户要的宽匹配）。
    排序按贴近程度：ID 精确 > 全名相等 > 前缀 > 全串包含 > 部分字重合（重合越多越靠前）。
    """
    if str(iid) == kw:
        return 1000, len(kwf)            # ID 精确
    n = _fold(_norm(name))
    qs = set(kwf)
    ns = set(n)
    ov = len(qs & ns)
    if not ov:
        return 0, 0                      # 一个字都没重合 → 不算候选
    if n == kwf:
        return 900, ov                   # 名称完全相等
    if n.startswith(kwf):
        return 800 + ov, ov              # 名称以关键词开头
    if kwf in n:
        return 700 + ov, ov              # 名称包含整串关键词
    # 部分字重合：按重合比例给分，1/4 字的命中也不会被丢掉，只是排在后面
    return 300 + int(200 * ov / max(1, len(qs))), ov


def search(kind: int | None, keyword: str, limit: int | None = None) -> tuple[list[tuple[int, int, str]], bool]:
    """返回 ([(kind,id,name)…], 是否精确命中)。

    关键词与名称都先做字形折叠（黒姫 ⇒ 黑姬），所以简繁/日文新字体互通。
    匹配规则：任一字符重合即返回（宽匹配）。limit=None 返回全部候选。
    排序：ID 精确 > 名称相等 > 前缀 > 包含 > 重合字数，再按名称长度短→长。
    """
    kw = _norm(keyword)
    if not kw:
        return [], False
    kwf = _fold(kw)
    kinds = [kind] if kind else list(KINDS)
    scored: list[tuple[int, int, int, int, int, str]] = []
    for k in kinds:
        for iid, name in rows_of(k):
            s, ov = _score(kw, kwf, iid, name)
            if s:
                scored.append((s, ov, len(name), k, iid, name))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2], x[3], x[4]))
    hits = [(k, iid, name) for _, _, _, k, iid, name in scored]
    exact = bool(scored) and scored[0][0] >= 900
    if limit:
        hits = hits[:limit]
    return hits, exact


def asset_url(kind: int, iid: int) -> str | None:
    spec = KINDS.get(kind)
    if not spec or not spec[2]:
        return None
    return ASSET_BASE.format(cat=spec[2], prefix=spec[3], iid=int(iid))


def cached_image(kind: int, iid: int) -> Path | None:
    spec = KINDS.get(kind)
    if not spec or not spec[2]:
        return None
    p = CACHE_DIR / spec[2] / f"{int(iid):06d}.png"
    return p if p.exists() and p.stat().st_size > 0 else None


def _download(url: str, dest: Path, timeout: float = 25.0) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "xiaoyo-bot/item-query"})
        with urllib.request.urlopen(req, timeout=timeout) as rsp:
            blob = rsp.read()
        if len(blob) < 512:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(blob)
        tmp.replace(dest)
        return True
    except Exception as exc:                        # noqa: BLE001
        logger.debug(f"[item_query] 下载失败 {url}：{exc}")
        return False


async def ensure_image(kind: int, iid: int) -> Path | None:
    """有缓存直接返回；没有就下载。上游没有这张 → None（调用方只回文字）。"""
    p = cached_image(kind, iid)
    if p:
        return p
    url = asset_url(kind, iid)
    if not url:
        return None
    spec = KINDS[kind]
    dest = CACHE_DIR / spec[2] / f"{int(iid):06d}.png"
    ok = await asyncio.to_thread(_download, url, dest)
    return dest if ok else None


def kind_name(kind: int) -> str:
    return (KINDS.get(kind) or ("未知",))[0]


def fmt_head(hits: list[tuple[int, int, str]]) -> str:
    """单条命中的标题行：姓名框 · 101 · はじまりのちほー"""
    k, iid, name = hits[0]
    return f"{kind_name(k)} · {iid} · {name}"


_last: dict[int, float] = {}
COOLDOWN = 3.0

cmd_query = on_command("小幽查询收藏品",
                       aliases={"查询收藏品", "查收藏品", "查藏品", "查询藏品"},
                       priority=5, block=True)


@cmd_query.handle()
async def h_query(event: MessageEvent) -> None:
    uid = int(event.user_id)
    now = time.time()
    if now - _last.get(uid, 0.0) < COOLDOWN:
        await cmd_query.finish()
    _last[uid] = now

    kind, kw = parse_query(event.get_plaintext())
    if not kw:
        await cmd_query.finish(
            "🔍 用法：小幽查询收藏品+类型+名称/ID\n"
            "类型：姓名框 / 称号 / 头像 / 旅行伙伴 / 搭档 / 背景（可省略=全搜）\n"
            "例：小幽查询收藏品+姓名框+ちほー\n"
            "　　小幽查询收藏品+头像+450101\n"
            "　　小幽查询收藏品+背景+100101")
    hits, exact = search(kind, kw)
    if not hits:
        tip = f"（类型：{kind_name(kind)}）" if kind else ""
        alt = "\n去掉类型再搜一次？可能记错类型了。" if kind else ""
        await cmd_query.finish(f"🔍 没找到「{kw}」{tip}{alt}\n换个关键词试试，或直接给 ID。")

    total = len(hits)
    if total == 1 or (exact and total <= 5):
        k, iid, name = hits[0]
        others = ""
        if total > 1:
            rest = "、".join(f"{i}({kind_name(kk)})" if not kind else f"{i}" for kk, i, _ in hits[1:6])
            others = f"\n另有 {total - 1} 条相近：{rest}"
            others += "\n要别的回：小幽查询收藏品+ID"
        img = await ensure_image(k, iid)
        if img:
            await cmd_query.finish(Message([
                MessageSegment.text(fmt_head(hits) + others),
                MessageSegment.image(img.read_bytes()),
            ]))
        if KINDS.get(k, ("", "", None))[2]:
            note = "\n（上游资源库没有这张的图，只有 ID 和名字）"
        else:
            note = "\n（这个类型游戏里没有图标，只有名字）"
        await cmd_query.finish(fmt_head(hits) + others + note)

    lines = [f"🔍 「{kw}」命中 {total} 条，选一条回复 ID 看图："]
    for idx, (k, iid, name) in enumerate(hits[:MAX_LIST], 1):
        lines.append(f"{idx}. {kind_name(k)} · {iid} · {name}")
    if total > MAX_LIST:
        lines.append(f"…还有 {total - MAX_LIST} 条，加个字缩小范围或直接给 ID")
    lines.append("例：小幽查询收藏品+" + str(hits[0][1]))
    await cmd_query.finish("\n".join(lines))
