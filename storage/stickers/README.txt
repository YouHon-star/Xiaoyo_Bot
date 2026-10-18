小幽表情包 · 素材说明

主素材：**账号在 QQ 里收藏的表情**（表情面板「爱心」那一栏）
  · 由 OneBot `fetch_custom_face` 拉取，缓存在 ../sticker_favorites.json
  · 30 分钟自动重取；自检命令：群里发「小幽表情」

兜底 1：这个目录（storage/stickers/）里的本地图片
  · 支持 jpg / jpeg / png / gif / webp / bmp
  · 丢进来就生效，不用重启

兜底 2：群友发过的表情（storage/sticker_cache.json，环形 300 条）

触发：每条群消息 2%，同群最短间隔 180 秒
      想调数值改 xiaoyo_bot/plugins/sticker.py 顶部的 PROB / COOLDOWN
