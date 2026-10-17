import re
from textwrap import dedent

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, Depends

from ..config import log
from ..core.database.qq import User
from ..core.handler import (
    draw_best50,
    draw_best50_data,
    draw_play_data,
    draw_song_galobal_data,
    get_best50,
    get_player_result,
)
from ..core.image.tools import text_to_bytes_io
from ..core.merge.models import Best50, ServiceName
from ..core.service import mai
from ..core.utils.calc import compute_rating
from .depend import GetUserAndAuth

best50 = on_command("b50", aliases={"B50"})
ap50 = on_command("ap50", aliases={"AP50"})
info = on_command("info", aliases={"minfo", "Minfo", "MINFO", "info", "Info", "INFO"})
ginfo = on_command("ginfo", aliases={"ginfo", "Ginfo", "GINFO"})
score = on_command("分数线")
fit50 = on_command(
    "拟合50", aliases={"拟50", "拟b50", "拟合b50", "拟合B50", "fit50", "Fit50"}
)


@best50.handle()
@ap50.handle()
async def _(
    matcher: Matcher,
    message: Message = CommandArg(),
    user: User = Depends(GetUserAndAuth),
):
    username = message.extract_plain_text().strip()
    is_ap = isinstance(matcher, ap50)
    await matcher.send("正在绘制中，请稍候…", reply_message=True)

    if is_ap and user.service == ServiceName.DIVINGFISH:
        # 水鱼没有原生 AP50 接口：拉取全部成绩 → 只保留 AP/AP+ → 重算 All Perfect 50
        player, _ = await get_best50(user, username=username)
        play_result = await get_player_result(user)
        best50 = _ap_best50_from_records(play_result)
        if not best50.sd and not best50.dx:
            await matcher.finish("你还没有 AP / AP+ 的成绩", reply_message=True)
        player.rating = best50.sd_total + best50.dx_total
        result = await draw_best50_data(user, player, best50, message=AP50_MESSAGE)
    else:
        result = await draw_best50(user, username=username, all_perfect=is_ap)

    await matcher.send(result, reply_message=True)


@info.handle()
async def _(
    message: Message = CommandArg(),
    user: User = Depends(GetUserAndAuth),
):
    data = message.extract_plain_text().strip()
    if not data:
        await info.finish("请输入曲目id或曲名", reply_message=True)

    if data.isdigit() and (by_id := mai.total_list.by_id(int(data))):
        song = by_id
    elif by_t := mai.total_list.by_name(data):
        song = by_t
    else:
        aliases = mai.total_alias_list.by_alias(data)
        if not aliases:
            await info.finish("未找到曲目", reply_message=True)
        elif len(aliases) != 1:
            msg = "找到相同别名的曲目，请使用以下ID查询：\n"
            for alias in aliases:
                msg += f"{alias.song_id}：{alias.alias[0]}\n"
            await info.finish(msg.strip(), reply_message=True)
        else:
            song_id = aliases[0].song_id
        song = mai.total_list.by_id(song_id)
        if song is None:
            await info.finish("未找到曲目", reply_message=True)

    await info.send("正在绘制中，请稍候…", reply_message=True)
    result = await draw_play_data(user, song)
    await info.send(result, reply_message=True)


@ginfo.handle()
async def _(message: Message = CommandArg()):
    args = message.extract_plain_text().strip()
    if not args:
        await ginfo.finish("请输入曲目id或曲名", reply_message=True)

    if args[0] not in "绿黄红紫白":
        level_index = 3
    else:
        level_index = "绿黄红紫白".index(args[0])
        args = args[1:].strip()
        if not args:
            await ginfo.finish("请输入曲目id或曲名", reply_message=True)
    if args.isdigit() and (by_id := mai.total_list.by_id(int(args))):
        song = by_id
    elif by_t := mai.total_list.by_name(args):
        song = by_t
    else:
        alias = mai.total_alias_list.by_alias(args)
        if not alias:
            await ginfo.finish("未找到曲目", reply_message=True)
        elif len(alias) != 1:
            msg = "找到相同别名的曲目，请使用以下ID查询：\n"
            for songs in alias:
                msg += f"{songs.song_id}：{songs.alias[0]}\n"
            await ginfo.finish(msg.strip(), reply_message=True)
        else:
            song = mai.total_list.by_id(alias[0].song_id)
    if level_index >= len(song.difficulties):
        await ginfo.finish("该乐曲没有这个等级", reply_message=True)
    stats = song.difficulties[level_index].stats
    if stats is None:
        await ginfo.finish("该等级没有统计信息", reply_message=True)

    await ginfo.send("正在绘制中，请稍候…", reply_message=True)
    data = await draw_song_galobal_data(song, level_index) + dedent(f"""\
        游玩次数：{round(stats.cnt)}
        拟合难度：{stats.fit_diff:.2f}
        平均达成率：{stats.avg:.2f}%
        平均 DX 分数：{stats.avg_dx:.1f}
        谱面成绩标准差：{stats.std_dev:.2f}""")
    await ginfo.send(data, reply_message=True)


@score.handle()
async def _(message: Message = CommandArg()):
    _args = message.extract_plain_text().strip()
    args = _args.split()
    if args and args[0] == "帮助":
        msg = dedent("""\
            此功能为查找某首歌分数线设计。
            命令格式：分数线「难度+歌曲id」「分数线」
            例如：分数线 紫799 100
            命令将返回分数线允许的「TAP」「GREAT」容错，
            以及「BREAK」50落等价的「TAP」「GREAT」数。
            以下为「TAP」「GREAT」的对应表：
                    GREAT / GOOD / MISS
            TAP         1 / 2.5  / 5
            HOLD        2 / 5    / 10
            SLIDE       3 / 7.5  / 15
            TOUCH       1 / 2.5  / 5
            BREAK       5 / 12.5 / 25 (外加200落)
        """).strip()
        await score.finish(
            MessageSegment.image(text_to_bytes_io(msg)), reply_message=True
        )
    else:
        try:
            result = re.search(r"([绿黄红紫白])\s?([0-9]+)", _args)
            level_labels = ["绿", "黄", "红", "紫", "白"]
            level_labels2 = ["Basic", "Advanced", "Expert", "Master", "Re:MASTER"]
            level_index = level_labels.index(result.group(1))
            chart_id = int(result.group(2))
            line = float(args[-1])
            song = mai.total_list.by_id(chart_id)
            if song is None or level_index >= len(song.difficulties):
                raise ValueError
            chart = song.difficulties[level_index]
            tap = int(chart.notes.tap)
            slide = int(chart.notes.slide)
            hold = int(chart.notes.hold)
            touch = int(chart.notes.touch)
            brk = int(chart.notes.brk)
            total_score = (
                tap * 500 + slide * 1500 + hold * 1000 + touch * 500 + brk * 2500
            )
            break_bonus = 0.01 / brk
            break_50_reduce = total_score * break_bonus / 4
            reduce = 101 - line
            if reduce <= 0 or reduce >= 101:
                raise ValueError
            msg = (
                f"{song.song_name}「{level_labels2[level_index]}」\n"
                f"分数线「{line}%」\n允许的最多「TAP」「GREAT」数量为\n"
                f"「{(total_score * reduce / 10000):.2f}」(每个-{10000 / total_score:.4f}%),\n"
                f"「BREAK」50落(一共「{brk}」个)\n"
                f"等价于「{(break_50_reduce / 100):.3f}」个「TAP」"
                f"「GREAT」(-{break_50_reduce / total_score * 100:.4f}%)"
            )
            await score.finish(msg, reply_message=True)
        except (AttributeError, ValueError) as e:
            log.exception(e)
            await score.finish(
                "格式错误，输入“分数线 帮助”以查看帮助信息", reply_message=True
            )


# ==================== 拟合50 ====================
# 用社区「拟合定数」代替官方定数重算 Best50：
#   单曲 Rating = floor(拟合定数 × min(100.5, 达成率)/100 × 档位系数)
#   sd / dx 两部分各自按新 Rating 重排，总分取重排后的和。
# 拟合定数来源：水鱼 chart_stats 的 fit_diff 字段，随曲目数据一并缓存在本地。
# 说明：默认「精确版」会拉取用户的全部成绩，逐条按拟合定数重算后取 Top N，
#       结果最贴近真实；若全量成绩获取失败则回退到「近似版」（仅重排官方 Top N）。

FIT50_MESSAGE = (
    "※ 本图按「拟合定数」近似重算（社区拟合值，非官方定数），仅供推分参考。\n"
    "可使用「主题」指令更换主题，「数据源」指令更换指定查分器。"
)

FIT50_PRECISE_MESSAGE = (
    "※ 本图按「拟合定数」从你的全部成绩精确重算 Best50"
    "（社区拟合值，非官方定数），仅供推分参考。\n"
    "可使用「主题」指令更换主题，「数据源」指令更换指定查分器。"
)

AP50_MESSAGE = (
    "※ 本图仅统计 AP / AP+ 的成绩（All Perfect 50），使用官方定数计算。\n"
    "可使用「主题」指令更换主题，「数据源」指令更换指定查分器。"
)


def _fit_diff(song_id: int, level_index: int) -> float | None:
    """
    获取某谱面的拟合定数

    Params:
        `song_id`: 曲目 ID
        `level_index`: 难度下标（0=Basic ~ 4=Re:MASTER）
    Returns:
        `float | None`: 拟合定数；取不到时返回 None
    """
    song = mai.total_list.by_id(song_id)
    if song is None or level_index >= len(song.difficulties):
        return None
    stats = song.difficulties[level_index].stats
    if stats is None or not stats.fit_diff:
        return None
    return float(stats.fit_diff)


def _apply_fit_constants(best50) -> int:
    """
    就地把 Best50 的官方定数替换为拟合定数并重算总分

    Params:
        `best50`: `Best50` 数据（会被就地修改）
    Returns:
        `int`: 拟合后的总 Rating
    """
    for part in ("sd", "dx"):
        items = getattr(best50, part)
        for item in items:
            fit = _fit_diff(item.song_id, int(item.level_index))
            if fit is None:
                continue  # 无拟合定数的谱面保留官方定数
            # 先取到与官方定数相同的精度（1 位小数），再据此算 Rating，
            # 保证图上显示的定数与单曲分数自洽（否则会差 1 分左右）
            item.level_value = round(fit, 1)
            item.rating = compute_rating(item.level_value, item.achievements)
        items.sort(key=lambda x: x.rating, reverse=True)
        setattr(best50, f"{part}_total", sum(x.rating for x in items))
    return best50.sd_total + best50.dx_total


# ==================== 全量成绩重算 Best50 ====================
# 精确版拟合50 / 水鱼 AP50 都基于「全部成绩」而非官方 Top N，因此：
#   - 拟合50：把每条成绩的官方定数换成拟合定数后重算 Rating，再各取 Top N；
#   - AP50  ：只保留 AP/AP+ 的成绩，按官方定数算 Rating，再各取 Top N。
# N 取 35 / 15，与官方 Best50（B35 旧曲 + B15 新曲）结构一致。

B50_SD_N = 35
B50_DX_N = 15


def _split_b50_records(play_result) -> tuple[list, list]:
    """
    把成绩拆成「旧曲 Best 35」与「新曲 Best 15」两组

    ⚠️ 划分依据是曲目**是不是当前版本的新曲**（`Song.isnew`），**不是**谱面类型 SD/DX：
       旧版本曲目的 DX 谱面同样归入旧曲组（进 B35）。此点已用官方 b50 实测核对
       ——官方 sd 组 35 条 `isnew` 全为 False（含 31 条 DX 谱面），dx 组 15 条全为 True。

    Params:
        `play_result`: 全部成绩（`list[PlayedResult]`）
    Returns:
        `tuple[list, list]`: (旧曲列表, 新曲列表)
    """
    sd: list = []
    dx: list = []
    for v in play_result:
        if v.song_id >= 100000:  # 宴会场曲目不参与 Best50
            continue
        if v.achievements <= 0:  # 未游玩
            continue
        song = mai.total_list.by_id(v.song_id)
        if song is None:
            continue
        (dx if song.isnew else sd).append(v)
    return sd, dx


def _is_ap_score(score) -> bool:
    """判断一条成绩是否为 AP / AP+"""
    fc = score.fc.value if hasattr(score.fc, "value") else score.fc
    return fc in ("ap", "app")


def _fit_constants_from_records(best50: Best50, play_result) -> int:
    """
    精确版拟合50：用全部成绩按拟合定数重算 Best50（就地写入 best50）

    Params:
        `best50`: 待填充的 `Best50`（会被就地修改）
        `play_result`: 全部成绩
    Returns:
        `int`: 拟合后的总 Rating
    """
    sd, dx = _split_b50_records(play_result)
    for v in sd + dx:
        fit = _fit_diff(v.song_id, int(v.level_index))
        if fit is None:
            continue  # 无拟合定数（如新曲）保留官方定数与官方 Rating
        # 先取到与官方定数相同的精度（1 位小数）再算 Rating，保证图上「定数 -> 分数」自洽
        v.level_value = round(fit, 1)
        v.rating = compute_rating(v.level_value, v.achievements)
    sd.sort(key=lambda x: x.rating, reverse=True)
    dx.sort(key=lambda x: x.rating, reverse=True)
    best50.sd = sd[:B50_SD_N]
    best50.dx = dx[:B50_DX_N]
    best50.sd_total = sum(x.rating for x in best50.sd)
    best50.dx_total = sum(x.rating for x in best50.dx)
    return best50.sd_total + best50.dx_total


def _ap_best50_from_records(play_result) -> Best50:
    """
    水鱼 AP50：只保留 AP / AP+ 的成绩，按官方定数重算 All Perfect 50

    Params:
        `play_result`: 全部成绩
    Returns:
        `Best50`: All Perfect 50 数据
    """
    ap_scores = [v for v in play_result if _is_ap_score(v)]
    sd, dx = _split_b50_records(ap_scores)
    for v in sd + dx:
        v.rating = compute_rating(v.level_value, v.achievements)
    sd.sort(key=lambda x: x.rating, reverse=True)
    dx.sort(key=lambda x: x.rating, reverse=True)
    sd, dx = sd[:B50_SD_N], dx[:B50_DX_N]
    return Best50(
        sd_total=sum(x.rating for x in sd),
        dx_total=sum(x.rating for x in dx),
        sd=sd,
        dx=dx,
    )


@fit50.handle()
async def _(
    matcher: Matcher,
    message: Message = CommandArg(),
    user: User = Depends(GetUserAndAuth),
):
    username = message.extract_plain_text().strip()
    await matcher.send("正在绘制中，请稍候…", reply_message=True)

    player, best50 = await get_best50(user, username=username)

    try:
        play_result = await get_player_result(user)
    except Exception as e:  # 全量成绩获取失败 → 回退近似版
        log.warning(f"拟合50 全量成绩获取失败，回退近似版: {e!r}")
        play_result = None

    if play_result:
        player.rating = _fit_constants_from_records(best50, play_result)
        msg = FIT50_PRECISE_MESSAGE
    else:
        player.rating = _apply_fit_constants(best50)
        msg = FIT50_MESSAGE

    await matcher.send(
        await draw_best50_data(user, player, best50, message=msg),
        reply_message=True,
    )
