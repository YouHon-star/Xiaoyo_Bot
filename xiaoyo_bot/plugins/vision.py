"""图片识别：把群/私聊里的图片读成一句客观描述，再交给 llmchat 用拟人语气聊天。

## 为什么不用 llmchat 自带的 `support_image`

它把图片拼成 **字符串** 塞进 content：
```python
content += f"\\n[图片:data:image/jpeg;base64,{base64_image}]"
```
GLM-4V 收到这种「文本里的 data URL」是当文字读的，不是当图看。实测同一张
「白底 + 红圆 + MAI 155」，三种方式对比：

| 方式 | 回答 |
|---|---|
| content 数组（本插件走的这条） | 「红色的圆形图案，旁边写着 MAI 155」✅ |
| 字符串内联 data URL（llmchat 那条） | 「由多个小方格组成的网格图案…」❌ 纯幻觉 |
| GLM-4-Flash 纯文本 | 根本看不到图 |

## 做法

1. 命中的消息里挑出 image 段，下载/解码成 base64
2. 调视觉模型（默认 `GLM-4V-Flash`，免费）用 **content 数组** 要一句客观描述
3. 把每个 image 段**就地替换**成文本 `[图片：…描述…]`
4. 之后 llmchat（priority=99）看到的就是带描述的普通消息 → 用同一份人设和历史接话

优先级取 90：排在 repeat(15)/sticker(20) 之后（它们要看到原始图片段），
又排在 llmchat(99) 之前（它要看到描述）。
"""

import base64
import os

import httpx
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.log import logger

VISION_MODEL = os.getenv("VISION_MODEL", "GLM-4V-Flash")
MAX_IMAGES = 3                 # 一条消息最多看几张
MAX_BYTES = 8 * 1024 * 1024    # 单张上限 8MB
TIMEOUT = 25.0

# 只要事实，不要评论 —— 评论交给 llmchat 的人设去说
VISION_PROMPT = (
    "客观描述这张图片：主体是谁/是什么、在做什么、什么场景，"
    "画面里出现的文字请原样抄下来。两句话以内，不要评价、不要建议、不要客套。"
    "直接说内容，不要用「图片中」「图中」「这张图」开头。"
)

FALLBACK = "[图片]"

# 有些模型仍爱加「图片中/图中/这张图片里」的前缀，注入前统一剥掉
# （长的放前面，「这张图片里」必须先于「这张图片」匹配，否则会剩下一个「里」）
_LEAD = ("这张图片里", "这张图里", "图片显示", "图中显示", "图片中", "图片里",
         "画面中", "这张图片", "这张图", "这幅图", "图中")


def clean_desc(text: str) -> str:
    t = (text or "").strip().replace("\n", " ")
    changed = True
    while changed:
        changed = False
        for lead in _LEAD:
            if t.startswith(lead):
                t = t[len(lead):].lstrip("里的有是上：:，, ")
                changed = True
    return t.strip()


# ====================== 纯函数（可离线测） ======================
def image_segments(message) -> list:
    return [seg for seg in message if getattr(seg, "type", None) == "image"]


def image_urls(message) -> list[str]:
    """从图片段里取可用地址（url 优先，其次 base64:// / http 形式的 file）。"""
    out = []
    for seg in image_segments(message):
        d = getattr(seg, "data", {}) or {}
        url = d.get("url") or d.get("file") or ""
        if isinstance(url, str) and (
            url.startswith(("http://", "https://", "base64://")) or url.startswith("data:image")
        ):
            out.append(url)
    return out


def build_payload(b64: str, mime: str = "image/jpeg", model: str | None = None) -> dict:
    """GLM-4V 认的形状：content 数组，image_url 放 data URL。"""
    return {
        "model": model or VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
        "max_tokens": 300,
        "temperature": 0.2,
    }


def marker(descs: list[str]) -> str:
    """把若干描述拼成注入用的标记文本。空/失败 → [图片]。"""
    keep = [d.strip() for d in descs if isinstance(d, str) and d.strip()]
    if not keep:
        return FALLBACK
    return "[图片：" + "；".join(keep) + "]"


def replace_images(message, descs: list[str]) -> int:
    """把 message 里的 image 段就地换成文本标记，返回替换个数。

    段数比描述多时（下载失败的），多出来的用 FALLBACK。
    """
    from nonebot.adapters.onebot.v11 import MessageSegment

    idx = 0
    n = 0
    for i, seg in enumerate(list(message)):
        if getattr(seg, "type", None) != "image":
            continue
        desc = descs[idx] if idx < len(descs) else ""
        idx += 1
        message[i] = MessageSegment.text(marker([desc]) if desc else FALLBACK)
        n += 1
    return n


def sniff_mime(raw: bytes) -> str:
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


# ====================== 运行期 ======================
def _llm_config() -> tuple[str, str]:
    """返回 (api_base, api_key)。复用 llmchat 已配好的 key，不另外存凭据。"""
    base = os.getenv("VISION_API_BASE", "")
    key = os.getenv("VISION_API_KEY", "")
    if base and key:
        return base.rstrip("/"), key
    try:
        from nonebot_plugin_llmchat import plugin_config
        presets = plugin_config.api_presets
        want = getattr(plugin_config, "default_preset", "")
        p = next((x for x in presets if x.name == want), presets[0])
        return p.api_base.rstrip("/"), p.api_key
    except Exception:  # noqa: BLE001
        return "", ""


async def fetch_image(url: str) -> tuple[bytes, str]:
    if url.startswith("base64://"):
        raw = base64.b64decode(url[len("base64://"):])
        return raw, sniff_mime(raw)
    if url.startswith("data:image"):
        head, _, b64 = url.partition(",")
        mime = head.split(";")[0].removeprefix("data:") or "image/jpeg"
        return base64.b64decode(b64), mime
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        raw = r.content
    if len(raw) > MAX_BYTES:
        raise ValueError(f"图片过大 {len(raw)}B")
    return raw, sniff_mime(raw)


async def describe(url: str) -> str:
    """下载 + 调视觉模型，返回一句描述；任何失败返回空串（调用方用兜底标记）。"""
    base, key = _llm_config()
    if not base or not key:
        logger.warning("[vision] 拿不到 llmchat 的 api_base/api_key，跳过")
        return ""
    try:
        raw, mime = await fetch_image(url)
        payload = build_payload(base64.b64encode(raw).decode(), mime)
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{base}/chat/completions",
                             headers={"Authorization": f"Bearer {key}"},
                             json=payload)
        if r.status_code != 200:
            logger.warning(f"[vision] HTTP {r.status_code} {r.text[:160]}")
            return ""
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        content = clean_desc(content)
        logger.info(f"[vision] {len(raw)}B → {content[:60]}")
        return content
    except Exception as e:  # noqa: BLE001  —— 识别失败不能影响聊天
        logger.warning(f"[vision] 识别失败: {type(e).__name__}: {e}")
        return ""


def should_handle(event: MessageEvent) -> bool:
    """只在「这条消息本来就会让 AI 回」的时候去看图，避免白花额度。"""
    if not image_segments(event.get_message()):
        return False
    try:
        from nonebot_plugin_llmchat import group_states, plugin_config, private_chat_states
    except Exception:  # noqa: BLE001
        return False

    text = event.get_plaintext().strip()
    for prefix in plugin_config.ignore_prefixes:
        if text.startswith(prefix):
            return False

    if isinstance(event, GroupMessageEvent):
        state = group_states[event.group_id]
        if state.preset_name == "off":
            return False
        if event.user_id in plugin_config.blacklist_user_ids:
            return False
        return bool(event.is_tome())        # 群里只在被 @ 时看图
    if isinstance(event, PrivateMessageEvent):
        if not plugin_config.enable_private_chat:
            return False
        state = private_chat_states[event.user_id]
        return state.preset_name != "off"
    return False


vision_watch = on_message(priority=90, block=False)


@vision_watch.handle()
async def handle_vision(event: MessageEvent) -> None:
    if not should_handle(event):
        return
    urls = image_urls(event.get_message())[:MAX_IMAGES]
    descs: list[str] = []
    for url in urls:
        descs.append(await describe(url))
    n = replace_images(event.message, descs)
    got = sum(1 for d in descs if d)
    logger.info(f"[vision] {n} 张图 → {got} 条描述，交给 llmchat 接话")


driver = get_driver()


@driver.on_startup
async def _log_ready() -> None:
    base, _ = _llm_config()
    logger.info(f"[vision] 就绪：model={VISION_MODEL} api_base={base or '（未取到）'}")
