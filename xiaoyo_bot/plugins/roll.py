"""小幽随机 / roll：从用户给的几个可选项里随机挑一个（2~6 项）。

用法：
  小幽随机 吃饭 睡觉 打豆豆
  小幽随机+火锅+烤肉+日料
  /roll A B C
  roll 一 二 三

⚠ 适配器会先把开头的昵称「小幽」剥掉（Bot._check_nickname），
   所以实际进 TrieRule 的是「随机」——必须显式给这个别名。
"""

import random
import re

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent

MAX_OPTIONS = 6
MIN_OPTIONS = 2

_SPLIT_RE = re.compile(r"[+＋,，、;；/|\s]+")
_CMD_RE = re.compile(r"^(?:小幽)?(?:随机|roll)", re.I)
_AT_RE = re.compile(r"^@\S+\s*")


def parse_options(raw: str) -> list[str]:
    """把「小幽随机 吃饭 睡觉」拆成 ['吃饭','睡觉']。

    支持 空格 / + / 全角＋ / ， , 、 ; ； / | 分隔；自动去掉 @机器人 和命令词。
    """
    t = (raw or "").strip()
    t = _AT_RE.sub("", t)
    t = _CMD_RE.sub("", t)
    return [p.strip() for p in _SPLIT_RE.split(t) if p.strip()]


def pick(options: list[str], rng: random.Random | None = None) -> str:
    return (rng or random).choice(options)


roll_cmd = on_command("小幽随机", aliases={"随机", "roll", "Roll", "ROLL", "r"},
                      priority=5, block=True)


@roll_cmd.handle()
async def handle_roll(event: MessageEvent) -> None:
    opts = parse_options(event.get_plaintext())

    if len(opts) < MIN_OPTIONS:
        await roll_cmd.finish(
            "🎲 用法：小幽随机 选项1 选项2 …\n"
            "2~6 项，空格或 + 分隔\n"
            "例：小幽随机 吃饭 睡觉 打豆豆"
        )
    if len(opts) > MAX_OPTIONS:
        await roll_cmd.finish(f"🎲 最多 {MAX_OPTIONS} 项，你这给了 {len(opts)} 项，去掉几个再来")

    await roll_cmd.finish(f"🎲 {len(opts)} 选 1 → {pick(opts)}")
