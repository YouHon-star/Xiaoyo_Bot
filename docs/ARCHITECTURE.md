# 架构说明

一句话：**NoneBot2 负责「QQ 这一侧」**（收发消息、命令解析、排队、权限），
业务插件按 `priority` 依次匹配；涉及账号数据的功能转交仓库之外的私有模块（闭源，子进程调用）。

```
QQ ── OneBot v11 ──▶ NapCat ──ws──▶ NoneBot2 (xiaoyo_bot/plugins)
                                        │
                                        ├─ 即时功能：复读 / 表情 / 随机 / 牛牛 / 聊天 / 看图
                                        │
                                        └─ 账号数据功能 ──subprocess──▶ 私有模块（未随仓库分发）
```

## 1. 插件清单与消息优先级

NoneBot 按 `priority` 数字**从小到大**依次尝试匹配，命中且 `block=True` 就终止后续匹配。

| priority | 插件 | 职责 |
|---|---|---|
| 2 | `owner_control.py` | 权限门禁、黑名单、`开关bot`、`重启bot`、`小幽状态` |
| 3 | `help.py` | `小幽帮助`（图片版） |
| 4 | `get_song.py` / `vip.py` | 点歌下载、视频直链解析（仅私聊） |
| 5 | `niuniu.py` / `roll.py` / `feed.py` / `arcade_count.py` | 牛牛、随机、投喂、机厅报数 |
| 10 | `arcade_count.py`（被动） | 群里「N 人」式报数 |
| 15 | `repeat.py` | 同一句话连发 3 遍 → 跟着复读 |
| 20 | `sticker.py` | 每条群消息 2% 概率发一张收藏表情 |
| 90 / 99 | `vision.py` | 图片 → 客观描述 → 交给 llmchat |
| — | `persona.py` | 只做人设提示词注入，不注册命令 |

`vendor/nonebot_plugin_maimaidx` 提供曲绘与 rating 图片渲染，
静态资源在 `storage/maimai/`（cover / rating_table / plate_table / font …）。

## 2. 权限档位

统一在 `xiaoyo_bot/perms.py`，数据源 `storage/mai_modify.json`（不入库）：

```
deny  >  admin  >  tech  >  basic  >  none
```

- `deny`：黑名单，消息直接丢弃。
- `admin`：主人 + `admin_list`，默认具备 `tech` 能力。
- `tech`：高级白名单。
- `basic`：普通白名单。
- **群维度只影响 `tech` / `admin`**：群不在授权名单 → 自动降级成 `basic`；私聊不受限制。

档位判断集中在这一个模块，插件不各写一套。

## 3. 数据与状态

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `.env` | NapCat / LLM / 查分器 token | 否 |
| `storage/df_tokens.json` | 每位用户自己的查分器 Token | 否 |
| `storage/mai_modify.json` | 权限名单、群授权 | 否 |
| `storage/permissions.json` | 黑名单、管理员、白名单群 | 否 |
| `storage/maimai/` | 曲绘与 rating 静态资源（约 593M） | 否（由部署者自备） |

## 4. 部署要点（踩过的坑）

1. **必须用持久 systemd 单元**。用 `systemd-run` 起的临时单元写在 `/run/systemd/transient/`，
   机器一重启就消失，bot 不会自己回来。
2. **日志交给 journald**。旧做法 `StandardOutput=append:bot.svc.log` 没有任何轮转，
   而用 logrotate `copytruncate` 轮转又会因为 systemd 持有写偏移生成空洞文件。
3. **不要从别的服务的会话里起进程**。这些进程会落在启动者的 cgroup 里，
   上游服务一重启就被连坐 `SIGKILL`。长驻服务一律 `systemctl` 管。
4. 端口：`8080` NoneBot、`3000` 本地网易云 API（点歌用，未启动时点歌不可用）。

```bash
systemctl status xiaoyo-nonebot
journalctl -u xiaoyo-nonebot -f
systemctl restart xiaoyo-nonebot      # 改完插件需要重启才生效
```
