"""小幽 · 仿真层（humanlike）：把 llmchat 的「一句一答」改成像真人在 QQ 上聊天。

四块，全部参数化、可关、可测：
  ① 节奏  send.*     —— 一次回 1~3 条、条间 1~3 秒、20% 概率插 5~10 秒长间隔
  ② 风控  risk.*     —— 每会话 ≤8 条/分、≤60 条/时，全局 ≤20 条/分；超限丢弃多余分段
  ③ 上下文 context.* —— 历史 40 轮 / 群消息 30 条；@ 你时把最近 12 条群聊当背景（不再只看那一条）
  ④ 主动  proactive.* —— 空闲 ≥15 分钟起算，30~90 分钟随机检查，30% 概率主动开口，每会话每天 ≤3

数值全部取自 /opt/qq-bridge/config.json（那套是调过的），不用重新试。

实现方式：**不 fork llmchat**，用三层注入
  · monkeypatch `send_split_messages` / `format_message`（纯运行时，不碰文件）
  · 改 `plugin_config.history_size / past_events_size / random_trigger_prob`
  · 只有「@ 时也带上群聊背景」这一处必须改源码 → 自愈补丁 `ensure_source_patched()`
    （幂等、留 .bak、失效只 warning 不阻塞；pip 升级后下次启动自动补回）

配置：storage/humanlike.json（首次启动自动生成默认值）。
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from nonebot import get_driver, on_message
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import (
    Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment,
)

BASE_DIR = Path(__file__).resolve().parents[2]        # .../Xiaoyo_Bot
CONF_FILE = BASE_DIR / "storage" / "humanlike.json"
STATE_FILE = BASE_DIR / "storage" / "humanlike_state.json"

DEFAULTS: dict = {
    "enabled": True,
    "send": {
        "burst_max": 3,              # 普通闲聊一次最多几条
        "hard_max": 8,               # 硬上限（llmchat 一次最多吐这么多段）
        "gap_min_ms": 1000,
        "gap_max_ms": 3000,
        "long_gap_prob": 0.2,        # 20% 的分段间隔换成 5~10 秒长间隔
        "long_gap_min_ms": 5000,
        "long_gap_max_ms": 10000,
        "max_chars": 500,            # 单条上限，超了就在这里截断并记日志
    },
    "reply": {"quote": True},        # 回复时引用对方那条消息
    "trigger": {"random_prob": 0.06},  # 群里没 @ 也插话的概率（0 关掉）
    "context": {
        "history_size": 40,
        "past_events_size": 30,
        "background_max": 12,        # @ 你时额外带上的群聊背景条数
    },
    "risk": {"per_min": 8, "per_hour": 60, "global_per_min": 20},
    "proactive": {
        "enabled": True,
        "idle_min_ms": 900000,       # 会话安静 15 分钟才算「可以搭话」
        "check_min_ms": 1800000,     # 30 分钟
        "check_max_ms": 5400000,     # 90 分钟
        "prob": 0.3,
        "daily_cap": 3,              # 每会话每天最多主动几次
        "group_daily_cap": 1,        # 群里更克制（每个群每天最多 1 次）
        "quiet_from": 0,
        "quiet_to": 7,               # 0~7 点不主动
        "private": True,
        "groups": True,
    },
}


# ============================ 配置 ============================
def deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_conf() -> dict:
    """读 storage/humanlike.json（缺字段用默认值补齐，坏了就用默认值）。"""
    if not CONF_FILE.exists():
        try:
            CONF_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONF_FILE.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except OSError as exc:
            logger.warning(f"[humanlike] 写默认配置失败：{exc}")
        return dict(DEFAULTS)
    try:
        return deep_merge(DEFAULTS, json.loads(CONF_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        logger.warning(f"[humanlike] 配置读不了（{exc}），本次用默认值")
        return dict(DEFAULTS)


CFG = load_conf()


# ============================ ① 节奏 ============================
class Pacer:
    """把「一次回复的若干段」摊成带随机间隔的发送计划。纯函数，可注入 rng 便于测。"""

    def __init__(self, send_cfg: dict, rng: random.Random | None = None):
        self.c = send_cfg
        self.rng = rng or random

    def clamp(self, segments: list[str], limit: int | None = None) -> list[str]:
        """截断：条数上限（默认 hard_max，普通回复传 burst_max）与单条字数上限。"""
        n = int(limit if limit is not None else self.c.get("hard_max", 8))
        out = [s for s in segments if s][:n]
        cap = int(self.c.get("max_chars", 500))
        return [(s if len(s) <= cap else s[:cap]) for s in out]

    def gaps(self, n: int) -> list[float]:
        """n 段之间的 n-1 个间隔（秒）。"""
        gaps = []
        for _ in range(max(0, n - 1)):
            if self.rng.random() < float(self.c.get("long_gap_prob", 0.2)):
                lo, hi = self.c.get("long_gap_min_ms", 5000), self.c.get("long_gap_max_ms", 10000)
            else:
                lo, hi = self.c.get("gap_min_ms", 1000), self.c.get("gap_max_ms", 3000)
            gaps.append(self.rng.uniform(lo, hi) / 1000.0)
        return gaps


# ============================ ② 风控 ============================
class RateLimiter:
    """滑动窗口限速：每会话 + 全局。超限**丢弃**多余分段（宁可少说，不冒风控风险）。"""

    def __init__(self, per_min: int, per_hour: int, global_per_min: int,
                 clock=time.time):
        self.per_min, self.per_hour, self.global_per_min = per_min, per_hour, global_per_min
        self.clock = clock
        self.hits: dict[str, deque] = {}
        self.all_hits: deque = deque()

    @staticmethod
    def _trim(dq: deque, now: float) -> None:
        while dq and now - dq[0] > 3600:
            dq.popleft()

    def allow(self, key: str) -> bool:
        now = self.clock()
        dq = self.hits.setdefault(key, deque())
        self._trim(dq, now)
        in_min = sum(1 for t in dq if now - t <= 60)
        if in_min >= self.per_min or len(dq) >= self.per_hour:
            return False
        while self.all_hits and now - self.all_hits[0] > 60:
            self.all_hits.popleft()
        if len(self.all_hits) >= self.global_per_min:
            return False
        dq.append(now)
        self.all_hits.append(now)
        return True


# ============================ ④ 主动：判定 ============================
def in_quiet_hours(hour: int, qf: int, qt: int) -> bool:
    if qf == qt:
        return False
    if qf < qt:
        return qf <= hour < qt
    return hour >= qf or hour < qt           # 跨零点，如 23→7


@dataclass
class ConvState:
    kind: str
    cid: int
    last_active: float = 0.0
    last_proactive: float = 0.0
    day: str = ""
    count: int = 0
    recent: deque = field(default_factory=lambda: deque(maxlen=12))

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.cid}"


def proactive_reason(st: ConvState, now: float, p: dict, hour: int,
                     rng: random.Random | None = None, cap: int | None = None) -> str:
    """返回放行原因（'ok'）或拒绝原因（用于日志与测试）。"""
    rng = rng or random
    if not p.get("enabled", True):
        return "disabled"
    if st.kind == "private" and not p.get("private", True):
        return "private_off"
    if st.kind == "group" and not p.get("groups", True):
        return "groups_off"
    limit = cap if cap is not None else int(p.get("daily_cap", 3))
    if st.day == datetime.fromtimestamp(now).strftime("%Y-%m-%d") and st.count >= limit:
        return "daily_cap"
    if in_quiet_hours(hour, int(p.get("quiet_from", 0)), int(p.get("quiet_to", 7))):
        return "quiet_hours"
    if now - st.last_active < float(p.get("idle_min_ms", 900000)) / 1000.0:
        return "too_recent"
    if now - st.last_proactive < float(p.get("idle_min_ms", 900000)) / 1000.0:
        return "just_proactive"
    if rng.random() >= float(p.get("prob", 0.3)):
        return "dice"
    return "ok"


# ============================ 状态持久化 ============================
class Store:
    def __init__(self, path: Path):
        self.path = path
        self.convs: dict[str, ConvState] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for k, v in (raw.get("convs") or {}).items():
            try:
                kind, cid = k.split(":")
                st = ConvState(kind=kind, cid=int(cid), last_active=float(v.get("last_active", 0)),
                               last_proactive=float(v.get("last_proactive", 0)),
                               day=str(v.get("day", "")), count=int(v.get("count", 0)))
                st.recent.extend(v.get("recent") or [])
                self.convs[k] = st
            except (ValueError, TypeError):
                continue

    def save(self) -> None:
        data = {"convs": {k: {"last_active": s.last_active, "last_proactive": s.last_proactive,
                              "day": s.day, "count": s.count,
                              "recent": list(s.recent)} for k, s in self.convs.items()}}
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning(f"[humanlike] 状态写盘失败：{exc}")

    def get(self, kind: str, cid: int) -> ConvState:
        k = f"{kind}:{cid}"
        st = self.convs.get(k)
        if st is None:
            st = ConvState(kind=kind, cid=int(cid))
            self.convs[k] = st
        return st


STORE = Store(STATE_FILE)
LIMITER = RateLimiter(int(CFG["risk"]["per_min"]), int(CFG["risk"]["per_hour"]),
                      int(CFG["risk"]["global_per_min"]))


# ============================ ③ 上下文：@ 时也带群聊背景 ============================
_MARK = "[humanlike]"
_OLD_BLOCK = """                if tome_events:
                    ordered = tome_events"""
_NEW_BLOCK = """                if tome_events:
                    # [humanlike] 上下文：@你的时候也把最近群聊当背景，@ 的消息放最后
                    ordered = other_events[-{n}:] + tome_events"""


# ============================ 黑话注入：给 llmchat 加一行（独立补丁）============================
_SLANG_MARK = "[humanlike:slang]"
_SLANG_ANCHOR = "            for ev in ordered:\n"
_SLANG_BLOCK = """            # [humanlike:slang] 黑话/梗库注入系统提示（不污染 user 消息）
            try:
                from xiaoyo_bot.slang import inject_system as _slang_inject
                if messages and messages[0].get("role") == "system":
                    messages[0]["content"] = _slang_inject(
                        messages[0]["content"], event, is_group)
            except Exception as _slang_err:
                logger.debug(f"slang 注入跳过：{_slang_err}")
"""


def patch_slang_source(src: str) -> tuple[str, str]:
    """把「黑话注入」插到 ordered 循环前。状态 = 'already' / 'patched' / 'miss'。"""
    if _SLANG_MARK in src:
        return src, "already"
    if _SLANG_ANCHOR not in src:
        return src, "miss"
    return src.replace(_SLANG_ANCHOR, _SLANG_BLOCK + _SLANG_ANCHOR, 1), "patched"


def patch_context_source(src: str, background_max: int = 12) -> tuple[str, str]:
    """返回 (新源码, 状态)。状态 = 'already' / 'patched' / 'miss'。纯函数，便于回归。"""
    if _MARK in src:
        return src, "already"
    if _OLD_BLOCK not in src:
        return src, "miss"
    return src.replace(_OLD_BLOCK, _NEW_BLOCK.format(n=int(background_max)), 1), "patched"


def ensure_source_patched(background_max: int = 12) -> str:
    """自愈补丁：pip 升级把 llmchat 覆盖回原样后，下次启动自动补回。"""
    try:
        import nonebot_plugin_llmchat as L
        path = Path(L.__file__)
    except Exception as exc:                      # noqa: BLE001
        logger.warning(f"[humanlike] 找不到 llmchat，跳过上下文补丁：{exc}")
        return "no-module"
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(f"[humanlike] 读 llmchat 源码失败：{exc}")
        return "io-error"
    new, status = patch_context_source(src, background_max)
    if status == "patched":
        try:
            path.with_suffix(".py.bak-humanlike").write_text(src, encoding="utf-8")
            path.write_text(new, encoding="utf-8")
            logger.info("[humanlike] 已给 llmchat 打上「@ 时带群聊背景」补丁（**下次重启生效**）")
        except OSError as exc:
            logger.warning(f"[humanlike] 写 llmchat 源码失败：{exc}")
            return "write-error"
    elif status == "miss":
        logger.warning("[humanlike] llmchat 结构变了（没找到 @ 分支），上下文补丁跳过——"
                       "剩下三块不受影响")
    return status


def ensure_slang_patched() -> str:
    """黑话注入补丁：同样的自愈策略（幂等 / 留 .bak / 失效只 warning）。"""
    try:
        import nonebot_plugin_llmchat as L
        path = Path(L.__file__)
    except Exception as exc:                      # noqa: BLE001
        logger.warning(f"[humanlike] 找不到 llmchat，跳过黑话注入补丁：{exc}")
        return "no-module"
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(f"[humanlike] 读 llmchat 源码失败：{exc}")
        return "io-error"
    new, status = patch_slang_source(src)
    if status == "patched":
        try:
            path.with_suffix(".py.bak-humanlike").write_text(src, encoding="utf-8")
            path.write_text(new, encoding="utf-8")
            logger.info("[humanlike] 已给 llmchat 打上「黑话注入」补丁（**下次重启生效**）")
        except OSError as exc:
            logger.warning(f"[humanlike] 写 llmchat 源码失败：{exc}")
            return "write-error"
    elif status == "miss":
        logger.warning("[humanlike] llmchat 结构变了（没找到 ordered 循环），黑话注入补丁跳过")
    return status


# ============================ 方括号表情清理（[嘿嘿] / ［喵］）============================
# 现象：人设里「语气词：… 嘿嘿、喵」被模型读成「情绪标签」，于是回复末尾挂一个 [嘿嘿]。
# 提示词里已经禁止，这里再做一层代码兜底（模型仍可能偶发），也覆盖历史人设副本。
_EMOTE_WORDS = {
    "嘿嘿", "喵", "喵喵", "笑", "笑哭", "偷笑", "奸笑", "捂脸", "滑稽", "狗头", "旺柴",
    "doge", "泪", "允悲", "呜呜", "叹气", "思考", "得意", "委屈", "无语", "害羞", "坏笑",
    "大笑", "微笑", "开心", "哭", "哼", "嘀咕", "小声", "哈欠", "困", "抖", "抱抱", "蹭",
}
_BRACKET_RE = re.compile(r"[\[［【(（]\s*([\u4e00-\u9fa5A-Za-z]{1,4})\s*[\]］】)）]")


def strip_emote_brackets(text: str) -> str:
    """删掉括号标注的情绪/动作（只删白名单里的词，正常括号内容不动）。"""
    s = str(text or "")

    def _repl(m: re.Match) -> str:
        word = m.group(1)
        return "" if word in _EMOTE_WORDS or word.lower() in _EMOTE_WORDS else m.group(0)

    s = _BRACKET_RE.sub(_repl, s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


# ============================ 发送（节奏 + 引用 + 风控）============================
def _event_of(handler) -> MessageEvent | None:
    ev = getattr(handler, "event", None)
    return ev if isinstance(ev, MessageEvent) else None


def _conv_of(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    return f"private:{event.user_id}"


async def send_with_pacing(handler, content: str) -> None:
    """替换 llmchat.send_split_messages：分段、随机间隔、引用、限速。"""
    try:
        import nonebot_plugin_llmchat as L
        strip = getattr(L, "strip_trailing_period", lambda s: s)
    except Exception:                              # noqa: BLE001
        strip = lambda s: s                        # noqa: E731

    segs = [s.strip() for s in str(content or "").split("<botbr>") if s.strip()]
    if not segs:
        return
    pacer = Pacer(CFG["send"])
    limit = int(CFG["send"].get("burst_max", 3))
    segs = [strip_emote_brackets(strip(s)) for s in pacer.clamp(segs, limit=limit)]
    segs = [s for s in segs if s]
    if not segs:
        return

    ev = _event_of(handler)
    key = _conv_of(ev) if ev else "unknown"
    gaps = pacer.gaps(len(segs))
    reply_id = getattr(ev, "message_id", None) if ev is not None else None
    quote_ok = bool(CFG["reply"].get("quote", True)) and reply_id is not None

    for i, seg in enumerate(segs):
        if i:
            await asyncio.sleep(gaps[i - 1])
        if not LIMITER.allow(key):
            logger.warning(f"[humanlike] 风控：{key} 超限，丢弃剩余 {len(segs) - i} 条")
            return
        msg = Message(seg)
        if i == 0 and quote_ok:
            try:
                msg = Message(MessageSegment.reply(int(reply_id))) + msg
            except Exception:                      # noqa: BLE001
                pass
        await handler.send(msg)
        try:                                       # 记下用了哪些梗，等群里接不接
            from xiaoyo_bot.slang import note_sent as _note_sent
            _note_sent(key, seg)
        except Exception as exc:                   # noqa: BLE001
            logger.debug(f"[humanlike] 记梗失败：{exc}")


# ============================ 上下文：给 LLM 标上「谁说的」============================
_NICK_CACHE: dict[tuple[int, int], str] = {}


def _nick(event: MessageEvent) -> str:
    sender = getattr(event, "sender", None)
    name = (getattr(sender, "card", "") or getattr(sender, "nickname", "") or "").strip()
    return name or str(getattr(event, "user_id", "?"))


def format_with_sender(original, event) -> str:
    """原 format_message 不写发言者，LLM 分不清谁是谁；这里补「昵称: 内容」。"""
    try:
        text = original(event)
    except Exception:                              # noqa: BLE001
        text = event.get_plaintext()
    text = (text or "").strip()
    if not isinstance(event, GroupMessageEvent):
        return text
    if str(getattr(event, "user_id", "")) == str(getattr(event, "self_id", "")):
        return text
    name = _nick(event)
    return f"{name}：{text}" if text else text


# ============================ ④ 主动开口 ============================
async def _llm_say(system: str, user: str) -> str:
    """用 llmchat 同一个 GLM 预设生成一句主动开场白。失败返回空串（宁可不发）。"""
    try:
        from nonebot_plugin_llmchat import plugin_config
        from openai import AsyncOpenAI
        presets = plugin_config.api_presets or []
        if not presets:
            return ""
        p = presets[0]
        client = AsyncOpenAI(api_key=p.api_key, base_url=p.api_base, timeout=25)
        rsp = await client.chat.completions.create(
            model=p.model_name, temperature=float(p.temperature or 0.8),
            max_tokens=120,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return (rsp.choices[0].message.content or "").strip()
    except Exception as exc:                       # noqa: BLE001
        logger.warning(f"[humanlike] 主动发言生成失败：{exc}")
        return ""


async def _proactive_loop() -> None:
    p = CFG["proactive"]
    rng = random.Random()
    while True:
        lo = int(p.get("check_min_ms", 1800000)) / 1000.0
        hi = max(lo, int(p.get("check_max_ms", 5400000)) / 1000.0)
        await asyncio.sleep(rng.uniform(lo, hi))
        if not CFG.get("enabled", True) or not p.get("enabled", True):
            continue
        now = time.time()
        hour = datetime.now().hour
        try:
            from nonebot import get_bots
            bots = list(get_bots().values())
        except Exception:                                     # noqa: BLE001
            bots = []
        bot = next((b for b in bots if b), None)
        if bot is None:
            continue
        for st in list(STORE.convs.values()):
            cap = int(p.get("group_daily_cap" if st.kind == "group" else "daily_cap", 3))
            reason = proactive_reason(st, now, p, hour, rng, cap)
            if reason != "ok":
                continue
            recent = " / ".join(list(st.recent)[-6:]) or "（最近没聊什么）"
            system = (
                "你叫小幽，是个真人在 QQ 上聊天，不是 AI。"
                "现在对话安静了一会儿，你想主动开口一句，像随手想起什么就说。"
                "只要一句，10 到 20 个字，不解释、不铺垫、不用 emoji、不加句号，不提自己是 AI。"
            )
            text = await _llm_say(system, f"最近的对话：{recent}\n现在说一句：")
            if not text:
                continue
            segs = [strip_emote_brackets(s.strip().rstrip("。.")) for s in text.split("<botbr>")
                    if s.strip()][:1]
            segs = [s for s in segs if s]
            if not segs:
                continue
            if not LIMITER.allow(f"{st.kind}:{st.cid}"):
                logger.warning(f"[humanlike] 主动发言被风控拦下：{st.key}")
                continue
            try:
                if st.kind == "group":
                    await bot.send_group_msg(group_id=st.cid, message=Message(segs[0]))
                else:
                    await bot.send_private_msg(user_id=st.cid, message=Message(segs[0]))
            except Exception as exc:                 # noqa: BLE001
                logger.warning(f"[humanlike] 主动发言发送失败 {st.key}：{exc}")
                continue
            st.last_proactive = now
            st.count = (st.count + 1) if st.day == datetime.now().strftime("%Y-%m-%d") else 1
            st.day = datetime.now().strftime("%Y-%m-%d")
            STORE.save()
            logger.info(f"[humanlike] 主动开口 {st.key}（今日第 {st.count} 次）：{segs[0][:30]}")


# ============================ 记录活动（供主动判定）============================
recorder = on_message(priority=1, block=False)


@recorder.handle()
async def _record(event: MessageEvent) -> None:
    if not CFG.get("enabled", True):
        return
    try:
        if isinstance(event, GroupMessageEvent):
            st = STORE.get("group", event.group_id)
        else:
            st = STORE.get("private", event.user_id)
        st.last_active = time.time()
        st.recent.append(format_with_sender(lambda e: e.get_plaintext(), event)[:200])
        if random.random() < 0.05:                   # 抽样落盘，别每条消息都写
            STORE.save()
    except Exception as exc:                          # noqa: BLE001
        logger.debug(f"[humanlike] 记录活动失败：{exc}")


# ============================ 安装 ============================
def install() -> None:
    try:
        import nonebot_plugin_llmchat as L
    except Exception as exc:                          # noqa: BLE001
        logger.warning(f"[humanlike] llmchat 未加载，仿真层不装配：{exc}")
        return

    L.send_split_messages = send_with_pacing
    _orig_fmt = getattr(L, "format_message", None)
    if _orig_fmt is not None:
        L.format_message = lambda ev: format_with_sender(_orig_fmt, ev)

    ctx = CFG["context"]
    L.plugin_config.history_size = int(ctx.get("history_size", 40))
    L.plugin_config.past_events_size = int(ctx.get("past_events_size", 30))
    L.plugin_config.random_trigger_prob = float(CFG["trigger"].get("random_prob", 0.06))
    for states in (getattr(L, "group_states", {}), getattr(L, "private_chat_states", {})):
        try:
            for st in states.values():
                st.random_trigger_prob = L.plugin_config.random_trigger_prob
                st.history = deque(st.history, maxlen=L.plugin_config.history_size * 2)
                st.past_events = deque(st.past_events,
                                       maxlen=L.plugin_config.past_events_size)
        except Exception:                             # noqa: BLE001
            continue

    status = ensure_source_patched(int(ctx.get("background_max", 12)))
    slang_status = ensure_slang_patched()
    logger.info(
        "[humanlike] 已装配：节奏 %d~%dms(+%d%% 长间隔) / 一次≤%d 条 / 引用=%s / "
        "插话=%.0f%% / 历史=%d轮 / 群背景=%d 条 / 风控 %d每分 %d每时 / 主动=%s(日≤%d) / 补丁=%s"
        % (CFG["send"]["gap_min_ms"], CFG["send"]["gap_max_ms"],
           int(float(CFG["send"]["long_gap_prob"]) * 100), CFG["send"]["burst_max"],
           CFG["reply"]["quote"], float(CFG["trigger"]["random_prob"]) * 100,
           CFG["context"]["history_size"], CFG["context"]["background_max"],
           CFG["risk"]["per_min"], CFG["risk"]["per_hour"],
           CFG["proactive"]["enabled"], CFG["proactive"]["daily_cap"], status))
    logger.info(f"[humanlike] 黑话注入补丁={slang_status}")


driver = get_driver()


@driver.on_startup
async def _boot() -> None:
    install()
    if CFG.get("enabled", True) and CFG["proactive"].get("enabled", True):
        asyncio.create_task(_proactive_loop())
        logger.info("[humanlike] 主动开口已排程（空闲 15min 起算，30~90min 检查一次，30% 概率）")
    else:
        logger.info("[humanlike] 主动开口未启用")
