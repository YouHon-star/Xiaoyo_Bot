import base64
import os
import random
import re
import string
import time
from pathlib import Path

import requests
from Crypto.Cipher import AES
from nonebot import logger, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import FinishedException
from nonebot.params import EventPlainText
from nonebot.rule import Rule

# ---------------------- 全局编码设置 ----------------------
# Prefer UTF-8 when possible; avoid relying on platform-specific TextIO methods.
os.environ["PYTHONUTF8"] = "1"

# ---------------------- 核心配置区 ----------------------
def _resolve_netease_api_url() -> str:
    """优先读系统环境变量；否则从 NoneBot 配置读取（.env 已由 NoneBot 解析）"""
    url = os.getenv("NETEASE_API_URL", "").strip().rstrip("/")
    if url:
        return url
    try:
        from nonebot import get_driver

        url = str(getattr(get_driver().config, "netease_api_url", "") or "").strip()
        return url.rstrip("/")
    except Exception:
        return ""


NETEASE_API_URL = _resolve_netease_api_url()
NETEASE_COOKIE = "MUSIC_R_T=1528949322774; MUSIC_A_T=1528948912495; _ga=GA1.1.502086794.1761498527; _ga_EPDQHDTJH5=GS2.1.s1761500524$o2$g0$t1761500616$j60$l0$h0; _ntes_nnid=e58cff0820bb64105f8715bbf936fa56,1761506248627; _ntes_nuid=e58cff0820bb64105f8715bbf936fa56; NMTID=00OdzvUEwciipnfv09sgBPgCSI58oYAAAGaIfQt4g; WEVNSM=1.0.0; WNMCID=ycznqn.1761506248929.01.0; sDeviceId=YD-nsslrwLmZA5AEhFEVQKCiQlWuiQerJNL; _clck=1etdoyr%5E2%5Eg0h%5E0%5E2125; _ga_C6TGHFPQ1H=GS2.1.s1761506690$o1$g0$t1761506998$j60$l0$h0; ntes_kaola_ad=1; _iuqxldmzr_=32; timing_user_id=time_4yQHBKW3Yj; JSESSIONID-WYYY=jhqUUnqCQ7%2Bt2V24%2F4CTWpUUFc2GDgI%5Ck%5C5yzBw%5CY%2Fcs5pBDQ25R4wvZeMCPST23UbusepWCr6fulilpfyhuydB8l%5Cr16mWvvXuZgf%2FsNGFpynD%5CNp%2Fkpket%5CdOFY%5C4pv%2B4jub3fmimiBBwpwt5J92Y%5CywXt7Tv%5COocAbNV0w9g2FE6b%3A1770998263500; Hm_lvt_1483fb4774c02a30ffa6f0e2945e9b70=1770996464; HMACCOUNT=F4BA0C5F0E3F480B; MUSIC_U=00265AC6FE7ADEB183A5DFDCB74C75FA82C1EA68188709CF24186F4A0EF148B5F564082E76453F3A85F897155C236E5C6CAAA152FB614FAA06A2D0D6F35DAF41A3B0593021FCDDAF80781768D695207B15AFB74FA22F0E685ABDA02E831BB5B4737C9CFB86B79DFA5CBE0EAE030FBD8B567A2B6342A04407A6184282681670262B0451A1E30F3BD7F21C4F209BB2A8B07FE7F804903123A6BDFE21707D45866FFE14C48B4BF0F6E2CFCFC970C1C0E8D7A993F212588EA1E233BFDFF8089F58E122E95478B3BFE2DCA1A87904656C3F365F4CDBFE800058C91E93FD1B67F904112B0512BB1CE30A4040B5C7387E5AFDF7BD4D55F9D2B6B865A26322843FF5E8D74C24250D60C48BC39201E86EABF55076A17693601A0E663DA03C257CC0527570AFF9B6DF5018B264FEBF343D0C4EAEB8BFB675F1F32948770D8D5A1FBA49D07AEDBDD9A5E1AC94CCE68E376011327F6A1483C7BA3448A119448FF29CCAD43AD1C2EA9B003C459B881216CA9A17EAD6C6ECF58140363717F2A9446FD89D193587E8133885017F1502E93D7A2642AA81379CE80024C1351A8AC690C39263B755F3EC; __csrf=46b77ab762f897466890211ac781337a; Hm_lpvt_1483fb4774c02a30ffa6f0e2945e9b70=1770997138"  # noqa: E501
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0"  # noqa: E501
TEMP_DIR = Path("storage")
COOLDOWN_SECONDS = 120  # 冷却时间配置（单位：秒）
SEARCH_LIMIT = 10
SELECT_TIMEOUT = 60

# Cache successful direct URLs for a short time to reduce repeated requests.
SONG_URL_CACHE_TTL = 180

# 初始化目录
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# 全局状态存储
group_cooldown = {}  # {群聊ID: 最后执行时间戳}
user_select_state = {}  # {用户ID: (歌曲列表, 创建时间戳, 群聊ID, 是否群聊)}
song_url_cache: dict[int, tuple[str, float]] = {}


# ---------------------- 网易云加密工具 ----------------------
def aes_encrypt(text: str, key: str) -> str:
    pad = 16 - len(text) % 16
    text = text + pad * chr(pad)
    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, b"0102030405060708")
    encrypted = cipher.encrypt(text.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def rsa_encrypt(text: str, pubkey: str, modulus: str) -> str:
    text = text[::-1]
    rs = pow(int(text.encode("utf-8").hex(), 16), int(pubkey, 16), int(modulus, 16))
    return format(rs, "x").zfill(256)


def create_secret_key(size: int) -> str:
    return "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(size)
    )


# ---------------------- 核心业务逻辑 ----------------------
def _get_cached_song_url(song_id: int) -> str | None:
    cached = song_url_cache.get(song_id)
    if not cached:
        return None
    url, ts = cached
    if time.time() - ts > SONG_URL_CACHE_TTL:
        song_url_cache.pop(song_id, None)
        return None
    return url


def _set_cached_song_url(song_id: int, url: str) -> None:
    song_url_cache[song_id] = (url, time.time())


def _search_songs_via_local_api(song_name: str) -> tuple[list | None, str | None]:
    if not NETEASE_API_URL:
        return None, None

    try:
        res = requests.get(
            f"{NETEASE_API_URL}/cloudsearch",
            params={"keywords": song_name, "limit": SEARCH_LIMIT},
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()

        songs_raw = data.get("result", {}).get("songs")
        if not songs_raw:
            return None, f"未找到歌曲「{song_name}」"

        songs = []
        for s in songs_raw:
            song_id = s.get("id")
            name = s.get("name")
            artists = s.get("ar") or s.get("artists") or []
            artist = artists[0].get("name") if artists else ""
            if not song_id or not name:
                continue

            full_name = f"{name}-{artist}" if artist else str(name)
            songs.append(
                {
                    "id": int(song_id),
                    "name": str(name),
                    "artist": str(artist),
                    "full_name": full_name,
                }
            )

        if not songs:
            return None, f"未找到歌曲「{song_name}」"
    except (requests.RequestException, ValueError, TypeError) as e:
        logger.warning(f"本地网易云API搜索失败，将回退直连：{e!s}")
        return None, None
    else:
        return songs, None


def search_netease_songs(song_name: str) -> tuple[list | None, str | None]:
    songs, err = _search_songs_via_local_api(song_name)
    if songs or err:
        return songs, err

    search_url = f"https://music.163.com/api/search/get/web?csrf_token=&type=1&s={song_name}&offset=0&limit={SEARCH_LIMIT}"
    headers = {
        "Cookie": NETEASE_COOKIE,
        "User-Agent": USER_AGENT,
        "Referer": "https://music.163.com/",
    }
    try:
        search_res = requests.get(search_url, headers=headers, timeout=10)
        search_res.raise_for_status()
        search_data = search_res.json()

        # 防御：直连接口可能返回非 dict（如错误字符串/风控提示），避免 AttributeError
        if not isinstance(search_data, dict):
            logger.warning(
                f"网易云直连搜索返回非JSON对象: {type(search_data).__name__}: {str(search_data)[:100]!r}"
            )
            return None, f"网易云直连搜索失败，请稍后再试或联系开发者更新 Cookie。"

        if not search_data.get("result") or not search_data["result"].get("songs"):
            return None, f"未找到歌曲「{song_name}」"

        songs = []
        for song_info in search_data["result"]["songs"]:
            song_id = song_info["id"]
            song_name = song_info["name"]
            artist = song_info["artists"][0]["name"]
            full_name = f"{song_name}-{artist}"
            songs.append(
                {
                    "id": song_id,
                    "name": song_name,
                    "artist": artist,
                    "full_name": full_name,
                }
            )
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.error(f"搜索歌曲失败：{e!s}")
        return None, f"搜索歌曲失败：{e!s}"
    else:
        song_names = [song["full_name"] for song in songs]
        logger.info(f"找到 {len(songs)} 首相关歌曲：{song_names}")
        return songs, None


def _get_song_download_url_via_local_api(
    song_id: int, level: str
) -> tuple[str | None, str | None]:
    if not NETEASE_API_URL:
        return None, None

    cached = _get_cached_song_url(song_id)
    if cached:
        return cached, None

    def _extract_url(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        items = payload.get("data")
        if not isinstance(items, list) or not items:
            return None
        url_val = items[0].get("url")
        return url_val if isinstance(url_val, str) and url_val else None

    # 1) v1 endpoint supports level.
    try:
        res = requests.get(
            f"{NETEASE_API_URL}/song/url/v1",
            params={"id": song_id, "level": level},
            timeout=10,
        )
        res.raise_for_status()
        url = _extract_url(res.json())
        if url:
            _set_cached_song_url(song_id, url)
            return url, None
    except (requests.RequestException, ValueError, TypeError) as e:
        logger.warning(f"本地网易云API获取直链失败(/song/url/v1)：{e!s}")

    # 2) legacy endpoint.
    try:
        res = requests.get(
            f"{NETEASE_API_URL}/song/url",
            params={"id": song_id},
            timeout=10,
        )
        res.raise_for_status()
        url = _extract_url(res.json())
        if url:
            _set_cached_song_url(song_id, url)
            return url, None
    except (requests.RequestException, ValueError, TypeError) as e:
        logger.warning(f"本地网易云API获取直链失败(/song/url)：{e!s}")

    return None, None


def _get_song_download_url_via_weapi(
    song_id: int, level: str
) -> tuple[str | None, str | None]:
    try:
        cached = _get_cached_song_url(song_id)
        if cached:
            return cached, None

        headers = {
            "Cookie": NETEASE_COOKIE,
            "User-Agent": USER_AGENT,
            "Referer": "https://music.163.com/",
        }
        url = "https://music.163.com/weapi/song/enhance/player/url/v1?csrf_token="
        modulus = "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"  # noqa: E501
        pubkey = "010001"
        nonce = "0CoJUm6Qyw8W8jud"
        sec_key = create_secret_key(16)

        enc_text = aes_encrypt(
            f'{{"ids":["{song_id}"],"level":"{level}","encodeType":"mp3"}}',
            nonce,
        )
        enc_text = aes_encrypt(enc_text, sec_key)
        enc_sec_key = rsa_encrypt(sec_key, pubkey, modulus)

        data = {"params": enc_text, "encSecKey": enc_sec_key}
        res = requests.post(url, headers=headers, data=data, timeout=10)
        res.raise_for_status()
        song_data = res.json()

        song_url = song_data["data"][0].get("url")
        if isinstance(song_url, str) and song_url:
            _set_cached_song_url(song_id, song_url)
            return song_url, None
    except (
        requests.RequestException,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
    ) as e:
        logger.error(f"获取下载链接失败：{e!s}")
        return None, f"获取下载链接失败：{e!s}"
    else:
        return None, None


def get_song_download_url(song_id: int) -> tuple[str | None, str | None]:
    levels = ["lossless", "exhigh", "standard"]
    for level in levels:
        url, err = _get_song_download_url_via_local_api(song_id, level)
        if url:
            return url, None
        if err:
            return None, err

        url, err = _get_song_download_url_via_weapi(song_id, level)
        if url:
            return url, None
        if err:
            return None, err

    return (
        None,
        "当前直链获取被限制（可能触发频控/风控）。请稍后再试，或换歌名/换时间；"
        "如连续多次失败请联系开发者更新 Cookie 或配置本地网易云API。",
    )


def download_song(song_url: str, save_path: Path) -> bool:
    try:
        headers = {"User-Agent": USER_AGENT}
        res = requests.get(song_url, headers=headers, stream=True, timeout=20)
        res.raise_for_status()
        with save_path.open("wb") as f:
            for chunk in res.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    except (requests.RequestException, OSError) as e:
        logger.error(f"下载歌曲失败：{e!s}")
        return False
    else:
        return True


# ---------------------- 冷却时间校验 ----------------------
def check_group_cooldown(group_id: str) -> tuple[bool, int]:
    current_time = time.time()
    last_exec_time = group_cooldown.get(group_id, 0)
    remaining = int(COOLDOWN_SECONDS - (current_time - last_exec_time))
    return (False, remaining) if remaining > 0 else (True, 0)


def update_group_cooldown(group_id: str) -> None:
    group_cooldown[group_id] = time.time()


# ---------------------- 选择状态管理 ----------------------
def generate_song_selection_msg(songs: list) -> str:
    msg = f"🎵 找到以下歌曲，请选择序号（1-{len(songs)}）：\n"
    for idx, song in enumerate(songs, 1):
        msg += f"{idx}. {song['full_name']}\n"
    msg += f"\n请在{SELECT_TIMEOUT}秒内回复序号，例如：1"
    return msg


def select_rule(event: MessageEvent) -> bool:
    """优化后的选择触发规则：确保场景+状态+超时都匹配"""
    user_id = str(event.user_id)
    if user_id not in user_select_state:
        return False

    # 解构状态
    _songs, create_time, group_id, is_group = user_select_state[user_id]

    # 检查超时
    if time.time() - create_time > SELECT_TIMEOUT:
        user_select_state.pop(user_id, None)
        return False

    # 检查聊天场景匹配（群聊/私聊）
    if is_group:
        return isinstance(event, GroupMessageEvent) and str(event.group_id) == group_id
    return not isinstance(event, GroupMessageEvent)


# ---------------------- NoneBot指令注册 ----------------------
get_music = on_command("小幽获取歌曲", aliases={"获取歌曲"}, priority=4, block=True)

# 选择歌曲的响应器（只在用户有待选择歌曲时触发）
select_song = on_message(
    Rule(select_rule),
    priority=4,
    # When in "select song" state, consume the message to avoid AI/other listeners
    # also handling the numeric reply.
    block=True,
)


@get_music.handle()
async def handle_get_music(bot: Bot, event: MessageEvent) -> None:
    # 1. 场景判断
    is_group = isinstance(event, GroupMessageEvent)
    group_id = str(event.group_id) if is_group else None
    user_id = str(event.user_id)

    # 2. 群聊冷却
    if is_group:
        assert group_id is not None
        can_exec, remaining = check_group_cooldown(group_id)
        if not can_exec:
            minutes = remaining // 60
            seconds = remaining % 60
            await get_music.finish(
                f"⏳ 该群聊正在冷却中！\n"
                f"需等待 {minutes}分{seconds}秒 后才能再次获取歌曲"
            )

    # 3. 提取歌名
    cmd_text = event.get_plaintext().strip()
    song_name = re.sub(r"^小幽获取歌曲|^获取歌曲", "", cmd_text).strip()
    if not song_name:
        await get_music.finish(
            "❌ 格式错误！请输入：小幽获取歌曲[歌名]\n例如：小幽获取歌曲星界可不"
        )

    # 4. 搜索歌曲
    await get_music.send(f"🔍 正在搜索歌曲「{song_name}」...")
    songs, error_msg = search_netease_songs(song_name)
    if not songs:
        await get_music.finish(f"❌ {error_msg}")

    # 5. 处理结果
    if len(songs) == 1:
        # 单结果直接处理
        selected_song = songs[0]
        await process_selected_song(bot, event, selected_song, is_group, group_id)
    else:
        # 多结果发送选择提示
        selection_msg = generate_song_selection_msg(songs)
        await get_music.send(selection_msg)
        # 记录用户状态（包含场景信息）
        user_select_state[user_id] = (songs, time.time(), group_id, is_group)


@select_song.handle()
async def handle_select_song(
    bot: Bot, event: MessageEvent, plain_text: str = EventPlainText()
) -> None:
    user_id = str(event.user_id)

    # 1. 基础校验
    if user_id not in user_select_state:
        await select_song.finish("❌ 没有待选择的歌曲，请重新发送指令")

    songs, create_time, group_id, is_group = user_select_state[user_id]

    # 2. 超时二次校验
    if time.time() - create_time > SELECT_TIMEOUT:
        user_select_state.pop(user_id, None)
        await select_song.finish("❌ 选择已超时，请重新发送「小幽获取歌曲[歌名]」")

    # 3. 解析用户输入
    select_input = plain_text.strip()
    if not select_input.isdigit():
        await select_song.finish("❌ 输入格式错误！请输入纯数字序号，例如：1")

    select_idx = int(select_input) - 1
    # 4. 序号范围校验
    if select_idx < 0 or select_idx >= len(songs):
        await select_song.finish(f"❌ 序号无效！请选择1-{len(songs)}之间的数字")

    # 5. 处理选中歌曲
    selected_song = songs[select_idx]
    logger.info(f"用户 {user_id} 选择了歌曲：{selected_song['full_name']}")

    # 6. 清理状态（关键：避免重复响应）
    user_select_state.pop(user_id, None)

    # 7. 执行发送逻辑
    try:
        await process_selected_song(bot, event, selected_song, is_group, group_id)
    except FinishedException:
        # matcher.finish() uses FinishedException for control flow; don't wrap it
        # as a user-visible failure.
        raise
    except Exception as e:
        if e.__class__.__name__ == "FinishedException":
            raise

        logger.error(f"处理歌曲选择失败：{e!s}")
        await select_song.finish(f"❌ 选择处理失败：{e!s}")


async def process_selected_song(  # noqa: C901, PLR0912, PLR0915
    bot: Bot,
    event: MessageEvent,
    selected_song: dict,
    is_group: bool,  # noqa: FBT001
    group_id: str | None,
) -> None:
    """处理选中的歌曲（下载+发送，适配NapCat）"""
    song_id = selected_song["id"]
    song_info = selected_song["full_name"]

    # 1. 获取下载链接
    await get_music.send(f"✅ 选择歌曲：{song_info}\n🔗 正在获取下载链接...")
    song_url, error_msg = get_song_download_url(song_id)
    if not song_url:
        await get_music.finish(f"❌ {song_info}：{error_msg}")

    # 2. 下载歌曲
    safe_song_name = re.sub(r'[\\/:*?"<>|]', "_", song_info)
    save_path = (TEMP_DIR / f"{safe_song_name}.mp3").resolve()
    await get_music.send(f"📥 正在下载歌曲「{song_info}」...")

    if not download_song(song_url, save_path):
        await get_music.finish(f"❌ 歌曲「{song_info}」下载失败！")

    # 3. 检查文件存在性
    if not save_path.exists():
        await get_music.finish(f"❌ 歌曲文件不存在：{save_path}")

    # 4. 发送歌曲（适配NapCat）
    await get_music.send(f"🎵 下载完成，正在发送歌曲「{song_info}」...")
    try:
        # NapCat兼容的发送方式
        # NapCat supports OneBot v11 "file" segment; adapter stubs may not expose
        # a helper like MessageSegment.file().
        file_seg = MessageSegment("file", {"file": str(save_path)})
        file_msg = Message([file_seg])
        if is_group:
            assert group_id is not None
            await bot.send_group_msg(group_id=int(group_id), message=file_msg)
        else:
            await bot.send_private_msg(user_id=int(event.user_id), message=file_msg)

        await get_music.send(f"✅ 歌曲「{song_info}」发送成功！")

        # 发送成功后删除本地文件
        try:
            if save_path.exists():
                save_path.unlink()
                logger.info(f"已删除本地歌曲文件：{save_path}")
        except OSError as e:
            logger.warning(f"删除本地文件失败：{e!s}")

        # 更新冷却时间
        if is_group and group_id:
            update_group_cooldown(group_id)
            await get_music.send("⚠️ 该群聊下次获取歌曲需等待2分钟哦~")

    except ActionFailed as e:
        # 降级上传方案
        logger.error(f"直接发送失败，尝试上传：{e!s}")
        try:
            if is_group:
                assert group_id is not None
                await bot.upload_group_file(
                    group_id=int(group_id),
                    file=str(save_path),
                    name=f"{safe_song_name}.mp3",
                )
            else:
                await bot.upload_private_file(
                    user_id=int(event.user_id),
                    file=str(save_path),
                    name=f"{safe_song_name}.mp3",
                )

            await get_music.send(
                f"✅ 歌曲「{song_info}」上传成功！文件已发送至当前聊天"
            )

            # 上传成功后删除本地文件
            try:
                if save_path.exists():
                    save_path.unlink()
                    logger.info(f"已删除本地歌曲文件：{save_path}")
            except OSError as e:
                logger.warning(f"删除本地文件失败：{e!s}")

            if is_group and group_id:
                update_group_cooldown(group_id)

        except Exception as e2:  # noqa: BLE001
            logger.error(f"上传也失败：{e2!s}")
            await get_music.finish(f"❌ 发送失败：{e2!s}\n请检查NapCat权限/文件大小")
    except Exception as e3:  # noqa: BLE001
        logger.error(f"发送未知错误：{e3!s}")
        await get_music.finish(f"❌ 发送失败：{e3!s}")
