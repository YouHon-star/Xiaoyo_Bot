"""权限档位（被 help / 管理指令 / 闭源插件共用）。

数据源：``storage/mai_modify.json``（档位名单）+ ``storage/permissions.json``（admin_list），结构::

    {
      "allow":       [QQ...],   # 基础白名单
      "tech":        [QQ...],   # 高级白名单
      "deny":        [QQ...],   # 黑名单
      "tech_groups": [群号...]  # 高级功能可用群；不在表里的群一律降级
    }

档位优先级：deny > admin（主人 + admin_list）> tech > allow > none。
群维度只影响 tech/admin：群未授权时降级成 basic，私聊不受限。
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "storage" / "mai_modify.json"   # 档位名单（allow / tech / deny / tech_groups）
PERM_FILE = BASE_DIR / "storage" / "permissions.json"  # 管理员名单（admin_list）
OWNER_QQ = 1923497173

_DEFAULT = {"consent": {}, "allow": [], "tech": [], "deny": [], "tech_groups": []}


def load_data() -> dict:
    if not DATA_FILE.exists():
        return {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _DEFAULT.items()}
    try:
        d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in _DEFAULT.items()}
    for k, v in _DEFAULT.items():
        d.setdefault(k, dict(v) if isinstance(v, dict) else list(v))
    return d


def save_data(d: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def is_admin(uid: int) -> bool:
    if uid == OWNER_QQ:
        return True
    try:
        return uid in json.loads(PERM_FILE.read_text(encoding="utf-8")).get("admin_list", [])
    except (OSError, json.JSONDecodeError):
        return False


def is_owner(uid: int) -> bool:
    """主人唯一：1923497173。群级开关只认他。"""
    return uid == OWNER_QQ


def tier_of(uid: int) -> str:
    """返回 admin / tech / basic / deny / none。"""
    d = load_data()
    if uid in d["deny"]:
        return "deny"
    if is_admin(uid):
        return "admin"
    if uid in d["tech"]:
        return "tech"
    if uid in d["allow"]:
        return "basic"
    return "none"


def group_allowed(gid: int | None) -> bool:
    """None（私聊）不受群限制；群聊必须在 tech_groups 里。"""
    if gid is None:
        return True
    return gid in load_data()["tech_groups"]


def eff_tier(uid: int, gid: int | None = None) -> str:
    """实际生效的档位：管理员默认具备高级身份，但群维度同样生效。"""
    t = tier_of(uid)
    if t == "deny":
        return "deny"
    if t in ("admin", "tech"):
        return "tech" if group_allowed(gid) else "basic"
    return t


def gate_tech(uid: int, gid: int | None = None) -> str:
    """没有高级档就返回提示文案；有则返回空串。"""
    return "" if eff_tier(uid, gid) == "tech" else "抱歉您没有权限"
