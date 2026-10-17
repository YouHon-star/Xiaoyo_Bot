# vendor/nonebot_plugin_maimaidx

本目录是**舞萌插件源码的备份快照**，包含本地手工补丁。

## 为什么放在这里

插件实际运行时是安装到 `.venv/lib/python3.13/site-packages/nonebot_plugin_maimaidx`
的（`.venv` 被 git 忽略）。但**本地对该插件做了大量手工修改**，那些改动原本
没有任何版本控制 —— `pip install` 升级、服务器重装都会覆盖/丢失。

因此把插件源码快照一份进仓库，作为**补丁的版本备份**。

## 基线版本

- 上游 `nonebot-plugin-maimaidx` **3.0.10**
- 手工合入了上游 **3.0.14** 的部分功能（见下）

## 本地补丁清单

| 补丁 | 位置 | 说明 |
|---|---|---|
| b50 提速 | `core/handler.py` | `ThreadPoolExecutor` 并行取数 |
| b50 提速 | `core/image/tools.py` | `lru_cache` 缓存 |
| diving-fish 域名修复 | `core/divingfish/` | 用 `www.diving-fish.com`（上游的 `maimai.diving-fish.com` 会挂） |
| 定数查歌新语义 | `commands/depend.py` | **两数字 = 定数+页数**（`定数查歌 13 2`）；三数字 = 范围+页数（`12 14 3`） |
| 绘制中提示 | `commands/*.py` | 所有发图指令绘制前先发「正在绘制中，请稍候…」，共 20 处 |
| 容错 | `__init__.py` | 启动流程加 `try/except`，避免单点失败拖垮加载 |
| @查成绩 | `commands/depend.py`、`commands/mai_base.py` | `GetOrCreateSender`（allow_at=False）用于「作用于发送者自己」的指令；查询类走 `GetOrCreateUser`（allow_at=True）自动支持 @ 他人 |
| **拟合50（精确版）** | `commands/mai_score.py`、`core/handler.py` | 新增 `拟合50` 指令（别名 `拟50`/`拟b50`/`拟合b50`/`拟合B50`/`fit50`/`Fit50`）：用社区拟合定数 `fit_diff` 代替官方定数重算 Best50。**默认走精确版**——拉取用户全部成绩逐条重算后各取 Top 35/15；全量成绩获取失败时回退近似版（仅重排官方 Top N）。`handler.py` 抽出 `draw_best50_data` 以便复用绘制 |
| **水鱼 AP50** | `commands/mai_score.py` | `ap50` 原仅支持落雪；现水鱼也可用：拉取全部成绩 → 只保留 AP/AP+ → 按官方定数重算 All Perfect 50（Top 35/15）。落雪仍走原生 `/bests/ap` 接口 |
| **B35/B15 分组依据** | `commands/mai_score.py` | ⚠️ 旧曲/新曲按 **`Song.isnew`**（是否当前版本新曲）划分，**不是**谱面类型 SD/DX —— 旧版本的 DX 谱面属于旧曲组(进 B35)。已用官方 b50 实测核对（官方 sd 组 35 条 `isnew` 全 False 含 31 条 DX 谱面，dx 组 15 条全 True） |
| 定数显示自洽 | `core/image/base.py` | `whiledraw` 的「定数 -> 单曲Rating」改为优先用 `info.level_value`（拟合50 会改写它），否则回退官方定数表。普通 b50 值不变 |

## 绘制中提示的 20 处分布

| 文件 | 处数 |
|---|---|
| `commands/mai_search.py` | 8 |
| `commands/mai_table.py` | 5 |
| `commands/mai_base.py` | 4 |
| `commands/mai_score.py` | 3（含原有 b50/查分） |

刻意**未加**的指令（纯静态图、无渲染耗时）：帮助图、分数线帮助、牌子条件、
今日舞萌、当前投票、猜歌/猜曲绘。

## 使用方法

插件实际从 `.venv` 加载，本目录**不参与运行**，仅作备份与比对。

需要恢复时，把本目录内容复制回：

```bash
cp -r vendor/nonebot_plugin_maimaidx/. \
  .venv/lib/python3.13/site-packages/nonebot_plugin_maimaidx/
```

或做补丁比对：

```bash
diff -ru .venv/lib/python3.13/site-packages/nonebot_plugin_maimaidx \
         vendor/nonebot_plugin_maimaidx
```

## 维护约定

- 每次修改插件后，**同步更新本目录**，否则备份会过期
- 升级上游版本时，注意**不要覆盖**上表列出的补丁
