"""
Stream Announcer Bot
---------------------
Проверяет YouTube-канал на наличие запланированных/идущих стримов,
генерирует текст анонса по шаблонам (без внешних AI-сервисов) и постит
его в Telegram вместе с превью (thumbnail) стрима.

Все настройки берутся из переменных окружения (см. README.md).
"""

import os
import json
import random
import sys
import urllib.request
import urllib.parse
import urllib.error
import re
import time
from datetime import datetime, timezone
import zoneinfo

# ---------- Конфиг из переменных окружения ----------
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
YOUTUBE_CHANNEL_ID = os.environ["YOUTUBE_CHANNEL_ID"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")
TIMEZONE_LABEL = os.environ.get("TIMEZONE_LABEL", "МСК")
SECOND_TIMEZONE = os.environ.get("SECOND_TIMEZONE", "Asia/Almaty")
SECOND_TIMEZONE_LABEL = os.environ.get("SECOND_TIMEZONE_LABEL", "Казахстан")

REPO = "ErikHai123"
REPO_NAME = "stream-announcer"

TWITCH_URL = "https://www.twitch.tv/atomgit"
TIKTOK_URL = "https://www.tiktok.com/@atomgit"

SUBSCRIBER_MILESTONE_STEP = int(os.environ.get("SUBSCRIBER_MILESTONE_STEP", "10000"))

STATE_FILE = os.path.join(os.path.dirname(__file__), "posted_ids.json")
MILESTONE_STATE_FILE = os.path.join(os.path.dirname(__file__), "milestone_state.json")
STATS_FILE = os.path.join(os.path.dirname(__file__), "daily_stats.json")
RANDOM_POSTED_STATE_FILE = os.path.join(os.path.dirname(__file__), "random_posted_ids.json")

# Защита от слишком частых запусков (не чаще раза в 60 секунд)
_RUN_LOCK_FILE = os.path.join(os.path.dirname(__file__), ".last_run")
_MIN_RUN_INTERVAL_SECONDS = 60

# Сколько дней хранить posted_ids (потом автоудаление)
_POSTED_IDS_MAX_AGE_DAYS = 30
_RANDOM_POSTED_MAX_AGE_DAYS = 90

# Сколько страниц плейлиста загружать для рандома (5 x 50 = 250 видео)
_MAX_PAGES_FOR_RANDOM = 5


# ---------- Статистика за день ----------
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ Ошибка чтения stats, создаём заново: {e}", file=sys.stderr)
    return {
        "current_date": "",
        "posts_today": 0,
        "errors_today": 0,
        "report_sent_today": False,
        "random_sent_today": False,
    }


def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def increment_posts(stats):
    stats["posts_today"] = stats.get("posts_today", 0) + 1


def increment_errors(stats):
    stats["errors_today"] = stats.get("errors_today", 0) + 1


def send_daily_report(stats):
    """Отправляет отчёт админу. НЕ ловит исключения — пусть всплывает в main()."""
    if not ADMIN_CHAT_ID:
        return
    date_str = datetime.now(zoneinfo.ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
    posts = stats.get("posts_today", 0)
    errors = stats.get("errors_today", 0)

    text = f"📊 Отчёт за {date_str}\n✅ Постов сделано: {posts}\n❌ Ошибок за день: {errors}"
    if errors > 10:
        text += f"\n🔗 Логи: https://github.com/{REPO}/{REPO_NAME}/actions"
    elif posts == 0 and errors == 0:
        text += "\n💤 Сегодня тихо, но бот на месте!"

    send_telegram_message(ADMIN_CHAT_ID, text)
    print(f"📤 Отчёт отправлен админу: {posts} постов, {errors} ошибок")


# ---------- Защита от частых запусков ----------
def _check_rate_limit():
    if os.path.exists(_RUN_LOCK_FILE):
        try:
            with open(_RUN_LOCK_FILE, "r", encoding="utf-8") as f:
                last_run = float(f.read().strip())
            if time.time() - last_run < _MIN_RUN_INTERVAL_SECONDS:
                print(
                    f"⏳ Слишком частый запуск. Пропускаем "
                    f"({int(time.time() - last_run)} сек назад был запуск)."
                )
                return False
        except (ValueError, OSError):
            pass
    with open(_RUN_LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(time.time()))
    return True


# ---------- posted_ids с автоочисткой старых записей ----------
def load_posted_ids():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Миграция со старого формата (список) на новый (словарь с датами)
            if isinstance(data, list):
                today = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
                return {vid: today for vid in data}
            elif isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ Ошибка чтения posted_ids, создаём заново: {e}", file=sys.stderr)
    return {}


def save_posted_ids(ids_dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(ids_dict, f, ensure_ascii=False, indent=2)


def clean_old_posted_ids(posted_ids, days=_POSTED_IDS_MAX_AGE_DAYS):
    """Удаляем ID старше N дней — файл не разрастётся бесконечно."""
    if not posted_ids:
        return posted_ids
    today = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).date()
    to_remove = []
    for vid, date_str in list(posted_ids.items()):
        try:
            post_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if (today - post_date).days > days:
                to_remove.append(vid)
        except (ValueError, TypeError):
            to_remove.append(vid)
    for vid in to_remove:
        del posted_ids[vid]
    if to_remove:
        print(f"🧹 Очищено старых ID: {len(to_remove)} (старше {days} дней)")
    return posted_ids


# ---------- Рандомные видео (отдельный трекинг) ----------
def load_random_posted_ids():
    if os.path.exists(RANDOM_POSTED_STATE_FILE):
        try:
            with open(RANDOM_POSTED_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ Ошибка чтения random_posted_ids: {e}", file=sys.stderr)
    return {}


def save_random_posted_ids(ids_dict):
    with open(RANDOM_POSTED_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(ids_dict, f, ensure_ascii=False, indent=2)


def clean_old_random_posted_ids(random_posted_ids, days=_RANDOM_POSTED_MAX_AGE_DAYS):
    if not random_posted_ids:
        return random_posted_ids
    today = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).date()
    to_remove = []
    for vid, date_str in list(random_posted_ids.items()):
        try:
            post_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if (today - post_date).days > days:
                to_remove.append(vid)
        except (ValueError, TypeError):
            to_remove.append(vid)
    for vid in to_remove:
        del random_posted_ids[vid]
    if to_remove:
        print(f"🧹 Очищено старых random ID: {len(to_remove)} (старше {days} дней)")
    return random_posted_ids


def get_all_uploads(max_pages=_MAX_PAGES_FOR_RANDOM):
    """Получает видео из uploads плейлиста с пагинацией (ограничено max_pages)."""
    playlist_id = get_uploads_playlist_id()
    if not playlist_id:
        return []

    video_ids = []
    next_page_token = None
    pages = 0

    while pages < max_pages:
        params = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": YOUTUBE_API_KEY,
        }
        if next_page_token:
            params["pageToken"] = next_page_token

        try:
            data = http_get_json(
                "https://www.googleapis.com/youtube/v3/playlistItems",
                params,
            )
        except Exception as e:
            print(f"⚠️ Ошибка загрузки страницы uploads: {e}", file=sys.stderr)
            break

        for item in data.get("items", []):
            vid = item["snippet"]["resourceId"]["videoId"]
            video_ids.append(vid)

        next_page_token = data.get("nextPageToken")
        pages += 1
        if not next_page_token:
            break

    return video_ids


def get_random_unposted_video(posted_ids, random_posted_ids):
    """Возвращает случайное видео, которое не публиковалось ни как новое, ни как рандомное."""
    all_videos = get_all_uploads()

    available = [vid for vid in all_videos if vid not in posted_ids and vid not in random_posted_ids]

    if not available:
        return None
    return random.choice(available)


# ---------- Состояния ----------
def load_last_milestone():
    if os.path.exists(MILESTONE_STATE_FILE):
        try:
            with open(MILESTONE_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("last_milestone", 0), True
        except (json.JSONDecodeError, OSError):
            pass
    return 0, False


def save_last_milestone(value):
    with open(MILESTONE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_milestone": value}, f, ensure_ascii=False, indent=2)


# ---------- HTTP с timeout ----------
def http_get_json(url, params, timeout=15):
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"
    try:
        req = urllib.request.Request(full_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"HTTP {e.code} ошибка при запросе к {url}", file=sys.stderr)
        print(f"Подробности: {error_body}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        print(f"Сетевая ошибка при запросе к {url}: {e.reason}", file=sys.stderr)
        raise


# ---------- YouTube ----------
def get_uploads_playlist_id():
    data = http_get_json(
        "https://www.googleapis.com/youtube/v3/channels",
        {
            "part": "contentDetails",
            "id": YOUTUBE_CHANNEL_ID,
            "key": YOUTUBE_API_KEY,
        },
    )
    items = data.get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def find_candidate_videos():
    playlist_id = get_uploads_playlist_id()
    if not playlist_id:
        return []

    data = http_get_json(
        "https://www.googleapis.com/youtube/v3/playlistItems",
        {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": YOUTUBE_API_KEY,
        },
    )
    return [item["snippet"]["resourceId"]["videoId"] for item in data.get("items", [])]


def get_video_details_batch(video_ids):
    if not video_ids:
        return {}
    data = http_get_json(
        "https://www.googleapis.com/youtube/v3/videos",
        {
            "part": "snippet,liveStreamingDetails,contentDetails",
            "id": ",".join(video_ids),
            "key": YOUTUBE_API_KEY,
        },
    )
    return {item["id"]: item for item in data.get("items", [])}


def get_channel_info():
    data = http_get_json(
        "https://www.googleapis.com/youtube/v3/channels",
        {
            "part": "snippet,statistics",
            "id": YOUTUBE_CHANNEL_ID,
            "key": YOUTUBE_API_KEY,
        },
    )
    items = data.get("items", [])
    if not items:
        return None, None
    count = int(items[0]["statistics"]["subscriberCount"])
    title = items[0]["snippet"]["title"]
    return count, title


def best_thumbnail(thumbnails):
    for key in ("maxres", "standard", "high", "medium", "default"):
        if key in thumbnails:
            return thumbnails[key]["url"]
    return None


def format_start_time(iso_ts):
    dt_utc = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(timezone.utc)

    local = dt_utc.astimezone(zoneinfo.ZoneInfo(TIMEZONE))
    main_str = local.strftime("%d.%m.%Y в %H:%M") + f" ({TIMEZONE_LABEL})"

    second = dt_utc.astimezone(zoneinfo.ZoneInfo(SECOND_TIMEZONE))
    second_str = second.strftime("%H:%M") + f" ({SECOND_TIMEZONE_LABEL})"

    return f"{main_str} / {second_str}"


def parse_duration_seconds(iso_duration):
    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration or ""
    )
    if not match:
        return 0
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def extract_video_id(url):
    if not url or not url.strip():
        return None
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})",
        r"(?:embed\/)([0-9A-Za-z_-]{11})",
        r"(?:youtu\.be\/)([0-9A-Za-z_-]{11})",
        r"(?:shorts\/)([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


# ---------- Эмодзи ----------
GAME_EMOJIS = [
    ("gta", "🚗"),
    ("гта", "🚗"),
    ("farcry", "🔫"),
    ("far cry", "🔫"),
    ("cyberpunk", "🤖"),
    ("cs2", "🔫"),
    ("csgo", "🔫"),
    ("cs 1.6", "🔫"),
    ("counter-strike", "🔫"),
    ("minecraft", "⛏️"),
    ("майнкрафт", "⛏️"),
    ("f1", "🏎️"),
    ("formula", "🏎️"),
    ("fifa", "⚽"),
    ("repo", "🤖"),
    ("battlefield", "💣"),
    ("valorant", "🔫"),
    ("dota", "⚔️"),
    ("fortnite", "🔫"),
    ("warzone", "🔫"),
    ("elden ring", "⚔️"),
    ("stalker", "☢️"),
    ("roblox", "🧱"),
    ("apex", "🔫"),
    ("overwatch", "🔫"),
    ("wow", "⚔️"),
    ("world of warcraft", "⚔️"),
    ("rocket league", "⚽"),
    ("fall guys", "🎮"),
    ("among us", "🎮"),
]

DEFAULT_THEME_EMOJI = "🔴"


def detect_theme_emoji(title):
    lowered = title.lower()
    for keyword, emoji in GAME_EMOJIS:
        if keyword in lowered:
            return emoji
    return DEFAULT_THEME_EMOJI


# ---------- Шаблоны ----------
LIVE_TEMPLATES = [
    "{emoji} Внимание! {channel} начал стрим прямо сейчас!\n«{title}»\nЗаходи, пока горячо 👇",
    "{emoji} Мы уже в эфире! {channel} стримит:\n«{title}»\nПодключайся, будет интересно!",
    "{emoji} Стрим уже идёт! «{title}» от {channel} — залетай в трансляцию 🚀",
    "{emoji} {channel} в эфире прямо сейчас: «{title}»\nНе тупи, залетай, пока не закончилось!",
    "{emoji} Погнали! {channel} стримит «{title}» уже сейчас — подключайся 👇",
    "{emoji} Лайв уже идёт: «{title}»\nЗаходи к {channel}, будет угарно!",
    "{emoji} {channel} на связи прямо сейчас!\n«{title}»\nЖмякай и залетай в трансляцию",
    "{emoji} Стрим уже кипит! «{title}» от {channel}\nНе пропусти самое интересное",
    "{emoji} Мы в эфире! {channel} — «{title}»\nВрывайся, тут жарко 🔥",
    "{emoji} Уже стримим: «{title}»\n{channel} ждёт тебя в трансляции прямо сейчас!",
]

UPCOMING_TEMPLATES = [
    "{emoji} Скоро стрим! {channel} проведёт трансляцию «{title}»\n🕒 {when}\nСтавь напоминание, чтобы не пропустить!",
    "{emoji} Анонс: «{title}»\nКанал: {channel}\n⏰ Начало: {when}\nЖдём всех на YouTube!",
    "{emoji} Готовь чай/кофе — уже {when} стартует «{title}» от {channel}. Не пропусти!",
    "{emoji} {channel} запланировал(а) стрим «{title}»\n🕒 Старт: {when}\nБудет интересно, залетай!",
    "{emoji} Совсем скоро в эфире: «{title}»\n⏰ {when}\nПодписывайся на уведомление, чтобы не проспать!",
    "{emoji} Внимание, анонс! {channel} выйдет в эфир {when}\nТема: «{title}»",
    "{emoji} Стрим на подходе: «{title}»\n{channel} ждёт тебя {when}, не пропусти!",
    "{emoji} Скоро погнали! {channel} — «{title}»\n🕒 Начало в {when}",
    "{emoji} Запланирован стрим «{title}»\nКанал: {channel} | ⏰ {when}\nСтавь напоминалку!",
    "{emoji} {channel} скоро в эфире!\n«{title}»\n🕒 {when} — будет жарко, не пропусти",
]

VIDEO_TEMPLATES = [
    "{emoji} Новое видео на канале {channel}!\n«{title}»\nСмотри прямо сейчас 👇",
    "{emoji} {channel} выпустил(а) новое видео:\n«{title}»\nНе пропусти!",
    "{emoji} Свежий ролик от {channel}: «{title}»\nЗаходи смотреть!",
    "{emoji} Вышло новое видео: «{title}»\nОт {channel} — залетай глянуть",
    "{emoji} {channel} радует новинкой!\n«{title}»\nСмотри, пока горячее 🔥",
    "{emoji} Свежак на канале: «{title}»\n{channel} уже ждёт тебя на просмотре",
    "{emoji} Новинка от {channel}: «{title}»\nЖмякай и смотри прямо сейчас!",
    "{emoji} Только что вышло: «{title}»\nОт {channel} — не проходи мимо",
    "{emoji} {channel} выложил(а) новое видео «{title}»\nЗалетай, будет интересно!",
    "{emoji} Новый ролик уже на канале: «{title}»\nОт {channel} — заходи смотреть",
]

SHORTS_TEMPLATES = [
    "{emoji} Новый Shorts от {channel}!\n«{title}»\nБыстро глянь, займёт всего минутку 👇",
    "{emoji} {channel} выпустил(а) новый шортс: «{title}»\nСмотри, пока не пролистал(а)!",
    "{emoji} Свежий Shorts: «{title}» от {channel}\nЗаглядывай!",
    "{emoji} Мини-ролик от {channel}: «{title}»\nСмотри за 60 секунд!",
    "{emoji} Новый шортс уже тут: «{title}»\nОт {channel} — быстро глянь",
    "{emoji} {channel} радует шортсом!\n«{title}»\nНе пролистывай, зацени",
    "{emoji} Свежак в Shorts: «{title}»\n{channel} ждёт лайк 👍",
    "{emoji} Новый Shorts: «{title}»\nОт {channel} — залетай на минутку",
    "{emoji} {channel} выложил(а) шортс «{title}»\nБыстро и по делу, смотри!",
    "{emoji} Только вышел шортс: «{title}»\nОт {channel} — не пролистывай мимо!",
]

RANDOM_TEMPLATES = [
    "{emoji} Вспоминаем классику! {channel} — «{title}»\nЕсли пропустил — самое время наверстать 👇",
    "{emoji} Рандомный выбор дня: «{title}» от {channel}\nЗалетай, это того стоит!",
    "{emoji} Случайно наткнулись на «{title}» от {channel}\nНе пропусти, если ещё не видел!",
    "{emoji} Давайте вспомним: «{title}»\nОт {channel} — отличный ролик для пересмотра",
    "{emoji} Рекомендуем глянуть: «{title}»\n{channel} уже ждёт тебя на просмотре",
    "{emoji} Случайный ролик дня: «{title}»\nОт {channel} — заходи, будет интересно!",
    "{emoji} Нашли жемчужину: «{title}» от {channel}\nЕсли пропустил — исправляй!",
    "{emoji} «{title}» — классика от {channel}\nПересмотри, если уже видел, или смотри впервые!",
    "{emoji} Рандомный ролик: «{title}»\n{channel} — заходи, не пожалеешь",
    "{emoji} Внезапно: «{title}» от {channel}!\nОтличный повод вернуться к старым видео",
]


def generate_announcement_text(content_type, title, channel_title, start_time_str=""):
    templates_map = {
        "live": LIVE_TEMPLATES,
        "upcoming": UPCOMING_TEMPLATES,
        "video": VIDEO_TEMPLATES,
        "shorts": SHORTS_TEMPLATES,
        "random": RANDOM_TEMPLATES,
    }
    template = random.choice(templates_map[content_type])
    emoji = detect_theme_emoji(title)
    return template.format(channel=channel_title, title=title, when=start_time_str, emoji=emoji)


# ---------- Telegram ----------
def send_telegram_message(chat_id, text):
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=body,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")
    return result


def react_to_message(chat_id, message_id, emoji="🔥"):
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": json.dumps([{"type": "emoji", "emoji": emoji}]),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMessageReaction",
        data=body,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")
    return result


def send_telegram_photo(photo_url, caption, buttons=None):
    params = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": photo_url,
        "caption": caption,
    }
    if buttons:
        params["reply_markup"] = json.dumps({"inline_keyboard": [buttons]})

    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
        data=body,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"❌ Telegram HTTP {e.code}: {error_body}", file=sys.stderr)
        print(f"📊 Длина caption: {len(caption)} символов", file=sys.stderr)
        print(f"🔗 URL фото: {photo_url}", file=sys.stderr)
        print(f"🔘 Кнопки: {buttons}", file=sys.stderr)
        raise RuntimeError(f"Telegram error {e.code}: {error_body}")

    if not result.get("ok"):
        print(f"❌ Telegram API error: {result}", file=sys.stderr)
        raise RuntimeError(f"Telegram error: {result}")
    return result


# ---------- Milestone ----------
MILESTONE_TEMPLATES = [
    "🎉 Ура! У {channel} уже {count} подписчиков!\nСпасибо, что вы с нами — это только начало 🚀",
    "🎊 Юбилей! {channel} набрал(а) {count} подписчиков!\nОгромное спасибо каждому из вас ❤️",
    "🥳 {count} подписчиков у {channel}!\nСпасибо за поддержку, дальше — больше!",
]


def check_subscriber_milestone(stats):
    """Проверяет milestones и обновляет stats при публикации."""
    try:
        count, channel_title = get_channel_info()
    except Exception as e:
        print(f"Не удалось получить число подписчиков: {e}", file=sys.stderr)
        return

    if count is None:
        return

    current_milestone = (count // SUBSCRIBER_MILESTONE_STEP) * SUBSCRIBER_MILESTONE_STEP
    last_milestone, state_existed = load_last_milestone()

    if not state_existed:
        save_last_milestone(current_milestone)
        print(f"Отметка подписчиков инициализирована: {current_milestone}")
        return

    if current_milestone > last_milestone and current_milestone > 0:
        text = random.choice(MILESTONE_TEMPLATES).format(
            channel=channel_title, count=current_milestone
        )
        try:
            result = send_telegram_message(TELEGRAM_CHAT_ID, text)
            print(f"Опубликовано поздравление с {current_milestone} подписчиками")
            increment_posts(stats)
            try:
                message_id = result["result"]["message_id"]
                react_to_message(TELEGRAM_CHAT_ID, message_id, "🎉")
            except Exception as e:
                print(f"Не удалось поставить реакцию: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Ошибка отправки поздравления: {e}", file=sys.stderr)
            increment_errors(stats)
            return
        save_last_milestone(current_milestone)


# ---------- Main ----------
SHORTS_MAX_DURATION_SECONDS = 60
MAX_POSTS_PER_RUN = 3
SECONDS_BETWEEN_POSTS = 3
CATCH_UP_ONLY = os.environ.get("CATCH_UP_ONLY", "false").lower() == "true"

FORCE_VIDEO_URL = os.environ.get("FORCE_VIDEO_URL", "")


def main():
    # --- Загружаем статистику и проверяем дату ---
    stats = load_stats()
    now = datetime.now(zoneinfo.ZoneInfo("Europe/Moscow"))
    today_str = now.strftime("%Y-%m-%d")

    # Если наступил новый день — сбрасываем счётчики
    if stats.get("current_date") != today_str:
        stats = {
            "current_date": today_str,
            "posts_today": 0,
            "errors_today": 0,
            "report_sent_today": False,
            "random_sent_today": False,
        }

    # Отчёт в 23:00 МСК (ловим в диапазоне 23:00–23:59)
    if now.hour == 23 and not stats.get("report_sent_today", False):
        try:
            send_daily_report(stats)
            stats["report_sent_today"] = True
        except Exception as e:
            print(f"❌ Ошибка отправки отчёта: {e}", file=sys.stderr)
            increment_errors(stats)
        save_stats(stats)

    # --- Защита от частых запусков ---
    if not _check_rate_limit():
        save_stats(stats)
        return

    # --- Загружаем и чистим posted_ids ---
    posted_ids = load_posted_ids()
    posted_ids = clean_old_posted_ids(posted_ids, days=_POSTED_IDS_MAX_AGE_DAYS)

    # --- Рандомное видео дня в 15:00 МСК ---
    if now.hour == 15 and not stats.get("random_sent_today", False):
        random_posted_ids = load_random_posted_ids()
        random_posted_ids = clean_old_random_posted_ids(
            random_posted_ids, days=_RANDOM_POSTED_MAX_AGE_DAYS
        )

        random_video_id = get_random_unposted_video(posted_ids, random_posted_ids)
        if random_video_id:
            print(f"🎲 Выбрано рандомное видео: {random_video_id}")
            try:
                details = get_video_details_batch([random_video_id]).get(random_video_id)
                if details:
                    snippet = details["snippet"]
                    title = snippet["title"]
                    channel_title = snippet["channelTitle"]
                    thumbnail_url = best_thumbnail(snippet["thumbnails"])

                    text = generate_announcement_text("random", title, channel_title)
                    video_link = f"https://www.youtube.com/watch?v={random_video_id}"
                    buttons = [{"text": "▶️ YouTube", "url": video_link}]

                    print(f"📤 Попытка отправки рандомного: {title} ({random_video_id})")
                    result = send_telegram_photo(thumbnail_url, text, buttons)
                    print(f"✅ Рандомное видео опубликовано: {title} ({random_video_id})")
                    increment_posts(stats)
                    try:
                        message_id = result["result"]["message_id"]
                        react_to_message(TELEGRAM_CHAT_ID, message_id, "🎲")
                    except Exception as e:
                        print(f"Не удалось поставить реакцию: {e}", file=sys.stderr)

                    # ФИКС: добавляем в posted_ids, чтобы не задублировать как "новое видео"
                    posted_ids[random_video_id] = today_str
                    save_posted_ids(posted_ids)

                    random_posted_ids[random_video_id] = today_str
                    save_random_posted_ids(random_posted_ids)
                    stats["random_sent_today"] = True
                else:
                    print(
                        f"⚠️ Нет деталей для рандомного видео {random_video_id}",
                        file=sys.stderr,
                    )
                    increment_errors(stats)
            except Exception as e:
                print(f"❌ Ошибка отправки рандомного видео: {e}", file=sys.stderr)
                increment_errors(stats)
        else:
            print(
                "ℹ️ Нет доступных видео для рандомной публикации (все уже были опубликованы)"
            )

        save_stats(stats)

    # --- Основная логика: новые видео/стримы ---
    candidates = []
    try:
        candidates = find_candidate_videos()
    except Exception as e:
        print(f"❌ Ошибка получения видео с YouTube: {e}", file=sys.stderr)
        increment_errors(stats)
        save_posted_ids(posted_ids)
        save_stats(stats)
        return

    candidates_to_check = [vid for vid in candidates if vid not in posted_ids]

    force_video_id = extract_video_id(FORCE_VIDEO_URL)
    if force_video_id:
        print(f"🔗 Принудительная публикация: {force_video_id}")
        if force_video_id in posted_ids:
            posted_ids.pop(force_video_id, None)
            print(f"🔄 Удалено из posted_ids для повторной публикации")
        if force_video_id not in candidates_to_check:
            candidates_to_check.insert(0, force_video_id)

    print(f"📋 Найдено {len(candidates)} видео в плейлисте")
    print(f"🆕 Новых (не в posted_ids): {len(candidates_to_check)}")

    if CATCH_UP_ONLY:
        today_str_main = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
        for vid in candidates_to_check:
            posted_ids[vid] = today_str_main
        save_posted_ids(posted_ids)
        print(
            f"Режим CATCH_UP_ONLY: помечено как уже показанные - "
            f"{len(candidates_to_check)} видео. Ничего не опубликовано."
        )
        save_stats(stats)
        return

    details_by_id = {}
    try:
        details_by_id = get_video_details_batch(candidates_to_check)
    except Exception as e:
        print(f"❌ Ошибка получения деталей видео: {e}", file=sys.stderr)
        increment_errors(stats)
        save_posted_ids(posted_ids)
        save_stats(stats)
        return

    new_posts = 0
    today_str_main = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")

    for video_id in candidates_to_check:
        if new_posts >= MAX_POSTS_PER_RUN:
            print(
                f"Достигнут лимит {MAX_POSTS_PER_RUN} постов за запуск, "
                f"остальное - в следующий раз"
            )
            break

        details = details_by_id.get(video_id)
        if not details:
            print(f"⚠️ Нет деталей для видео {video_id}", file=sys.stderr)
            increment_errors(stats)
            continue

        snippet = details["snippet"]
        live_details = details.get("liveStreamingDetails")
        content_details = details.get("contentDetails", {})

        title = snippet["title"]
        channel_title = snippet["channelTitle"]
        thumbnail_url = best_thumbnail(snippet["thumbnails"])
        start_time_str = ""

        if live_details:
            is_live = (
                "actualStartTime" in live_details and "actualEndTime" not in live_details
            )
            is_upcoming = (
                "scheduledStartTime" in live_details and "actualStartTime" not in live_details
            )

            if not is_live and not is_upcoming:
                print(f"⏭️ Пропуск завершённого стрима: {title}")
                continue

            content_type = "live" if is_live else "upcoming"
            scheduled_start = live_details.get("scheduledStartTime")
            start_time_str = format_start_time(scheduled_start) if scheduled_start else ""
        else:
            duration_seconds = parse_duration_seconds(content_details.get("duration", ""))
            content_type = (
                "shorts" if duration_seconds <= SHORTS_MAX_DURATION_SECONDS else "video"
            )

        try:
            text = generate_announcement_text(
                content_type, title, channel_title, start_time_str
            )
        except Exception as e:
            print(f"Ошибка генерации текста для {video_id}: {e}", file=sys.stderr)
            increment_errors(stats)
            continue

        video_link = f"https://www.youtube.com/watch?v={video_id}"
        caption = text

        # Умные кнопки: Twitch/TikTok только для стримов
        if content_type in ("live", "upcoming"):
            buttons = [
                {"text": "▶️ YouTube", "url": video_link},
                {"text": "🟣 Twitch", "url": TWITCH_URL},
                {"text": "⚫️ TikTok", "url": TIKTOK_URL},
            ]
        else:
            buttons = [
                {"text": "▶️ YouTube", "url": video_link},
            ]

        print(f"📤 Попытка отправки: {title} ({video_id}) | Тип: {content_type}")
        print(f"   🖼️ Thumbnail: {thumbnail_url}")
        print(f"   📝 Caption ({len(caption)} симв.): {caption[:100]}...")

        try:
            result = send_telegram_photo(thumbnail_url, caption, buttons)
            print(f"✅ Опубликовано: {title} ({video_id})")
            increment_posts(stats)
            try:
                message_id = result["result"]["message_id"]
                react_to_message(TELEGRAM_CHAT_ID, message_id, "🔥")
            except Exception as e:
                print(f"Не удалось поставить реакцию: {e}", file=sys.stderr)
        except Exception as e:
            print(f"❌ Ошибка отправки в Telegram для {video_id}: {e}", file=sys.stderr)
            increment_errors(stats)
            continue

        # ФИКС: сохраняем posted_ids сразу после каждого поста — защита от дублей при падении
        posted_ids[video_id] = today_str_main
        new_posts += 1
        save_posted_ids(posted_ids)
        time.sleep(SECONDS_BETWEEN_POSTS)

    save_posted_ids(posted_ids)
    print(f"Готово. Новых постов: {new_posts}")

    # ФИКС: milestone тоже учитывается в stats
    check_subscriber_milestone(stats)
    save_stats(stats)


if __name__ == "__main__":
    main()
