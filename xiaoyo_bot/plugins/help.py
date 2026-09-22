from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent

# 普通用户帮助指令
help_cmd = on_command("小幽帮助", aliases={"help"}, priority=3, block=True)

# 开发者功能帮助指令
dev_help_cmd = on_command(
    "小幽开发者帮助", aliases={"开发者帮助"}, priority=3, block=True
)


@help_cmd.handle()
async def handle_help(_event: MessageEvent) -> None:
    """发送普通用户帮助信息，包含所有普通用户能使用的功能和指令"""
    help_msg = """
✝️小幽功能简介✝️

B50（获取成绩图）
分数列表（指定分数成绩图）
今日舞萌（出勤运势）
xx是什么歌（查询歌曲）
idxxx（歌曲ID查询）
猜歌（发送猜歌帮助查看玩法，支持开字母/听歌猜曲/谱面猜歌/猜曲绘/线索猜歌/随机猜歌）
以下是测试功能：
小幽获取视频+某酷/某艺想观看的视频链接（获取无广vip视频链接  
小幽获取歌曲+歌名
直发某抑云无损音质歌曲
但不稳有时会失败比较抽奖）
关于AI聊天和回复暂不开放
（预计寒假回归）
bot主是傻福学生加急补作业中
可以请bot打舞萌嗯对👁👄👁
    """
    await help_cmd.finish(help_msg)


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
