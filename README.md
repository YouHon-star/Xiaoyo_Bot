# XiaoyoBot Project Architecture

xiaoyo_bot/plugins/          # NoneBot 插件目录
├── owner_control.py          # 权限管理 (priority=2)
├── guess_song/               # 猜歌游戏
│   ├── __init__.py           #   游戏主逻辑 (priority=3/4)
│   └── song_data.py          #   歌曲题库数据
├── help.py                   # 帮助信息 (priority=3)
├── get_song.py              # 网易云搜歌下载 (priority=4)
├── vip.py                   # 视频链接解析 (priority=4, 仅私聊)
└── ai_module/               # AI 对话模块 (priority=5)
    ├── __init__.py           #   AI 主逻辑
    ├── chat_context.py       #   上下文记忆管理
    └── system_prompt.txt     #   人设提示词
消息处理优先级链: event_preprocessor(全局) -> 2(权限) -> 3(帮助/猜歌开始) -> 4(搜歌/猜歌答案/视频) -> 5(AI)

