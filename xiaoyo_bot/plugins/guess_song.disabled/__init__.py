import asyncio
import random

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from .song_data import SONG_POOL

__plugin_meta__ = PluginMetadata(
    name="小幽",
    description="3个提示、10秒间隔、支持别名猜歌",
    usage="发送小幽猜歌，即可开始",
)

# 全局状态：每个群/用户只能进行一个游戏
guess_song_status = {}  # key: {answer, aliases, hints, step, running, is_group}

# Keep references to background tasks so they aren't GC'ed early.
_hint_tasks: set[asyncio.Task] = set()

# 开始猜歌
guess_start = on_command("小幽猜歌", priority=3, block=True)


@guess_start.handle()
async def _(bot: Bot, event: MessageEvent) -> None:
    # 确定唯一标识：群聊用group_id，私聊用user_id
    if isinstance(event, GroupMessageEvent):
        key = event.group_id
        is_group = True
        msg_prefix = "本群"
    else:  # PrivateMessageEvent
        key = event.user_id
        is_group = False
        msg_prefix = "您"

    # 防止重复开
    if guess_song_status.get(key, {}).get("running", False):
        await guess_start.finish(f"{msg_prefix}正在猜歌中")

    # 随机抽歌
    song = random.choice(list(SONG_POOL.keys()))
    hints = SONG_POOL[song][:3]
    aliases = SONG_POOL[song][3]

    guess_song_status[key] = {
        "answer": song,
        "aliases": aliases,
        "hints": hints,
        "step": 0,
        "running": True,
        "is_group": is_group,
    }

    await guess_start.send("猜歌开始，共3个提示10秒一条")

    # 异步提示
    task = asyncio.create_task(send_hints(bot, key))
    _hint_tasks.add(task)
    task.add_done_callback(_hint_tasks.discard)


# 自动发3条提示
async def send_hints(bot: Bot, key: int) -> None:
    status = guess_song_status.get(key)
    if not status:
        return

    for i in range(3):
        await asyncio.sleep(10)
        status = guess_song_status.get(key)
        if not status or not status["running"]:
            return

        status["step"] = i + 1
        hint = status["hints"][i]

        # 根据is_group决定发送方式
        if status["is_group"]:
            await bot.send_group_msg(group_id=key, message=f"提示 {i + 1}：{hint}")
        else:
            await bot.send_private_msg(user_id=key, message=f"提示 {i + 1}：{hint}")

    # 全发完还没人猜中
    await asyncio.sleep(30)
    status = guess_song_status.get(key)
    if status and status["running"]:
        ans = status["answer"]
        if status["is_group"]:
            await bot.send_group_msg(
                group_id=key, message=f"还没猜出来吗。答案是：{ans}"
            )
        else:
            await bot.send_private_msg(
                user_id=key, message=f"还没猜出来吗。答案是：{ans}"
            )
        guess_song_status[key]["running"] = False


def guess_game_running() -> Rule:
    """检查猜歌游戏是否正在进行"""

    async def _check(event: MessageEvent) -> bool:
        # 确定唯一标识：群聊用group_id，私聊用user_id
        key = event.group_id if isinstance(event, GroupMessageEvent) else event.user_id
        status = guess_song_status.get(key)
        return status is not None and status.get("running", False)

    return Rule(_check)


@on_message(priority=4, block=False, rule=guess_game_running()).handle()
async def check_answer(bot: Bot, event: MessageEvent) -> None:
    # 确定唯一标识：群聊用group_id，私聊用user_id
    if isinstance(event, GroupMessageEvent):
        key = event.group_id
        uid = event.user_id
        is_group = True
    else:  # PrivateMessageEvent
        key = event.user_id
        uid = event.user_id
        is_group = False

    msg = event.message.extract_plain_text().strip()

    status = guess_song_status.get(key)
    if not status or not status["running"]:
        return

    answer = status["answer"]
    aliases = status["aliases"]

    # 判断：歌名别名都算对
    if msg == answer or msg in aliases:
        guess_song_status[key]["running"] = False
        if is_group:
            await bot.send_group_msg(
                group_id=key, message=f" [CQ:at,qq={uid}] 猜对了，答案为：{answer}"
            )
        else:
            await bot.send_private_msg(user_id=key, message=f"猜对了，答案为：{answer}")
