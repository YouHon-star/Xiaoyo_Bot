"""小幽PC50 —— 按「游玩次数（PC）」排的前 50 首，旧曲 35 + 新曲 15（划分同 b50）。

为什么单独做数据通道：
    playCount 只存在于游戏服务端返回的成绩明细里。
    水鱼 `dev/player/records`、落雪 `v0/.../scores` 的返回里都**没有** PC 字段
    （已实测：水鱼 1161 条成绩单条键里没有任何 play/pc 字样）→ 想显示 PC 只能读游戏数据。

用法：
    · 小幽PC50            → 用上次读档的缓存出图（不登录、不消耗冷却）
    · 小幽PC50 <扫码串>    → 拉取一次账号数据（约 1 分钟）→ 出图 + 刷新缓存
      扫码串 10 分钟内有效，一次性

权限：全体可用（和 b50 同级），只拦黑名单（deny）。
      不要求「改数据同意」——全程只读，不写账号任何字段。
缓存：storage/pc50/<qq>.json
"""
import asyncio
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

from xiaoyo_bot.perms import is_blacklisted

PY = "/root/mai_research/.venv/bin/python"
ENTRY = "/root/mai_research/mai_bot.py"
WORKDIR = "/root/mai_research"
BASE_DIR = Path("/home/xiaoyo/Xiaoyo_Bot")
CACHE_DIR = BASE_DIR / "storage/pc50"

N_OLD = 35      # 旧曲（非当前版本新曲）
N_NEW = 15      # 新曲
FETCH_TIMEOUT = 300

_QR_RE = re.compile(r"(SGWCMAID[0-9A-Za-z]+)|(https?://\S+)")


# ====================== 纯逻辑（可离线回归）======================
def extract_qr(text: str) -> str | None:
    """从命令参数里抠出二维码：SGWCMAID… 或公众号链接。"""
    m = _QR_RE.search(text or "")
    return m.group(0) if m else None


def rate_of(achievement: float) -> str:
    """达成率 → 评级字母（与游戏内一致）。"""
    if achievement >= 100.5:
        return "sssp"
    if achievement >= 100.0:
        return "sss"
    if achievement >= 99.5:
        return "ssp"
    if achievement >= 99.0:
        return "ss"
    if achievement >= 98.0:
        return "sp"
    if achievement >= 97.0:
        return "s"
    if achievement >= 94.0:
        return "aaa"
    if achievement >= 90.0:
        return "aa"
    if achievement >= 80.0:
        return "a"
    if achievement >= 75.0:
        return "bbb"
    if achievement >= 70.0:
        return "bb"
    if achievement >= 60.0:
        return "b"
    if achievement >= 50.0:
        return "c"
    return "d"


def rank_key(item) -> tuple:
    """排序键：先 PC，再达成率（同 PC 时更硬的在前）。"""
    return (int(item.rating), float(item.achievements or 0))


def split_pc50(details, song_of, make_result, n_old: int = N_OLD, n_new: int = N_NEW):
    """把读数拆成（旧曲 Top35, 新曲 Top15）。

    Params:
        `details`: mai_pc50.py 落盘的 details 列表
        `song_of`: `musicId -> Song | None`
        `make_result`: `(detail, song, level_index) -> PlayedResult`
    Returns:
        `tuple[list, list]`: (旧曲, 新曲)，各自按 PC 降序
    """
    old: list = []
    new: list = []
    for d in details or []:
        try:
            mid = int(d.get("musicId") or 0)
            lv = int(d.get("level") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0 or mid >= 100000:          # 宴会场不参与
            continue
        if int(d.get("playCount") or 0) <= 0:  # 没打过
            continue
        song = song_of(mid)
        if song is None or not getattr(song, "difficulties", None):
            continue
        if lv < 0 or lv >= len(song.difficulties):
            continue
        item = make_result(d, song, lv)
        (new if getattr(song, "isnew", False) else old).append(item)
    old.sort(key=rank_key, reverse=True)
    new.sort(key=rank_key, reverse=True)
    return old[:n_old], new[:n_new]


# ====================== 缓存 ======================
def cache_path(uid: int) -> Path:
    return CACHE_DIR / f"{int(uid)}.json"


def load_cache(uid: int) -> dict | None:
    try:
        return json.loads(cache_path(uid).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_cache(uid: int, data: dict) -> Path:
    p = cache_path(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# ====================== 拉取（只读通道）======================
_fetch_lock = asyncio.Lock()


def _run_fetch(uid: int, qr: str) -> tuple[bool, str, str]:
    """同步跑 mai_bot.py --mode pc50；返回 (ok, 输出, 落盘路径)。"""
    out_json = str(cache_path(uid))
    argv = [PY, ENTRY, "--mode", "pc50", "--qr", qr, "--out", out_json]
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=FETCH_TIMEOUT, cwd=WORKDIR)
    except subprocess.TimeoutExpired:
        return False, f"超时（>{FETCH_TIMEOUT}s）", out_json
    out = (p.stdout or "") + (p.stderr or "")
    verdict = next((ln for ln in out.splitlines() if ln.startswith("VERDICT=")), "")
    return verdict.startswith("VERDICT=OK"), out, out_json


# ====================== 绘图 ======================
async def render_pc50(uid: int, data: dict) -> MessageSegment:
    """用缓存数据画 PC50 图（复用 b50 底图/排版）。"""
    from nonebot_plugin_maimaidx.core.database.qq import User
    from nonebot_plugin_maimaidx.core.image.pc50 import PlayerPC50
    from nonebot_plugin_maimaidx.core.merge.models import (
        Best50,
        PlayedResult,
        Player,
        ServiceName,
        Theme,
    )
    from nonebot_plugin_maimaidx.core.service import mai

    def song_of(mid: int):
        return mai.total_list.by_id(mid)

    def make_result(d: dict, song, lv: int):
        diff = song.difficulties[lv]
        ach = int(d.get("achievement") or 0) / 10000.0
        return PlayedResult(
            song_id=int(d["musicId"]),
            song_name=song.song_name,
            level_index=lv,
            type=str(getattr(song, "type", "SD")),
            level_value=float(getattr(diff, "level_value", 0.0) or 0.0),
            rating=int(d.get("playCount") or 0),      # ★ 图上右侧数字 = PC
            achievements=ach,
            rate=rate_of(ach),
            level=str(getattr(diff, "level", "") or ""),
            dx_score=int(d.get("deluxscoreMax") or 0),
        )

    sd, dx = split_pc50(data.get("details") or [], song_of, make_result)
    if not sd and not dx:
        raise ValueError("缓存里没有可画的成绩（都是 0 次？）")

    best50 = Best50(
        sd_total=sum(x.rating for x in sd),
        dx_total=sum(x.rating for x in dx),
        sd=sd,
        dx=dx,
    )
    player = Player(
        name=str(data.get("userName") or "无名"),
        rating=int(data.get("rating") or 0),
        course_rank=int(data.get("courseRank") or 0),
        class_rank=int(data.get("classRank") or 0),
    )
    theme = Theme.PRISM_PLUS
    try:
        from nonebot_plugin_maimaidx.core.database.qq import get_user
        theme = (await get_user(int(uid))).theme
    except Exception:  # noqa: BLE001  没绑定/查库失败就用默认主题
        pass
    user = User(qqid=int(uid), service=ServiceName.DIVINGFISH, theme=theme)

    pic = PlayerPC50(user, player=player, best50=best50)
    loop = asyncio.get_running_loop()
    b64 = await loop.run_in_executor(None, lambda: asyncio.run(pic.draw()))

    when = datetime.fromtimestamp(int(data.get("ts") or 0)).strftime("%m-%d %H:%M")
    caption = (
        f"\n📀 PC50（按游玩次数）· {player.name} · 读取于 {when}\n"
        f"旧曲 PC35 合计 {best50.sd_total} 次 ｜ 新曲 PC15 合计 {best50.dx_total} 次"
    )
    return MessageSegment.image(b64) + MessageSegment.text(caption)


# ====================== 命令 ======================
pc50_cmd = on_command(
    "小幽PC50",
    aliases={"PC50", "pc50", "Pc50", "查PC", "查pc", "小幽查PC"},
    priority=5,
    block=True,
)


def _gate(event: MessageEvent) -> str:
    """PC50 是普通查询（和 b50 同级）：不再要求高级档，只拦 deny 黑名单。

    历史：原先用 eff_tier(...) != "tech" 按科技功能管控，导致 tech 名单外的人
    在已授权群里也拿不到；用户 2026-10-28 明确要求改成「大家都能用」。
    黑名单两份都查（mai_modify.json.deny + permissions.json.black_list）。
    """
    if is_blacklisted(int(event.get_user_id())):
        return "抱歉您没有权限"
    return ""


@pc50_cmd.handle()
async def h_pc50(bot: Bot, event: MessageEvent, message: Message = CommandArg()) -> None:
    err = _gate(event)
    if err:
        await pc50_cmd.finish(err, reply_message=True)
        return

    uid = int(event.get_user_id())
    arg = message.extract_plain_text().strip()
    qr = extract_qr(arg)

    if qr:
        await pc50_cmd.send("⏳ 正在登录读档（只读不改，约 1 分钟）…", reply_message=True)
        async with _fetch_lock:
            ok, out, path = await asyncio.to_thread(_run_fetch, uid, qr)
        if not ok:
            tail = "\n".join(out.strip().splitlines()[-6:])
            await pc50_cmd.finish(f"❌ 读档失败：\n{tail}", reply_message=True)
            return
        data = load_cache(uid)
        if not data:
            await pc50_cmd.finish("❌ 读档成功但缓存读不出来，请重试", reply_message=True)
            return
    else:
        data = load_cache(uid)
        if not data:
            await pc50_cmd.finish(
                "📀 PC50 = 按游玩次数排的前 50 首（旧曲 35 + 新曲 15）\n"
                "PC 只存在于游戏本体数据，需要一次性读档：\n"
                "发「小幽PC50 + 扫码串」\n"
                "（游戏的登录扫码串，10 分钟内有效）",
                reply_message=True,
            )
            return

    try:
        seg = await render_pc50(uid, data)
    except Exception as e:  # noqa: BLE001
        await pc50_cmd.finish(f"❌ 出图失败：{e!r}", reply_message=True)
        return
    await pc50_cmd.send(seg, reply_message=True)
