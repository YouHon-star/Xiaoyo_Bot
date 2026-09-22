"""机厅报数插件

用法：
  小幽添加机厅+机厅名称   添加一个机厅（所有人可用）
  机厅名称+人数          报数，例：环游嘉年华+5
  机厅名称+j             查询当前人数，例：环游嘉年华+j
  小幽机厅列表           查看所有机厅及人数
  小幽删除机厅+机厅名称   删除机厅（管理员/主人）

也可以不带分隔符直接连写：
  报数：机厅名称人数，例：sb2（sb 机厅报 2 人）
  查询：机厅名称j，例：sbj（查询 sb 机厅人数）

每天 0 点自动清空所有机厅人数（机厅保留）。
"""
#我说ai真好用吧孩子们
import json
import re
from datetime import datetime
from pathlib import Path

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.plugin import PluginMetadata
from nonebot_plugin_apscheduler import scheduler

__plugin_meta__ = PluginMetadata(
    name="机厅报数",
    description="机厅人数报数/查询，每天0点自动清空",
    usage=(
        "小幽添加机厅+机厅名称 | "
        "机厅名称+人数 | "
        "机厅名称+j | "
        "小幽机厅列表 | "
        "小幽删除机厅+机厅名称"
    ),
)

# ====================== 配置 ======================
DATA_FILE = Path("storage/arcade_data.json")
PERM_FILE = Path("storage/permissions.json")
OWNER_QQ = 1923497173  # 与 owner_control.py 保持一致
MAX_COUNT = 9999  # 人数上限

# 报数 / 查询 消息匹配：名称+数字 或 名称+j（带分隔符，兼容旧格式）
_ACTION_RE = re.compile(r"(.+?)\s*[+＋加]\s*(\d{1,4}|[jJｊ])\s*$")
# 无分隔符格式：名称后直接跟 数字（报数）或 j（查询）
_NO_SEP_REPORT_RE = re.compile(r"^(\d{1,4})$")
_NO_SEP_QUERY_RE = re.compile(r"^[jJｊ]$")


# ====================== 数据持久化 ======================
def load_data() -> dict:
    """读取机厅数据"""
    if not DATA_FILE.exists():
        return {"arcades": {}}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"arcades": {}}


def save_data(data: dict) -> None:
    """保存机厅数据"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_arcades() -> dict:
    """返回 {名称: {count, added_by, updated_by, updated_at}}"""
    return load_data().get("arcades", {})


def is_admin_user(user_id: int) -> bool:
    """判断管理员/主人（读取 permissions.json，与 owner_control 保持一致）"""
    if user_id == OWNER_QQ:
        return True
    try:
        perms = json.loads(PERM_FILE.read_text(encoding="utf-8"))
        return user_id in perms.get("admin_list", [])
    except (OSError, json.JSONDecodeError):
        return False


def now_str() -> str:
    """当前时间 HH:MM"""
    return datetime.now().strftime("%H:%M")


# ====================== 添加机厅 ======================
add_arcade = on_command("小幽添加机厅", aliases={"添加机厅"}, priority=5, block=True)


@add_arcade.handle()
async def handle_add_arcade(event: MessageEvent) -> None:
    raw = event.get_plaintext().strip()
    for cmd in ("小幽添加机厅", "添加机厅"):
        if raw.startswith(cmd):
            raw = raw[len(cmd):].strip()
            break

    # 去掉开头的分隔符（+ ＋ 加 空格）
    name = raw.lstrip("+＋加 ").strip()

    if not name:
        await add_arcade.finish(
            "❌ 请带上机厅名称\n\n用法：小幽添加机厅+机厅名称\n"
            "例：小幽添加机厅+环游嘉年华"
        )

    if len(name) > 20:
        await add_arcade.finish("❌ 机厅名称太长啦（最多20字）")

    arcades = get_arcades()
    if name in arcades:
        await add_arcade.finish(f"⚠️ 机厅「{name}」已经存在啦\n当前人数：{arcades[name]['count']}")

    arcades[name] = {
        "count": 0,
        "added_by": event.user_id,
        "updated_by": 0,
        "updated_at": "",
    }
    save_data({"arcades": arcades})

    await add_arcade.finish(
        f"✅ 机厅「{name}」添加成功！\n\n"
        f"报数：发送「{name}+人数」\n"
        f"查询：发送「{name}+j」\n"
        f"当前共 {len(arcades)} 个机厅"
    )


# ====================== 删除机厅 ======================
del_arcade = on_command("小幽删除机厅", aliases={"删除机厅"}, priority=5, block=True)


@del_arcade.handle()
async def handle_del_arcade(event: MessageEvent) -> None:
    if not is_admin_user(event.user_id):
        await del_arcade.finish("❌ 权限不足（仅管理员/主人可删除机厅）")

    raw = event.get_plaintext().strip()
    for cmd in ("小幽删除机厅", "删除机厅"):
        if raw.startswith(cmd):
            raw = raw[len(cmd):].strip()
            break

    name = raw.lstrip("+＋加 ").strip()
    if not name:
        await del_arcade.finish("❌ 请带上机厅名称\n\n用法：小幽删除机厅+机厅名称")

    arcades = get_arcades()
    if name not in arcades:
        await del_arcade.finish(f"❌ 机厅「{name}」不存在")

    del arcades[name]
    save_data({"arcades": arcades})
    await del_arcade.finish(f"✅ 已删除机厅「{name}」，剩余 {len(arcades)} 个机厅")


# ====================== 机厅列表 ======================
list_arcade = on_command("小幽机厅列表", aliases={"机厅列表"}, priority=5, block=True)


@list_arcade.handle()
async def handle_list_arcade() -> None:
    arcades = get_arcades()
    if not arcades:
        await list_arcade.finish("还没有机厅哦\n\n用「小幽添加机厅+机厅名称」添加第一个吧！")

    lines = []
    for name, info in sorted(arcades.items()):
        count = info["count"]
        if count > 0:
            lines.append(f"  {name}：{count}人")
        else:
            lines.append(f"  {name}：--")
    await list_arcade.finish(f"📋 机厅列表（共{len(arcades)}个）\n" + "\n".join(lines))


# ====================== 报数 / 查询（裸消息） ======================
def _parse_action(text: str) -> tuple[str, str, int | None] | None:
    """解析裸消息，返回 (机厅名, action, 人数)，action 为 'report' 或 'query'；不匹配返回 None

    支持两种格式：
      1. 带分隔符：机厅名+数字 / 机厅名+j（例：环游嘉年华+5、环游嘉年华+j）
      2. 无分隔符：机厅名数字 / 机厅名j（例：sb2、sbj）
    """
    raw = text.strip()
    if not raw:
        return None
    arcades = get_arcades()

    # ---- 格式1：带分隔符 ----
    m = _ACTION_RE.match(raw)
    if m:
        name = m.group(1).strip()
        arg = m.group(2)
        if name in arcades:
            if arg.lower() in ("j", "ｊ"):
                return (name, "query", None)
            return (name, "report", int(arg))

    # ---- 格式2：无分隔符（机厅名 + 数字/j） ----
    # 按机厅名长度从长到短匹配，避免短名吞掉长名（如 "sb" vs "sb2店"）
    for name in sorted(arcades, key=len, reverse=True):
        if not raw.startswith(name):
            continue
        rest = raw[len(name):]
        if not rest:
            continue  # 消息恰好等于机厅名，无动作
        if _NO_SEP_QUERY_RE.match(rest):
            return (name, "query", None)
        if _NO_SEP_REPORT_RE.match(rest):
            return (name, "report", int(rest))

    return None


async def arcade_msg_rule(event: MessageEvent) -> bool:
    """仅当消息是「已有机厅名+数字/j」时才拦截处理，避免误伤普通聊天"""
    text = event.get_plaintext().strip()
    return _parse_action(text) is not None


arcade_msg = on_message(rule=arcade_msg_rule, priority=10, block=True)


@arcade_msg.handle()
async def handle_arcade_msg(event: MessageEvent) -> None:
    text = event.get_plaintext().strip()
    parsed = _parse_action(text)
    if parsed is None:  # 保险：rule 已校验，正常不会走到
        return
    name, action, count = parsed
    arcades = get_arcades()

    if action == "query":
        info = arcades[name]
        count = info["count"]
        if count > 0:
            at = f"（最近更新 {info['updated_at']}）" if info.get("updated_at") else ""
            await arcade_msg.finish(f"机厅「{name}」当前人数：{count}人 {at}")
        await arcade_msg.finish(f"机厅「{name}」目前还没有报数哦")
        return

    # action == "report"
    assert count is not None
    count = max(0, min(count, MAX_COUNT))
    arcades[name].update(
        {"count": count, "updated_by": event.user_id, "updated_at": now_str()}
    )
    save_data({"arcades": arcades})

    if count == 0:
        await arcade_msg.finish(f" 机厅「{name}」人数已清空（0人）")
    await arcade_msg.finish(f"✅ 报数成功！机厅「{name}」当前人数：{count}人")


# ====================== 每天0点清空人数 ======================
@scheduler.scheduled_job(
    "cron", hour=0, minute=0, id="arcade_daily_reset", timezone="Asia/Shanghai"
)
async def daily_reset() -> None:
    """每天 0 点清空所有机厅人数（保留机厅）"""
    data = load_data()
    arcades = data.get("arcades", {})
    if not arcades:
        return
    for info in arcades.values():
        info["count"] = 0
        info["updated_by"] = 0
        info["updated_at"] = ""
    save_data(data)
