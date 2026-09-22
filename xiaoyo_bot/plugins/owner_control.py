"""
权限管理插件
功能：
  - 主人/管理员权限验证
  - 用户拉黑/解除拉黑
  - 添加/移除管理员
  - Bot 开关控制
  - 群白名单过滤
  - 查看状态
"""

import asyncio
import json
import re
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, MessageEvent
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="权限管理",
    description="Bot 权限管理系统",
    usage="查看帮助：/权限管理",
)

# ====================== 配置 ======================
OWNER_QQ = 1923497173  # 主人QQ号
DATA_FILE = Path("storage/permissions.json")  # 数据存储文件

# ====================== 状态管理 ======================
bot_enabled = True
black_list: set[int] = set()
admin_list: set[int] = set()
white_group_list: set[int] = set()


# ====================== 数据持久化 ======================
def load_data() -> None:
    """从文件加载数据"""
    global bot_enabled  # noqa: PLW0603
    if not DATA_FILE.exists():
        return

    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    bot_enabled = bool(data.get("enabled", True))
    black_list.clear()
    black_list.update(data.get("black_list", []))
    admin_list.clear()
    admin_list.update(data.get("admin_list", []))
    white_group_list.clear()
    white_group_list.update(data.get("white_group_list", []))


def save_data() -> None:
    """保存数据到文件"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "enabled": bot_enabled,
        "black_list": list(black_list),
        "admin_list": list(admin_list),
        "white_group_list": list(white_group_list),
    }
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# 启动时加载数据
load_data()


# ====================== 权限判断 ======================
def is_owner(user_id: int) -> bool:
    """判断是否为主人"""
    return user_id == OWNER_QQ


def is_admin(user_id: int) -> bool:
    """判断是否为管理员（主人默认有权限）"""
    return is_owner(user_id) or user_id in admin_list


def extract_qq(message: str) -> int | None:
    """从消息中提取QQ号"""
    match = re.search(r"\d{5,12}", message)
    return int(match.group()) if match else None


# ====================== 权限指令 ======================
verify_master = on_command("小幽我是谁", aliases={"我是谁"}, priority=2, block=True)


@verify_master.handle()
async def handle_verify(event: MessageEvent) -> None:
    user_id = event.user_id
    if is_owner(user_id):
        await verify_master.finish("✅ 幽鸿，我会一直陪着您")
    elif is_admin(user_id):
        await verify_master.finish("✅ 您好，管理员大人")
    else:
        await verify_master.finish("❌ 抱歉，您没有权限")


switch_bot = on_command("开关bot", aliases={"开关机器人"}, priority=2, block=True)


@switch_bot.handle()
async def handle_switch(event: MessageEvent) -> None:
    if not is_owner(event.user_id):
        await switch_bot.finish("❌ 权限不足")

    global bot_enabled  # noqa: PLW0603
    bot_enabled = not bot_enabled
    save_data()
    status = "开启" if bot_enabled else "关闭"
    await switch_bot.finish(f"✅ Bot已{status}！")


restart_bot = on_command("重启bot", aliases={"重启"}, priority=2, block=True)


@restart_bot.handle()
async def handle_restart(event: MessageEvent) -> None:
    if not is_owner(event.user_id):
        await restart_bot.finish("❌ 权限不足")

    await restart_bot.send("🔄 小幽重启中...")
    await asyncio.sleep(1)
    save_data()
    raise SystemExit("主人触发重启")


black_add = on_command("拉黑", priority=2, block=True)


@black_add.handle()
async def handle_black_add(event: MessageEvent) -> None:
    if not is_admin(event.user_id):
        await black_add.finish("❌ 权限不足")

    qq = extract_qq(str(event.message))
    if qq is None:
        await black_add.finish("⚠️ 未识别到QQ号\n用法：拉黑 123456789")

    if qq == OWNER_QQ:
        await black_add.finish("❌ 无法拉黑主人")

    black_list.add(qq)
    save_data()
    await black_add.finish(f"✅ 已拉黑用户：{qq}")


black_remove = on_command("解除拉黑", aliases={"取消拉黑"}, priority=2, block=True)


@black_remove.handle()
async def handle_black_remove(event: MessageEvent) -> None:
    if not is_admin(event.user_id):
        await black_remove.finish("❌ 权限不足")

    qq = extract_qq(str(event.message))
    if qq is None:
        await black_remove.finish("⚠️ 未识别到QQ号\n用法：解除拉黑 123456789")

    black_list.discard(qq)
    save_data()
    await black_remove.finish(f"✅ 已解除拉黑：{qq}")


add_admin = on_command("添加管理员", priority=2, block=True)


@add_admin.handle()
async def handle_add_admin(event: MessageEvent) -> None:
    if not is_owner(event.user_id):
        await add_admin.finish("❌ 权限不足")

    qq = extract_qq(str(event.message))
    if qq is None:
        await add_admin.finish("⚠️ 未识别到QQ号\n用法：添加管理员 123456789")

    if qq == OWNER_QQ:
        await add_admin.finish("✅ 主人拥有最高权限")

    if qq in admin_list:
        await add_admin.finish(f"⚠️ {qq} 已经是管理员")

    admin_list.add(qq)
    save_data()
    await add_admin.finish(f"✅ 已添加 {qq} 为管理员")


remove_admin = on_command("移除管理员", priority=2, block=True)


@remove_admin.handle()
async def handle_remove_admin(event: MessageEvent) -> None:
    if not is_owner(event.user_id):
        await remove_admin.finish("❌ 权限不足")

    qq = extract_qq(str(event.message))
    if qq is None:
        await remove_admin.finish("⚠️ 未识别到QQ号\n用法：移除管理员 123456789")

    if qq not in admin_list:
        await remove_admin.finish(f"❌ {qq} 不是管理员")

    admin_list.discard(qq)
    save_data()
    await remove_admin.finish(f"✅ 已移除 {qq} 的管理员权限")


view_admin = on_command("查看管理", aliases={"管理员列表"}, priority=2, block=True)


@view_admin.handle()
async def handle_view_admin() -> None:
    if admin_list:
        admins = "\n".join(f"  • {qq}" for qq in sorted(admin_list))
        msg = f"📋 管理员列表\n👑 主人：{OWNER_QQ}\n👮 管理：\n{admins}"
    else:
        msg = f"📋 管理员列表\n👑 主人：{OWNER_QQ}\n👮 管理：暂无"
    await view_admin.finish(msg)


bot_status = on_command("小幽状态", aliases={"状态"}, priority=2, block=True)


@bot_status.handle()
async def handle_status(event: MessageEvent) -> None:
    if not is_admin(event.user_id):
        await bot_status.finish("❌ 权限不足")

    status_icon = "🟢" if bot_enabled else "🔴"
    ws_groups = len(white_group_list)
    await bot_status.finish(
        f"📊 小幽状态\n"
        f"状态：{status_icon} {'运行中' if bot_enabled else '已关闭'}\n"
        f"主人：{OWNER_QQ}\n"
        f"管理员：{len(admin_list)}人\n"
        f"白名单群：{ws_groups}个\n"
        f"黑名单：{len(black_list)}人"
    )


view_black = on_command("查看黑名单", priority=2, block=True)


@view_black.handle()
async def handle_view_black(event: MessageEvent) -> None:
    if not is_admin(event.user_id):
        await view_black.finish("❌ 权限不足")

    if black_list:
        users = "\n".join(f"  • {qq}" for qq in sorted(black_list))
        msg = f"🚫 黑名单\n{users}"
    else:
        msg = "✅ 黑名单为空"
    await view_black.finish(msg)


# ====================== 群白名单 ======================
add_white_group = on_command("添加白名单", priority=2, block=True)


@add_white_group.handle()
async def handle_add_white(event: MessageEvent) -> None:
    if not is_admin(event.user_id):
        await add_white_group.finish("❌ 权限不足")

    gid = extract_qq(str(event.message))
    if gid is None:
        await add_white_group.finish("⚠️ 未识别到群号\n用法：添加白名单 123456789")

    if gid in white_group_list:
        await add_white_group.finish(f"⚠️ 群 {gid} 已在白名单")

    white_group_list.add(gid)
    save_data()
    await add_white_group.finish(f"✅ 已添加群 {gid} 到白名单")


remove_white_group = on_command(
    "删除白名单", aliases={"移除白名单"}, priority=2, block=True
)


@remove_white_group.handle()
async def handle_remove_white(event: MessageEvent) -> None:
    if not is_admin(event.user_id):
        await remove_white_group.finish("❌ 权限不足")

    gid = extract_qq(str(event.message))
    if gid is None:
        await remove_white_group.finish("⚠️ 未识别到群号\n用法：删除白名单 123456789")

    if gid not in white_group_list:
        await remove_white_group.finish(f"❌ 群 {gid} 不在白名单")

    white_group_list.discard(gid)
    save_data()
    await remove_white_group.finish(f"✅ 已从白名单移除群 {gid}")


view_white_group = on_command(
    "查看白名单", aliases={"群白名单"}, priority=2, block=True
)


@view_white_group.handle()
async def handle_view_white(event: MessageEvent) -> None:
    if not is_admin(event.user_id):
        await view_white_group.finish("❌ 权限不足")

    if white_group_list:
        groups = "\n".join(f"  • {gid}" for gid in sorted(white_group_list))
        msg = f"📋 群白名单（共{len(white_group_list)}个）\n{groups}"
    else:
        msg = "⚠️ 白名单为空，所有群均可使用"
    await view_white_group.finish(msg)


# ====================== 消息拦截 ======================
@event_preprocessor
async def message_guard(event: Event) -> None:
    """全局消息拦截器"""
    if not isinstance(event, MessageEvent):
        return

    # 管理员以上直接放行
    if is_admin(event.user_id):
        return

    # 黑名单拦截
    if event.user_id in black_list:
        raise IgnoredException("黑名单用户")

    # 群白名单过滤：仅允许白名单内的群
    if (
        white_group_list
        and isinstance(event, GroupMessageEvent)
        and event.group_id not in white_group_list
    ):
        raise IgnoredException("群不在白名单")

    # Bot关闭时拦截
    if not bot_enabled:
        raise IgnoredException("Bot已关闭")
