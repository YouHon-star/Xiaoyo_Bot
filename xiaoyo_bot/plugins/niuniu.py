"""牛牛大作战 —— 群聊娱乐插件

玩法说明：
  开启牛牛大作战      在群内开启（需要 bot 管理员及以上）
  关闭牛牛大作战      在群内关闭（需要 bot 管理员及以上）

  ccb @某人 / 日群友 @某人
      双方牛牛都会变化：
        70% 成功 → 发送者牛牛 +，被 @ 的群友牛牛 −
        30% 失败（被反攻）→ 发送者牛牛 −，被 @ 的群友牛牛 +

  卢关 / 鹿管 / 炉管 / 禄冠 / 卢管 / 录关 / 录管 / 鹿 / 🦌
      直接给自己加长度。每人每天前 2 次「必增长」；第 3 次起只会「减短」；
      当天超过 20 次会直接「归零」，并变成「无机物·无性人」，
      该状态 8 小时内 ccb 对其无效（双方都不变化），到期自动解除。

  查看排行            查看本群参与过的群友长度排行
  查看牛牛            查看自己的长度与今日剩余必涨次数

说明：每个人的初始长度为 0，长度可以为负数；随机数值带小数，较激进。
"""

import json
import random
import time
from datetime import date
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="牛牛大作战",
    description="群聊娱乐：牛牛长度大作战",
    usage=(
        "开启牛牛大作战 / 关闭牛牛大作战 | "
        "ccb @群友 或 日群友 @群友 | "
        "卢关 / 鹿管 / 炉管 / 禄冠 / 卢管 / 录关 / 录管 / 鹿 / 🦌 | 查看排行 | 查看牛牛"
    ),
)

# ====================== 配置 ======================
DATA_FILE = Path("storage/niuniu_data.json")
PERM_FILE = Path("storage/permissions.json")
OWNER_QQ = 1923497173  # 与 owner_control.py 保持一致
DAILY_GROW = 2  # 卢关类每天前 N 次必增长
DAILY_MAX = 20  # 卢关类每天最多使用次数，超出后直接归零
ASEXUAL_HOURS = 8  # 归零后「无机物·无性人」状态持续小时数
SUCCESS_RATE = 0.7  # ccb 成功率
AMOUNT_MIN = 0.1  # 随机幅度下限（可加大）
AMOUNT_MAX = 30.0  # 随机幅度上限（可加大）

NOT_ENABLED_MSG = "游玩牛牛大作战请联系无敌牛逼至上幽鸿大人开启喵"

# 卢关文案池（每档随机取一句；牛牛数值单独成行，不与文案同排）
LULU_GROW_LINES = [
    "🍌 一撸冲天！",
    "🍌 撸起袖子就是干，势如破竹！",
    "🍌 气冲霄汉，一柱擎天！",
    "🍌 扶摇直上九万里！",
    "🍌 这波稳赚，牛气冲天！",
]
LULU_SHRINK_LINES = [
    "💀 强撸之末，不能穿鲁缟！",
    "💀 再撸而衰，三撸而竭！",
    "💀 一鼓作气，再而衰！",
    "💀 用力过猛，反受其累！",
    "💀 撸到深处自然虚…",
]
LULU_RESET_LINES = [
    f"🧟 强撸灰飞烟灭！{DAILY_MAX} 次大限已破",
    f"🧟 樯橹灰飞烟灭！{DAILY_MAX} 次大限已到",
    f"🧟 撸到天地俱焚，{DAILY_MAX} 次大限已破",
]
LULU_RESET_FLAVOR = [
    "从此六根清净，遁入空门",
    "一朝归零，遁入空门",
    "你已是无机物·无性人，红尘与你再无瓜葛",
]


# ====================== 数据层 ======================
def _load() -> dict:
    """读取数据文件"""
    if not DATA_FILE.exists():
        return {"groups": {}}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"groups": {}}


def _save(data: dict) -> None:
    """写入数据文件"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _group(data: dict, gid: int) -> dict:
    """取（或创建）某群的数据结构"""
    groups = data.setdefault("groups", {})
    g = groups.setdefault(str(gid), {"enabled": False, "users": {}, "daily": {}})
    g.setdefault("enabled", False)
    g.setdefault("users", {})
    g.setdefault("daily", {})
    return g


def is_enabled(gid: int) -> bool:
    """该群是否已开启牛牛大作战"""
    return bool(_load().get("groups", {}).get(str(gid), {}).get("enabled", False))


def _bump(g: dict, uid: int, delta: float, name: str = "") -> float:
    """修改某人的长度并返回新值（可为负）"""
    users = g["users"]
    u = users.setdefault(str(uid), {"name": "", "len": 0.0})
    if name:
        u["name"] = name
    u["len"] = round(float(u.get("len", 0.0)) + delta, 1)
    return u["len"]


def _len_of(g: dict, uid: int) -> float:
    """取某人当前长度（未记录视为 0）"""
    return float(g["users"].get(str(uid), {}).get("len", 0.0))


def _today_used(g: dict, uid: int) -> int:
    """今日已用卢关次数（跨天自动归零）"""
    rec = g["daily"].get(str(uid))
    if not rec or rec.get("date") != date.today().isoformat():
        return 0
    return int(rec.get("count", 0))


def _inc_used(g: dict, uid: int) -> int:
    """今日卢关次数 +1，返回新次数"""
    today = date.today().isoformat()
    rec = g["daily"].get(str(uid))
    if not rec or rec.get("date") != today:
        rec = {"date": today, "count": 0}
        g["daily"][str(uid)] = rec
    rec["count"] = int(rec.get("count", 0)) + 1
    return rec["count"]


# ====================== 无性人状态 ======================
def _is_asexual(info: dict) -> bool:
    """某人是否处于「无机物·无性人」状态（未到期）"""
    return float(info.get("asexual_until", 0) or 0) > time.time()


def _set_asexual(g: dict, uid: int) -> None:
    """将某人置为无性人，ASEXUAL_HOURS 小时后自动解除"""
    u = g["users"].setdefault(str(uid), {"name": "", "len": 0.0})
    u["asexual_until"] = time.time() + ASEXUAL_HOURS * 3600


def _asexual_left(info: dict) -> str:
    """无性人状态剩余时间的人类可读描述"""
    secs = int(float(info.get("asexual_until", 0) or 0) - time.time())
    if secs <= 0:
        return "已解除"
    hours, rem = divmod(secs, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"约 {hours} 小时 {minutes} 分"
    if hours:
        return f"约 {hours} 小时"
    return f"约 {max(minutes, 1)} 分钟"


# ====================== 核心逻辑（纯函数，便于测试） ======================
def _apply_ccb(
    g: dict,
    sender: int,
    target: int,
    sender_name: str,
    *,
    success: bool,
    target_name: str = "",
) -> dict:
    """结算一次 ccb，返回 {'mode', 's_old', 's_new', 't_old', 't_new'}

    成功：发送者 +，目标 − ；失败（被反攻）：发送者 −，目标 +。
    """
    s_old = _len_of(g, sender)
    t_old = _len_of(g, target)
    if success:
        gain = _rand_amount()
        loss = _rand_amount()
        s_new = _bump(g, sender, gain, sender_name)
        t_new = _bump(g, target, -loss, target_name)
        mode = "success"
    else:
        loss = _rand_amount()
        gain = _rand_amount()
        s_new = _bump(g, sender, -loss, sender_name)
        t_new = _bump(g, target, gain, target_name)
        mode = "fail"
    return {
        "mode": mode,
        "s_old": s_old,
        "s_new": s_new,
        "t_old": t_old,
        "t_new": t_new,
    }


def _apply_lulu(g: dict, uid: int, name: str) -> dict:
    """结算一次卢关，返回 {'mode', 'old', 'new', 'used'}

    当天前 DAILY_GROW 次必增长，之后只减；
    当天累计超过 DAILY_MAX 次后，直接清零。
    """
    old = _len_of(g, uid)
    used = _today_used(g, uid)
    if used >= DAILY_MAX:
        new = _bump(g, uid, -old, name)  # 超上限：直接归零
        used = _inc_used(g, uid)
        _set_asexual(g, uid)  # 变成无机物·无性人
        return {"mode": "reset", "old": old, "new": new, "used": used}
    if used < DAILY_GROW:
        amt = _rand_amount()
        new = _bump(g, uid, amt, name)
        used = _inc_used(g, uid)
        return {"mode": "grow", "old": old, "new": new, "used": used}
    amt = _rand_amount()
    new = _bump(g, uid, -amt, name)
    used = _inc_used(g, uid)
    return {"mode": "shrink", "old": old, "new": new, "used": used}


# ====================== 工具函数 ======================
def is_bot_admin(user_id: int) -> bool:
    """bot 主人或管理员"""
    if user_id == OWNER_QQ:
        return True
    try:
        perms = json.loads(PERM_FILE.read_text(encoding="utf-8"))
        return user_id in perms.get("admin_list", [])
    except (OSError, json.JSONDecodeError):
        return False


def _rand_amount() -> float:
    """随机幅度，带小数，较激进"""
    return round(random.uniform(AMOUNT_MIN, AMOUNT_MAX), 1)


def _fmt(x: float) -> str:
    """数值展示：整数不带小数，否则保留 1 位"""
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:.1f}"


def _extract_target(event: GroupMessageEvent) -> int | None:
    """从消息里取第一个 @ 的群友（排除全体/机器人自己）"""
    for seg in event.get_message():
        if seg.type == "at":
            qq = str(seg.data.get("qq", ""))
            if qq.isdigit() and int(qq) != event.self_id:
                return int(qq)
    return None


def _sender_name(event: GroupMessageEvent) -> str:
    """发送者群名片或昵称"""
    try:
        return event.sender.card or event.sender.nickname or str(event.user_id)
    except AttributeError:
        return str(event.user_id)


async def _fetch_name(bot: Bot, gid: int, uid: int) -> str:
    """获取群成员的群名片/昵称（失败返回空串）"""
    try:
        info = await bot.get_group_member_info(group_id=gid, user_id=uid)
    except Exception:  # noqa: BLE001 - 取不到就算了
        return ""
    if isinstance(info, dict):
        return str(info.get("card") or info.get("nickname") or "")
    return str(getattr(info, "card", "") or getattr(info, "nickname", "") or "")


def _apply_membership(
    users: dict, member_map: dict[int, str] | None
) -> tuple[dict, bool]:
    """按群成员表过滤「已退群」的人，并回填缺失昵称。

    `member_map` 为 None（拿不到成员表）时不过滤、不回填。
    Returns: (可见用户字典, 是否有变更)
    """
    if member_map is None:
        return dict(users), False
    visible: dict[str, dict] = {}
    changed = False
    for uid, info in users.items():
        u = int(uid)
        if u not in member_map:
            continue  # 已退群，不显示
        if not info.get("name") and member_map[u]:
            info["name"] = member_map[u]
            changed = True
        visible[uid] = info
    return visible, changed


def _at(qq: int) -> MessageSegment:
    return MessageSegment.at(qq)


# ====================== 指令注册 ======================
ccb = on_command("ccb", aliases={"日群友"}, priority=5, block=True)
lulu = on_command(
    "卢关",
    aliases={"鹿管", "炉管", "禄冠", "卢管", "录关", "录管", "鹿", "🦌"},
    priority=5,
    block=True,
)
ranking = on_command("查看排行", aliases={"牛牛排行"}, priority=5, block=True)
mystat = on_command("查看牛牛", aliases={"我的牛牛"}, priority=5, block=True)
enable_cmd = on_command("开启牛牛大作战", priority=5, block=True)
disable_cmd = on_command("关闭牛牛大作战", priority=5, block=True)


# ====================== ccb / 日群友 ======================
@ccb.handle()
async def _handle_ccb(bot: Bot, event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await ccb.finish("请在群里玩牛牛大作战喵～")

    gid: int = event.group_id
    if not is_enabled(gid):
        await ccb.finish(NOT_ENABLED_MSG)

    target = _extract_target(event)
    if target is None:
        await ccb.finish("请 @ 一位群友再发送喵～\n例：ccb @某人")
    assert target is not None
    sender = event.user_id
    if target == sender:
        await ccb.finish("不要对自己下手啦，@ 别的群友喵～")

    data = _load()
    g = _group(data, gid)

    # 无机物·无性人：作为发起者或被 @ 者，ccb 都无效（双方均无变化）
    s_info = g["users"].get(str(sender), {})
    t_info = g["users"].get(str(target), {})
    if _is_asexual(s_info):
        await ccb.finish(
            f"🚫 你已沦为无机物·无性人，{_asexual_left(s_info)}后恢复，"
            f"现在 ccb 无效"
        )
    if _is_asexual(t_info):
        await ccb.finish(
            f"🚫 对方已沦为无机物·无性人，ccb 无效，双方均无变化"
            f"\n（{_asexual_left(t_info)}后恢复）"
        )

    res = _apply_ccb(
        g,
        sender,
        target,
        _sender_name(event),
        success=random.random() < SUCCESS_RATE,
        target_name=await _fetch_name(bot, gid, target),
    )

    if res["mode"] == "success":
        head = MessageSegment.text("🥵 得手了！你日翻了\n")
        tail = MessageSegment.text(
            f"\n你的牛牛 {_fmt(res['s_old'])} → {_fmt(res['s_new'])}"
            f"\n对方牛牛 {_fmt(res['t_old'])} → {_fmt(res['t_new'])}"
        )
    else:
        head = MessageSegment.text("😭 被反攻了！你被\n")
        tail = MessageSegment.text(
            f"\n反杀"
            f"\n你的牛牛 {_fmt(res['s_old'])} → {_fmt(res['s_new'])}"
            f"\n对方牛牛 {_fmt(res['t_old'])} → {_fmt(res['t_new'])}"
        )

    _save(data)
    await ccb.finish(Message([head, _at(target), tail]))


# ====================== 卢关 / 鹿管 / 炉管 ======================
@lulu.handle()
async def _handle_lulu(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await lulu.finish("请在群里玩牛牛大作战喵～")

    gid: int = event.group_id
    if not is_enabled(gid):
        await lulu.finish(NOT_ENABLED_MSG)

    data = _load()
    g = _group(data, gid)
    res = _apply_lulu(g, event.user_id, _sender_name(event))

    old, new = _fmt(res["old"]), _fmt(res["new"])
    if res["mode"] == "grow":
        msg = f"{random.choice(LULU_GROW_LINES)}\n牛牛 {old} → {new}"
    elif res["mode"] == "shrink":
        msg = f"{random.choice(LULU_SHRINK_LINES)}\n牛牛 {old} → {new}"
    else:  # reset
        msg = (
            f"{random.choice(LULU_RESET_LINES)}"
            f"\n牛牛 {old} → {new}"
            f"\n{random.choice(LULU_RESET_FLAVOR)}"
        )

    _save(data)
    await lulu.finish(msg)


# ====================== 查看排行 ======================
@ranking.handle()
async def _handle_rank(bot: Bot, event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await ranking.finish("请在群里查看排行喵～")

    gid: int = event.group_id
    if not is_enabled(gid):
        await ranking.finish(NOT_ENABLED_MSG)

    data = _load()
    g = _group(data, gid)
    users = g["users"]
    if not users:
        await ranking.finish("本群还没有人玩过牛牛大作战喵～")

    # 拉一次群成员表：用于「过滤已退群的人」+「回填缺失昵称」
    member_map: dict[int, str] | None = None
    try:
        members = await bot.get_group_member_list(group_id=gid)
        member_map = {
            int(m["user_id"]): str(m.get("card") or m.get("nickname") or "")
            for m in members
        }
    except Exception:  # noqa: BLE001 - 取不到就不做过滤
        member_map = None

    visible, changed = _apply_membership(users, member_map)
    if changed:
        _save(data)

    if not visible:
        await ranking.finish("本群还没有人玩过牛牛大作战喵～")

    ranked = sorted(
        visible.items(), key=lambda kv: float(kv[1].get("len", 0.0)), reverse=True
    )
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, info) in enumerate(ranked[:20]):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        label = info.get("name") or f"QQ{uid}"
        lines.append(f"{prefix} {label}：{_fmt(info.get('len', 0.0))}")

    tail = "\n（仅显示前 20 名）" if len(ranked) > 20 else ""
    await ranking.finish(
        f"牛牛大作战·本群排行（共 {len(ranked)} 人）\n" + "\n".join(lines) + tail
    )


# ====================== 查看牛牛 ======================
@mystat.handle()
async def _handle_mystat(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await mystat.finish("请在群里查看喵～")

    gid: int = event.group_id
    if not is_enabled(gid):
        await mystat.finish(NOT_ENABLED_MSG)

    g = _group(_load(), gid)
    info = g["users"].get(str(event.user_id), {})
    used = _today_used(g, event.user_id)
    grow_left = max(0, DAILY_GROW - used)
    lines = [
        f"🍆 你的牛牛当前长度：{_fmt(info.get('len', 0.0))}",
        f"今日卢关已用：{used}/{DAILY_MAX} 次",
        f"剩余必涨次数：{grow_left}/{DAILY_GROW}",
    ]
    if _is_asexual(info):
        lines.append(f"🚫 无机物·无性人状态中，{_asexual_left(info)}后自动解除")
    await mystat.finish("\n".join(lines))


# ====================== 开启 ======================
@enable_cmd.handle()
async def _handle_enable(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await enable_cmd.finish("请在群里开启喵～")

    if not is_bot_admin(event.user_id):
        await enable_cmd.finish("❌ 权限不足，需要 bot 管理员及以上才能开启喵")

    data = _load()
    g = _group(data, event.group_id)
    if g["enabled"]:
        await enable_cmd.finish("⚠️ 本群牛牛大作战已经开启啦")
    g["enabled"] = True
    _save(data)
    await enable_cmd.finish(
        "✅ 牛牛大作战已在本群开启！\n发 ccb @群友 或 卢关 一起来玩喵～"
    )


# ====================== 关闭 ======================
@disable_cmd.handle()
async def _handle_disable(event: MessageEvent) -> None:
    if not isinstance(event, GroupMessageEvent):
        await disable_cmd.finish("请在群里关闭喵～")

    if not is_bot_admin(event.user_id):
        await disable_cmd.finish("❌ 权限不足，需要 bot 管理员及以上才能关闭喵")

    data = _load()
    g = _group(data, event.group_id)
    if not g["enabled"]:
        await disable_cmd.finish("⚠️ 本群牛牛大作战还没开启哦")
    g["enabled"] = False
    _save(data)
    await disable_cmd.finish("✅ 牛牛大作战已在本群关闭")
