"""小幽 · 黑话/梗库入口（采集 + 闭环 + 管理指令）。

采集是旁路：priority=2、block=False，只把群消息塞进缓冲，攒够阈值才异步跑一轮学习，
不抢任何指令，也不影响聊天回复。

指令：
  小幽黑话                     看当前会话能用的词（按出现次数/被接梗次数排序）
  小幽黑话状态                  词库规模、上次学习时间、考究开关
  小幽学黑话                    立刻对当前群跑一轮学习（不等阈值）
  小幽黑话删 <词>               删词（管理员）
  小幽黑话加 <词> <含义>         手工加一条已确认的词（管理员）
"""

from __future__ import annotations

import asyncio

from nonebot import get_driver, on_command, on_message
from nonebot.adapters.onebot.v11 import Message, MessageEvent
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from xiaoyo_bot import slang
from xiaoyo_bot.perms import eff_tier, is_admin

__plugin_meta__ = PluginMetadata(
    name="黑话梗库",
    description="从群聊里自学黑话和梗，注入聊天上下文，并跟踪用了有没有人接梗",
    usage="小幽黑话 / 小幽黑话状态 / 小幽学黑话 / 小幽黑话删 词 / 小幽黑话加 词 含义",
)


def _scope(event: MessageEvent) -> str:
    return slang.scope_of(event)


def _gid(event: MessageEvent):
    return getattr(event, "group_id", None)


def _allowed(event: MessageEvent) -> bool:
    return eff_tier(int(getattr(event, "user_id", 0)), _gid(event)) in ("tech", "admin")


# ============================ 采集钩子 ============================
recorder = on_message(priority=2, block=False)


@recorder.handle()
async def _observe(event: MessageEvent) -> None:
    if not slang.load_conf().get("enabled", True):
        return
    try:
        scope = _scope(event)
        text = (event.get_plaintext() or "").strip()
        if text:
            slang.judge_pending(scope, text)        # 结算「上次用的梗有没有人接」
            if slang.scope_allowed(scope):
                name = ""
                sender = getattr(event, "sender", None)
                name = (getattr(sender, "card", "") or getattr(sender, "nickname", "") or "")
                slang.record_message(scope, getattr(event, "user_id", 0), name, text)
                if slang.should_learn(scope):
                    asyncio.create_task(slang.maybe_learn(scope))
    except Exception as exc:                        # noqa: BLE001
        logger.debug(f"[slang] 采集失败：{exc}")


# ============================ 查看 ============================
cmd_list = on_command("小幽黑话", aliases={"黑话", "黑话库", "黑话列表"}, priority=5, block=True)


@cmd_list.handle()
async def _list(event: MessageEvent) -> None:
    scope = _scope(event)
    head = "这个群在用的词" if _gid(event) else "我学到的词"
    await cmd_list.finish(f"{head}\n{slang.render_list(scope, limit=10)}")


cmd_peek = on_command("小幽黑话预览", aliases={"黑话预览", "注入预览"},
                      priority=5, block=True)


@cmd_peek.handle()
async def _peek(event: MessageEvent) -> None:
    """把「此刻会追加到系统提示里的那段」原样打出来，方便核对。"""
    block = slang.build_inject_block(_scope(event))
    await cmd_peek.finish(block or "这个会话现在没有可注入的词")


cmd_status = on_command("小幽黑话状态", aliases={"黑话状态"}, priority=5, block=True)


@cmd_status.handle()
async def _status(event: MessageEvent) -> None:
    if not _allowed(event):
        await cmd_status.finish("这个只有管理员能看")
    await cmd_status.finish(slang.render_status())


# ============================ 立刻学一轮 ============================
cmd_learn = on_command("小幽学黑话", aliases={"学黑话"}, priority=5, block=True)


@cmd_learn.handle()
async def _learn(event: MessageEvent) -> None:
    if not _allowed(event):
        await cmd_learn.finish("这个只有管理员能用")
    scope = _scope(event)
    if not slang.scope_allowed(scope):
        await cmd_learn.finish("这个会话不在采集名单里")
    if not slang.STATE.buffers.get(scope):
        await cmd_learn.finish("这个会话还没攒到聊天记录 先在群里聊几句")
    result = await slang.learn_once(scope)
    if result.get("reason") == "empty":
        await cmd_learn.finish("缓冲是空的 再聊几句")
    if result.get("reason") == "llm-error":
        await cmd_learn.finish("抽词这步失败了 稍后再试")
    words = "、".join(result.get("words") or []) or "没有新词"
    await cmd_learn.finish(f"抽到 {result.get('extracted', 0)} 个候选 过筛 "
                           f"{result.get('kept', 0)} 个 确认 {result.get('confirmed', 0)} 个\n{words}")


# ============================ 增删 ============================
cmd_del = on_command("小幽黑话删", aliases={"黑话删", "小幽黑话删除"}, priority=5, block=True)


@cmd_del.handle()
async def _delete(event: MessageEvent, args: Message = CommandArg()) -> None:
    if not is_admin(int(getattr(event, "user_id", 0))):
        await cmd_del.finish("这个只有管理员能用")
    word = args.extract_plain_text().strip().split()
    if not word:
        await cmd_del.finish("要删哪个 写成「小幽黑话删 yyds」")
    ok = slang.LIB.remove(word[0])
    slang.LIB.flush(force=True)
    await cmd_del.finish(f"删掉了 {word[0]}" if ok else f"词库里没有 {word[0]}")


cmd_add = on_command("小幽黑话加", aliases={"黑话加", "小幽黑话添加"}, priority=5, block=True)


@cmd_add.handle()
async def _add(event: MessageEvent, args: Message = CommandArg()) -> None:
    if not is_admin(int(getattr(event, "user_id", 0))):
        await cmd_add.finish("这个只有管理员能用")
    parts = [p for p in args.extract_plain_text().replace("+", " ").split() if p]
    if len(parts) < 2:
        await cmd_add.finish("写成「小幽黑话加 词 含义」")
    word, meaning = parts[0], " ".join(parts[1:])
    scope = _scope(event) if _gid(event) else None
    slang.LIB.upsert(word, {"status": slang.CONFIRMED, "meaning": meaning,
                            "source": "manual"}, scope=scope, count_inc=1)
    slang.LIB.flush(force=True)
    await cmd_add.finish(f"记住了 {word}：{meaning}")


# ============================ 启动 ============================
driver = get_driver()


@driver.on_startup
async def _boot() -> None:
    try:
        slang.boot()
    except Exception as exc:                        # noqa: BLE001
        logger.warning(f"[slang] 启动初始化失败（不影响聊天）：{exc}")
