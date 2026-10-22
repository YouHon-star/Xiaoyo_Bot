# 小幽 Bot

一个跑在 QQ 上的自用机器人：**NoneBot2 + OneBot v11（NapCat）**，以「小幽」这个人设出现在群里——
能聊天、能发疯、能查分、能陪着打音游。

> **关于舞萌账号数据类功能**
> 小幽的账号数据读取、成绩查询（b50 / PC50）与数据写入能力**已经实现，但源码不开源**（闭源）。
> 仓库只保留聊天、群娱乐、图片渲染、权限与运维这些通用部分，
> 不含任何协议层、登录凭证处理或写数据逻辑。
>
> 想了解或使用那部分功能：**先加 bot 好友**（QQ `2889487322`），私聊说明用途，
> 由主人评估后授权。

---

## 功能一览

| 模块 | 能干什么 |
|---|---|
| 💬 **聊天** | GLM-4-Flash 驱动的人设对话、看图说话、复读、随机选择、表情包随机发 |
| 📊 **查询** | 权限自查、bot 状态、机厅报数；b50 / PC50 出图由闭源模块提供 |
| 🎲 **群娱乐** | 牛牛大作战、猜歌、点歌、视频链接解析、投喂图 |
| 🛠 **运维** | 权限 / 黑名单 / 白名单、开关 bot、远程重启、状态查询 |

## 命令速查

完整说明见 **[docs/USAGE.md](docs/USAGE.md)**。

```
# 查询
小幽我是谁                      # 看自己的 QQ 与权限档
小幽状态                        # bot 与队列状态
小幽机厅列表

# 娱乐
小幽随机+吃饭+睡觉+打舞萌
小幽获取歌曲+歌名
ccb  /  查看牛牛  /  查看排行
小幽帮助

# 管理（管理员 / 主人）
查看管理        添加管理员+@某人     移除管理员+@某人
拉黑+@某人      解除拉黑+@某人       开关bot
```

## 目录结构

```
xiaoyo_bot/
  perms.py                 权限档位（admin / tech / basic / deny / none）
  plugins/                 插件（业务全部在这里）
    help.py                图片版帮助
    owner_control.py       权限 / 黑名单 / 重启
    sticker.py niuniu.py repeat.py roll.py vision.py get_song.py vip.py …
vendor/                    第三方插件源码（nonebot_plugin_maimaidx，曲绘与 rating 渲染）
storage/                   运行数据（大部分不入库，见 .gitignore）
  maimai/                  曲绘 / rating 表 / 字体等静态资源（约 593M，**不入库**，见下）
server/ NeteaseCloudMusicApi/   点歌用的本地网易云 API（监听 127.0.0.1:3000）
docs/                      使用说明与架构说明
```

### 静态资源（`storage/maimai/`）

曲绘（`mai/cover/` 392M）、rating 表、plate 表、字体合计约 **593M**，因此**不进版本库**——
仓库本体只有几 MB。首次部署需要自己准备这份资源（二选一）：

1. 从你自己的备份里恢复，例如 `git bundle` / 旧的完整仓库副本；
2. 从 `vendor/nonebot_plugin_maimaidx` 对应的上游资源包解压到 `storage/maimai/`。

目录结构要对上：

```
storage/maimai/
├── mai/{cover,rating_table,plate_table,pic,plate_version}/
├── font/{*.ttf,*.otf}
└── data/{music_data.json,music_chart.json,plate_data.json,…}
```

缺了它 bot 能聊天，但曲绘相关功能**出不了图**。

### 闭源模块

账号数据相关能力由**仓库之外的私有模块**提供，通过子进程调用，不随本仓库分发。
未部署时相关命令会提示「依赖未就绪」，不影响其余功能。

## 部署

```bash
git clone https://github.com/YouHon-star/Xiaoyo_Bot.git
cd Xiaoyo_Bot
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e .

# .env 见下表，自己建
nb run                      # 或交给 systemd，见下
```

`.env` 至少要配：

| 变量 | 说明 |
|---|---|
| `ONEBOT_ACCESS_TOKEN` / NapCat 侧配置 | 与 NapCat 的 WebSocket 对接 |
| `LLMCHAT__*` | 人设、模型、温度（温度太低会变成「嗯」「哦」） |
| `DIVINGFISH_TOKEN` | 水鱼查分器开发者 token（只读曲库） |
| `NETEASE_API_URL` | 点歌用，如 `http://127.0.0.1:3000` |

生产环境用 systemd 托管（**不要用 `systemd-run` 起临时单元，重启机器就没了**）：

```ini
# /etc/systemd/system/xiaoyo-nonebot.service
[Unit]
Description=Xiaoyo QQ Bot
After=network-online.target

[Service]
WorkingDirectory=/home/xiaoyo/Xiaoyo_Bot
ExecStart=/home/xiaoyo/Xiaoyo_Bot/.venv/bin/nb run
Environment=HOME=/root
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now xiaoyo-nonebot
journalctl -u xiaoyo-nonebot -f
```

## 安全

- 仓库里不含任何 token / 账号数据：`.env`、`storage/*.json`、登录凭证一律在 `.gitignore` 里。
- 权限分档（`xiaoyo_bot/perms.py`）：`deny` > `admin` > `tech` > `basic` > `none`，
  群维度只影响 `tech` / `admin`，群未授权时自动降级。
- 被拉黑用户的消息在入口就被丢弃。

## 许可

个人自用项目，代码可参考；第三方源码（`vendor/`、`NeteaseCloudMusicApi/`）遵循其各自许可。
