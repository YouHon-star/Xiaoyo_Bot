"""帮助插件

两套帮助：
· `小幽帮助`      —— 所有人（科技身份决定发科技版还是普通版图）
· `小幽管理帮助`  —— **仅管理员及以上**（主人 + 管理员名单），按角色裁剪内容
· `小幽开发者帮助` —— 兼容旧命令，同样要求管理员及以上，内容见「管理帮助」

管理员帮助的每一条命令都对应真实注册的 matcher / alias，
`t_admin_help.py` 会扫全部插件的 on_command 做交叉校验，防止说明书和代码走散。
"""

import unicodedata
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment

# 普通用户帮助指令
# ⚠ 适配器会先把开头的昵称「小幽」剥掉（_check_nickname），所以实际进 TrieRule 的是「帮助」，
# 必须显式给 帮助 这个别名，否则用户发「小幽帮助」永远匹配不到。
help_cmd = on_command("小幽帮助", aliases={"帮助", "help", "功能"}, priority=3, block=True)

# 开发者功能帮助（旧名，保留兼容；实际等同管理帮助）
dev_help_cmd = on_command(
    "小幽开发者帮助", aliases={"开发者帮助"}, priority=3, block=True
)

# ★ 管理帮助：仅管理员及以上可见
admin_help_cmd = on_command(
    "小幽管理帮助",
    aliases={"管理帮助", "管理员帮助", "管理指令", "adminhelp"},
    priority=3,
    block=True,
)

IMG_DIR = Path("/home/xiaoyo/Xiaoyo_Bot/storage/help_images")
IMG_TECH = IMG_DIR / "help_tech.jpg"
IMG_NORMAL = IMG_DIR / "help_normal.jpg"

# 图片都读不到时的兜底文字
HELP_FALLBACK = (
    "小幽功能简介\n\n"
    "B50（获取成绩图）\n分数列表（指定分数成绩图）\n今日舞萌（出勤运势）\n"
    "xx是什么歌（查询歌曲）\nidxxx（歌曲ID查询）\n猜歌（发送「猜歌帮助」查看玩法）\n"
    "更新b50（同步成绩到查分器）\n\n"
    "群聊娱乐：牛牛大作战（需管理员开启）\n音乐：小幽获取歌曲+歌名\n\n"
    "有问题请联系幽鸿（1923497173）"
)


# ====================== 管理帮助内容 ======================
# (分组名, 最低权限 "admin"|"owner", [(命令, 说明), …])
ADMIN_GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("状态与名单", "admin", [
        ("小幽状态", "运行状态 / 白名单 / 黑名单概览"),
        ("查看管理", "管理员名单"),
        ("查看黑名单", "被拉黑的人"),
        ("查看白名单", "群白名单（为空 = 所有群可用）"),
        ("白名单列表", "科技名单 / 科技群 / 普通白名单 / 黑名单 / 排队"),
        ("科技群当前", "本群号 + 本群是否允许科技"),
    ]),
    ("用户管理", "admin", [
        ("拉黑+QQ", "该用户的全部消息被忽略"),
        ("解除拉黑+QQ", "恢复该用户"),
        ("白名单允许+QQ", "普通白名单：除舞萌科技外的功能（更新b50 等）"),
        ("白名单禁止+QQ", "移出普通白名单"),
    ]),
    ("群管理", "admin", [
        ("添加白名单+群号", "群白名单：只允许名单内的群使用（空=不限）"),
        ("删除白名单+群号", "从群白名单移除"),
        ("科技群允许+群号", "允许该群用舞萌科技（仅主人；不带群号=当前群）"),
        ("科技群禁止+群号", "禁止该群的科技功能（仅主人）"),
    ]),
    ("舞萌科技权限", "admin", [
        ("科技白名单允许+QQ", "开科技：改里程/改成绩/发票/发藏品/解禁歌曲/新建成绩"),
        ("科技白名单禁止+QQ", "关科技"),
        ("小幽改数据帮助", "科技功能的用法与参数（需科技身份）"),
    ]),
    ("群内玩法", "admin", [
        ("开启牛牛大作战", "本群开启（在目标群里发）"),
        ("关闭牛牛大作战", "本群关闭"),
        ("小幽添加机厅+名称+机台数", "例：小幽添加机厅+环游嘉年华+12"),
        ("小幽删除机厅+名称", "删除机厅"),
        ("小幽机厅列表", "机厅与在玩人数"),
    ]),
    ("主人专属", "owner", [
        ("重启bot", "重启进程，起来后回本会话报平安（同：重启）"),
        ("开关bot", "全局暂停/恢复非管理员功能"),
        ("添加管理员+QQ", "授予管理员（管理命令 + 默认科技权限）"),
        ("移除管理员+QQ", "收回管理员"),
        ("小幽我是谁", "认主验证"),
    ]),
]

ADMIN_NOTES = [
    "管理员 = 主人 + 管理员名单，两者都能用「管理员」档的命令",
    "标「仅主人」的命令，管理员发会回「权限不足」",
    "科技 = 人（科技白名单）∩ 群（科技群），缺一即无效",
    "管理员默认有科技权限，但仍受群维度限制",
    "名单/开关类命令立即生效，无二次确认；改错再改回来即可",
    "QQ 号 5~12 位；部分群命令不带号时默认操作当前群",
]


def _w(s: str) -> int:
    """显示宽度（CJK 全角算 2），用于对齐命令列。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def build_admin_help(is_owner: bool) -> str:
    """按查看者身份生成管理帮助文本（纯函数，离线可测）。

    `is_owner=False` 时整段「主人专属」不出现 —— 不是打码，是根本不拼接。
    """
    lines = [
        "🔐 小幽管理帮助",
        "（仅管理员及以上可见；普通成员发这条只会收到「权限不足」）",
        "",
    ]
    for title, level, rows in ADMIN_GROUPS:
        if level == "owner" and not is_owner:
            continue
        lines.append(f"【{title}】")
        for cmd, desc in rows:
            lines.append(f"  {_pad(cmd, 30)}{desc}")
        lines.append("")
    lines.append("💡 说明")
    for note in ADMIN_NOTES:
        lines.append(f"  · {note}")
    if not is_owner:
        lines.append("  · 你当前是管理员；重启/开关/管理员增删属主人专属")
    lines.append("")
    lines.append("⚠ 写舞萌账号有封号风险，科技权限别乱给")
    return "\n".join(lines)


def _is_admin(uid: int) -> tuple[bool, bool]:
    """返回 (是否管理员及以上, 是否主人)。延迟导入避免循环依赖。"""
    try:
        from xiaoyo_bot.plugins.owner_control import is_admin, is_owner
        return bool(is_admin(uid)), bool(is_owner(uid))
    except Exception:  # noqa: BLE001
        return False, False


# ====================== 普通帮助 ======================
def in_tech_list(uid: int, gid: int | None = None) -> bool:
    """是否**在这个会话里**具备科技身份：科技白名单或管理员，且（群聊时）群在科技群里。

    群维度对管理员同样生效：群未授权 → 出普通版。私聊不受群限制。
    """
    try:
        from xiaoyo_bot.plugins.mai_modify import eff_tier  # 延迟导入，避免循环依赖
        return eff_tier(uid, gid) == "tech"
    except Exception:  # noqa: BLE001
        return False


def pick_help_image(uid: int, gid: int | None = None) -> Path | None:
    img = IMG_TECH if in_tech_list(uid, gid) else IMG_NORMAL
    if not img.exists():                      # 另一张在就用另一张，不回空
        img = IMG_NORMAL if img == IMG_TECH else IMG_TECH
    return img if img.exists() else None


@help_cmd.handle()
async def handle_help(event: MessageEvent) -> None:
    """发帮助图：科技白名单 + 已允许的群 → 科技版，其余一律普通版。"""
    gid = getattr(event, "group_id", None)
    img = pick_help_image(event.user_id, int(gid) if gid is not None else None)
    if img is None:
        await help_cmd.finish(HELP_FALLBACK)
        return
    await help_cmd.finish(MessageSegment.image(img.read_bytes()))


# ====================== 管理帮助 ======================
@admin_help_cmd.handle()
async def handle_admin_help(event: MessageEvent) -> None:
    allowed, owner = _is_admin(event.user_id)
    if not allowed:
        await admin_help_cmd.finish("❌ 权限不足：管理帮助仅管理员及以上可见")
        return
    await admin_help_cmd.finish(build_admin_help(owner))


@dev_help_cmd.handle()
async def handle_dev_help(event: MessageEvent) -> None:
    """旧命令「开发者帮助」：同样只给管理员及以上，内容指向管理帮助。"""
    allowed, owner = _is_admin(event.user_id)
    if not allowed:
        await dev_help_cmd.finish("❌ 权限不足：该帮助仅管理员及以上可见")
        return
    await dev_help_cmd.finish(build_admin_help(owner))
