"""视频解析插件用法：
小幽获取视频 <视频链接> - 解析视频链接
"""

import re

from nonebot import on_command
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="视频解析",
    description="视频解析工具",
    usage="小幽获取视频 <视频链接>",
)

# 解析VIP视频
vip_parser = on_command(
    "小幽获取视频", aliases={"获取视频", "视频解析"}, priority=4, block=True
)


@vip_parser.handle()
async def handle_vip_parser(_bot: Bot, event: MessageEvent) -> None:
    msg = event.get_message()
    text = msg.extract_plain_text().strip()

    # 用正则表达式提取URL（http/https开头的）
    url_match = re.search(r"https?://[^\s]+", text)

    if url_match:
        video_url = url_match.group(0)
    else:
        # 没有URL，返回提示
        await vip_parser.finish(
            "请输入视频链接！\n\n"
            "正确用法：\n"
            "小幽获取视频+视频链接\n"
            "或：\n"
            "小幽获取视频 视频链接\n\n"
            "示例：\n"
            "小幽获取视频 https://www.iqiyi.com/..."
        )
        return

    # 解析链接
    parse_url = f"https://jx.xmflv.cc/?url={video_url}"

    await vip_parser.finish(
        f"解析成功！\n\n"
        f"复制链接到浏览器即可播放：\n{parse_url}\n\n"
        f"⚠️ 叠甲提示：禁盗用商用仅供学习参考"
    )
