"""群聊复读参加：同一句话被 3 个不同的人重复后，小幽自动补一句「+1」参加。

规则（都可调，见下面常量）：
  · 90 秒窗口内，同一句原文被 ≥3 个不同 QQ 发过 → 触发一次
  · 触发后本群 10 分钟内不再触发（防刷）
  · 只参与纯文本；图片/表情/超长句/命令（以「小幽」「/」开头）不算
  · 不统计机器人自己的消息（否则会和自己的 +1 套娃）

判断逻辑全在 note() 里，纯函数式（时间由调用方传入），方便离线回归。
"""

import time

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

WINDOW = 90.0          # 多久之内的重复算「同一轮复读」
MIN_SENDERS = 3        # 至少要几个不同的人发过
COOLDOWN = 600.0       # 触发后本群冷却
MAX_LEN = 40           # 超过这个长度不参与（长段落不是复读）
REPLY = "+1"           # 小幽参加时发的内容

# gid -> [(ts, uid, text), ...]（只保留最近一小段）
_streaks: dict[int, list[tuple[float, int, str]]] = {}
_last_fire: dict[int, float] = {}


def is_candidate(text: str) -> bool:
    """这句话值不值得参与判断。"""
    t = (text or "").strip()
    if not t or len(t) > MAX_LEN:
        return False
    if "[CQ:" in t:                 # 含图片/表情/at 的段
        return False
    if t.startswith(("小幽", "/", "／", "!", "！")):
        return False
    if t.startswith("+"):           # 别把自己的 +1 当复读
        return False
    return True


def note(gid: int, uid: int, text: str, now: float | None = None) -> bool:
    """记一条群消息；返回 True 表示「该补一句 +1 了」。"""
    now = time.time() if now is None else now
    t = (text or "").strip()
    if not is_candidate(t):
        return False

    entries = [e for e in _streaks.get(gid, []) if now - e[0] <= WINDOW]
    if entries and entries[-1][2] != t:      # 换了一句话 → 重新计数
        entries = []
    entries.append((now, int(uid), t))
    entries = entries[-16:]
    _streaks[gid] = entries

    senders = {u for _, u, s in entries if s == t}
    if len(senders) < MIN_SENDERS:
        return False
    if now - _last_fire.get(gid, 0.0) < COOLDOWN:
        return False

    _last_fire[gid] = now
    _streaks[gid] = []                       # 清掉，别同一句连着刷
    return True


def reset() -> None:
    """测试用：清空所有状态。"""
    _streaks.clear()
    _last_fire.clear()


chat_watch = on_message(priority=15, block=False)


@chat_watch.handle()
async def handle_chat(bot: Bot, event: GroupMessageEvent) -> None:
    try:
        if int(event.user_id) == int(event.self_id):
            return
    except (TypeError, ValueError):
        return
    if note(event.group_id, event.user_id, event.get_plaintext()):
        await chat_watch.send(REPLY)
