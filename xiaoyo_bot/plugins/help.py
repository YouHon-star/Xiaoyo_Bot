"""普通用户帮助：按是否在「科技白名单」发不同的图。

· 科技白名单/管理员 → storage/help_images/help_tech.jpg（含科技功能）
· 其他人           → storage/help_images/help_normal.jpg（不含科技功能）
图片缺失时退回文字版，保证 help 永不挂掉。
"""

from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment

# 普通用户帮助指令
help_cmd = on_command("小幽帮助", aliases={"help"}, priority=3, block=True)

# 开发者功能帮助指令
dev_help_cmd = on_command(
    "小幽开发者帮助", aliases={"开发者帮助"}, priority=3, block=True
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


def in_tech_list(uid: int) -> bool:
    """是否在科技白名单（管理员视为在内）。"""
    try:
        from xiaoyo_bot.plugins.mai_modify import tier_of  # 延迟导入，避免循环依赖
        return tier_of(uid) in ("admin", "tech")
    except Exception:  # noqa: BLE001
        return False


def pick_help_image(uid: int) -> Path | None:
    img = IMG_TECH if in_tech_list(uid) else IMG_NORMAL
    if not img.exists():                      # 另一张在就用另一张，不回空
        img = IMG_NORMAL if img == IMG_TECH else IMG_TECH
    return img if img.exists() else None


@help_cmd.handle()
async def handle_help(event: MessageEvent) -> None:
    """发帮助图：科技白名单一张，其他人另一张。"""
    img = pick_help_image(event.user_id)
    if img is None:
        await help_cmd.finish(HELP_FALLBACK)
        return
    await help_cmd.finish(MessageSegment.image(img.read_bytes()))


@dev_help_cmd.handle()
async def handle_dev_help(_event: MessageEvent) -> None:
    """发送开发者功能帮助信息，包含所有管理员和主人能使用的功能和指令"""
    dev_help_msg = """
小幽Bot开发者功能一览
👑 管理员功能
• 小幽状态
  └─ 查看Bot运行状态（仅管理员）
• 拉黑 [QQ号]
  └─ 拉黑指定用户（仅管理员）
• 解除拉黑 [QQ号]
  └─ 解除指定用户的拉黑（仅管理员）

👑 主人专属功能
• 小幽我是谁
  └─ 认主验证，确认是否为唯一主人
•开关bot
  └─ 切换Bot运行状态
• 重启bot
  └─ 重启Bot进程
• 添加管理员 [QQ号]
  └─ 添加指定用户为管理员
• 移除管理员 [QQ号]
  └─ 移除指定用户的管理员权限

💡 使用提示
• 以上功能需要相应的权限才能使用
• 主人拥有最高权限，默认具有管理员权限
• 管理员可以执行大部分管理操作
• 敏感操作（如重启、添加管理员）仅主人可用
    """
    await dev_help_cmd.finish(dev_help_msg)
