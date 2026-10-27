"""舞萌知识注入（llmchat 用）：把本地曲库的事实塞进 system prompt，杜绝 AI 瞎编定数。

为什么不用向量检索 / MCP 工具：
  · 曲库本来就是结构化的（1404 条、定数/物量/谱师齐全），关键词命中后直接查表即可；
  · GLM-4-Flash 的 function calling 很弱，工具该调不调；检索注入不依赖模型能力。

接线方式（在 pip 安装的插件里加了 6 行）：
  `nonebot_plugin_llmchat/__init__.py` 的 `process_messages()` 里
      default_prompt = (state.group_prompt) or plugin_config.default_prompt
  +   try:
  +       from xiaoyo_bot.mai_kb import inject_for_event as _kb_inject
  +       default_prompt = _kb_inject(default_prompt, event)
  +   except Exception:
  +       pass
  ⚠️ pip 升级会覆盖，升级后需重新打这几行；本模块独立，缺了只是「不注入」。

数据源：
  storage/maimai/data/merge_music_data.json  曲目 + 四难度定数/物量/谱师
  storage/maimai/data/music_alias.json       别名（含玩家起的黑话）
  首次启动生成 storage/mai_kb_cache.json，源文件 mtime/size 变化时自动重建。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from nonebot.log import logger

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "storage" / "maimai" / "data"
CACHE = ROOT / "storage" / "mai_kb_cache.json"
SRC = [DATA / "merge_music_data.json", DATA / "music_alias.json", DATA / "local_music_alias.json"]

MAX_FACTS = 700          # 注入上限，防止把 system prompt 撑爆
MAX_SONGS = 2            # 一条消息最多注入两首
DIFF_ABBR = ["BAS", "ADV", "EXP", "MAS"]

# 命中这些词但没有具体曲名 → 注入机制卡
MECH_KEYS = (
    "定数", "谱面", "物量", "notes", "谱师", "rating", "rt", "锐评", "推分",
    "上分", "b50", "牌子", "段位", "舞神", "ap", "fc", "sss", "全连",
    "满分", "达成率", "等级", "难度", "打歌",
)
CHARTER_HINT = ("谱师", "谁写", "谁做", "写的", "做谱", "作者")

MECH_CARD = """【舞萌机制 · 只用这些数字】
- 难度：BASIC(绿)/ADVANCED(黄)/EXPERT(红)/MASTER(紫)，定数 ds 取 1~15，带小数（如 13.7）
- 星级：按达成率算，85%/90%/93%/95%/97% 分别是 1~5 星
- 评级：S 97%、SS 98%、SSS 100%、SSS+ 100.5%
- DX 分 = 3×CRITICAL PERFECT + 2×PERFECT + GREAT，BREAK 最多额外 +1%
- Rating：取 b50（35 首旧版本曲 + 15 首当前版本曲），单曲由定数×达成率决定
"""

_index: dict[str, dict] = {}
_names: dict[str, list[str]] = {}
_loaded = False


def _norm(s: str) -> str:
    """归一化：小写、去空白标点、全角转半角，便于子串匹配。"""
    out = []
    for ch in s.lower():
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            ch = chr(o - 0xFEE0)
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            out.append(ch)
    return "".join(out)


def _is_ascii(k: str) -> bool:
    return all(ord(c) < 128 for c in k)


def _norm_map(s: str) -> tuple[str, list[int]]:
    """归一化并保留「归一化后第 i 个字符」对应的原文下标，供词边界校验用。"""
    out, idx = [], []
    for pos, ch in enumerate(s.lower()):
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            ch = chr(o - 0xFEE0)
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            out.append(ch)
            idx.append(pos)
    return "".join(out), idx


def _ascii_hit(name: str, norm: str, raw_low: str, omap: list[int]) -> int:
    """英文/数字别名按词边界匹配，返回在 norm 中的下标（-1 = 未命中）。

    避免 "xxx" 命中 "PARADOXXX"、"2000" 命中 "12000"。
    """
    L = len(name)
    start = 0
    while True:
        i = norm.find(name, start)
        if i < 0:
            return -1
        left = raw_low[omap[i] - 1] if omap[i] > 0 else ""
        j = omap[i + L - 1] + 1
        right = raw_low[j] if j < len(raw_low) else ""
        if not (left.isascii() and left.isalnum()) and not (right.isascii() and right.isalnum()):
            return i
        start = i + 1


def _build() -> tuple[dict, dict]:
    songs: dict[str, dict] = {}
    names: dict[str, list[str]] = {}

    for s in json.loads(SRC[0].read_text(encoding="utf-8")):
        sid = str(s["song_id"])
        diffs = []
        for d in s.get("difficulties") or []:
            notes = d.get("notes") or {}
            diffs.append([d.get("level", "?"), d.get("level_value", 0),
                          notes.get("total", 0), d.get("note_designer") or "-"])
        if not diffs:
            continue
        entry = songs.setdefault(sid, {
            "name": s.get("song_name", "?"),
            "artist": s.get("artist") or "-",
            "bpm": round(float(s.get("bpm") or 0)),
            "genre": s.get("genre") or "-",
            "ver": s.get("version_str") or "-",
            "charts": {},
        })
        entry["charts"][s.get("type") or "?"] = diffs
        nk = _norm(entry["name"])
        names.setdefault(nk, [])
        if sid not in names[nk]:
            names[nk].append(sid)

    for a in json.loads(SRC[1].read_text(encoding="utf-8")):
        sid = str(a.get("song_id"))
        if sid not in songs:
            continue
        entry = songs[sid]
        for al in [a.get("name")] + list(a.get("alias") or []):
            k = _norm(al or "")
            # 纯 ASCII 别名至少 3 字，否则「2000」这种会命中「12000」
            if len(k) < (3 if _is_ascii(k) else 2):
                continue
            names.setdefault(k, [])
            if sid not in names[k]:
                names[k].append(sid)

    # 玩家手工补的别名（{song_id: [别名...]}），柚子源里没有的缺词写这里
    try:
        local = json.loads(SRC[2].read_text(encoding="utf-8"))
    except Exception:
        local = {}
    for sid, als in local.items():
        sid = str(sid)
        if sid not in songs or not isinstance(als, list):
            continue
        for al in als:
            k = _norm(str(al))
            if len(k) < (3 if _is_ascii(k) else 2):
                continue
            names.setdefault(k, [])
            if sid not in names[k]:
                names[k].append(sid)

    return songs, names


def _load() -> None:
    global _index, _names, _loaded
    if _loaded:
        return
    stamp = {p.name: [p.stat().st_mtime, p.stat().st_size] for p in SRC if p.exists()}
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached.get("stamp") == stamp:
            _index, _names = cached["songs"], cached["names"]
            _loaded = True
            logger.info(f"[mai_kb] 缓存命中：{len(_index)} 首 / {len(_names)} 个名字")
            return
    except Exception:
        pass
    t0 = time.time()
    _index, _names = _build()
    _loaded = True
    logger.info(f"[mai_kb] 曲库索引完成：{len(_index)} 首 / {len(_names)} 个名字，耗时 {time.time()-t0:.1f}s")
    try:
        CACHE.write_text(json.dumps({"stamp": stamp, "songs": _index, "names": _names},
                                    ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[mai_kb] 缓存写入失败：{e}")


def _fmt_song(sid: str, with_charter: bool) -> str:
    s = _index[sid]
    lines = [f"{s['name']} / {s['artist']} / BPM {s['bpm']} / {s['genre']} / {s['ver']}"]
    for ctype in ("SD", "DX"):
        diffs = s["charts"].get(ctype)
        if not diffs:
            continue
        cells = []
        for i, (lv, ds, total, charter) in enumerate(diffs[:4]):
            cell = f"{DIFF_ABBR[i]} {lv}({ds}) 物量{total}"
            if with_charter and charter != "-":
                cell += f" 谱师{charter}"
            cells.append(cell)
        lines.append(f"[{ctype}] " + " · ".join(cells))
    return "\n".join(lines)


def build(text: str) -> str:
    """给一段消息文本，返回要注入的资料块（无命中返回空串）。"""
    _load()
    if not text:
        return ""
    raw = re.sub(r"\[CQ:[^\]]*\]", " ", text)
    raw = re.sub(r"@\S+\s*", " ", raw).strip()
    low = raw.lower()
    norm, omap = _norm_map(raw)
    if not norm:
        return ""

    hits: list[tuple[int, int, int, str]] = []   # (长度, 是否曲名命中, 命中位置, sid)
    for name, sids in _names.items():
        if len(name) < (3 if _is_ascii(name) else 2):
            continue
        if _is_ascii(name):
            # 英文/数字别名按词边界匹配，避免 "xxx" 命中 "PARADOXXX"、"2000" 命中 "12000"
            pos = _ascii_hit(name, norm, low, omap)
            if pos < 0:
                continue
        else:
            pos = norm.find(name)
            if pos < 0:
                continue
        is_title = _norm(_index[sids[0]]["name"]) == name
        for sid in sids:
            hits.append((len(name), 1 if is_title else 0, pos, sid))

    # 丢掉被更长命中完整包裹的短命中（如「水晶肛塞」已命中，就不要再单独命中「肛塞」）
    def _contained(h):
        L, _, p, _ = h
        return any(o[0] > L and o[2] <= p and p + L <= o[2] + o[0] for o in hits)

    if hits:
        hits = [h for h in hits if not _contained(h)] or hits

    picked: list[str] = []
    if hits:
        hits.sort(key=lambda h: (h[1], h[0]), reverse=True)
        seen_names: set[str] = set()
        for _, _, _, sid in hits:
            nm = _index[sid]["name"]
            if nm in seen_names:
                continue
            seen_names.add(nm)
            picked.append(sid)
            if len(picked) >= MAX_SONGS:
                break

    parts = []
    if picked:
        parts.append("【曲目资料 · 只用这里给出的数字，没写的就说不知道】")
        with_charter = any(h in raw for h in CHARTER_HINT)
        parts.extend(_fmt_song(sid, with_charter) for sid in picked)
    elif any(k in low for k in MECH_KEYS):
        parts.append(MECH_CARD.strip())

    block = "\n".join(parts).strip()
    return block[:MAX_FACTS] + "…" if len(block) > MAX_FACTS else block


def inject_for_event(base_prompt: str, event) -> str:
    """从 OneBot 事件取文本 → 命中则把资料块追加到 system prompt 末尾。"""
    try:
        extra = build(event.get_plaintext() or "")
        if not extra:
            return base_prompt
        logger.debug(f"[mai_kb] 注入 {len(extra)} 字")
        return f"{base_prompt}\n\n{extra}"
    except Exception as e:  # 任何异常都不许影响正常对话
        logger.warning(f"[mai_kb] 注入失败，忽略：{e}")
        return base_prompt
