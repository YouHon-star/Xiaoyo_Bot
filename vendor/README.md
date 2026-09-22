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
