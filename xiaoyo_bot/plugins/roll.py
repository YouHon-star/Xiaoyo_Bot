"""小幽随机 / roll：从用户给的几个可选项里随机挑一个（2~6 项）。

用法：
  小幽随机 吃饭 睡觉 打豆豆
  小幽随机+火锅+烤肉+日料
  /roll A B C
  roll 一 二 三

⚠ 适配器会先把开头的昵称「小幽」剥掉（Bot._check_nickname），
   所以「随机」这个裸别名必须显式注册。

★ 2026-11-01 修正：改用严格匹配，不再用 TrieRule 前缀命中。
  原来 aliases 里有单字母 "r"，而 TrieRule 是纯前缀比较（无词边界），
  于是「rua」「rn」「really good」…任何 r 开头的话都会命中本命令并回用法提示。
  现在规则是：命令词（随机 / roll）后面必须紧跟分隔符或整句结束；
  单字母 "r" 别名已删除（"r u ok" 这类英文会误中，代价大于收益）。
"""

import random
import re

from nonebot import on_message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.rule import Rule

MAX_OPTIONS = 6
MIN_OPTIONS = 2

_SPLIT_RE = re.compile(r"[+＋,，、;；/|\s]+")
_CMD_RE = re.compile(r"^(?:小幽)?(?:随机|roll)", re.I)
_AT_RE = re.compile(r"^@\S+\s*")
# command_start 是 {'/', ''}，用户会写「/roll a b」；剥掉开头的斜杠再判词边界
_CMD_START_RE = re.compile(r"^[/／]+")

# 命令词 + 词边界：后面必须是分隔符或行尾，避免「随机性」「rolling」误命中
_HEAD_RE = re.compile(r"^(?:小幽)?(?:随机|roll)(?=$|[\s+＋,，、;；/|])", re.I)


def _normalize(text: str) -> str:
    """去掉前缀 @机器人 / command_start，只留正文。"""
    t = (text or "").strip()
    t = _AT_RE.sub("", t).strip()
    return _CMD_START_RE.sub("", t).strip()


def is_roll_cmd(text: str) -> bool:
    """这句话是不是「小幽随机」指令（严格匹配，允许前缀 @ / 昵称 / 斜杠）。"""
    t = _normalize(text)
    if not t:
        return False
    return bool(_HEAD_RE.match(t))


def parse_options(raw: str) -> list[str]:
    """把「小幽随机 吃饭 睡觉」拆成 ['吃饭','睡觉']。

    支持 空格 / + / 全角＋ / ， , 、 ; ； / | 分隔；自动去掉 @机器人 和命令词。
    """
    t = _normalize(raw)
    t = _CMD_RE.sub("", t)
    return [p.strip() for p in _SPLIT_RE.split(t) if p.strip()]


def pick(options: list[str], rng: random.Random | None = None) -> str:
    return (rng or random).choice(options)


async def _roll_rule(event: MessageEvent) -> bool:
    return is_roll_cmd(event.get_plaintext())


roll_cmd = on_message(rule=Rule(_roll_rule), priority=5, block=True)


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
