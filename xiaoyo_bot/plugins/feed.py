"""小幽投喂：发一张打赏/投喂图（收款码 + 感谢）。

⚠ 适配器会先把开头的昵称「小幽」剥掉（Bot._check_nickname），所以用户发「小幽投喂」时
   进到匹配层只剩「投喂」——必须显式给 投喂 这个别名，否则永远匹配不到（help 就踩过这个坑）。
"""

from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment

tip_cmd = on_command(
    "小幽投喂", aliases={"投喂", "投喂小幽", "打赏"}, priority=5, block=True
)

IMG = Path("/home/xiaoyo/Xiaoyo_Bot/storage/feed_images/tip.jpg")
FALLBACK = "投喂通道暂时没打开喵～有事找幽鸿（1923497173）"

_COOLDOWN = 10.0          # 同一个人 10 秒内重复发就静默跳过，避免刷屏
_last: dict[int, float] = {}


@tip_cmd.handle()
async def handle_tip(event: MessageEvent) -> None:
    import time

    uid = int(event.user_id)
    now = time.time()
    if now - _last.get(uid, 0.0) < _COOLDOWN:
        return
    _last[uid] = now

    if IMG.exists():
        # 与 help.py 一致：传 bytes → 走 base64，不依赖 NapCat 能否读到本地路径
        await tip_cmd.finish(MessageSegment.image(IMG.read_bytes()))
    await tip_cmd.finish(FALLBACK)
