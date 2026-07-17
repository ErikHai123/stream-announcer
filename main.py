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
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")
TIMEZONE_LABEL = os.environ.get("TIMEZONE_LABEL", "МСК")
SECOND_TIMEZONE = os.environ.get("SECOND_TIMEZONE", "Asia/Almaty")
SECOND_TIMEZONE_LABEL = os.environ.get("SECOND_TIMEZONE_LABEL", "Казахстан")

TWITCH_URL = "https://www.twitch.tv/atomgit"
TIKTOK_URL = "https://www.tiktok.com/@atomgit"

SUBSCRIBER_MILESTONE_STEP = int(os.environ.get("SUBSCRIBER_MILESTONE_STEP", "10000"))

STATE_FILE = os.path.join(os.path.dirname(__file__), "posted_ids.json")
MILESTONE_STATE_FILE = os.path.join(os.path.dirname(__file__), "milestone_state.json")


def load_last_milestone():
    if os.path.exists(MILESTONE_STATE_FILE):
        with open(MILESTONE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("last_milestone", 0), True
    return 0, False


def save_last_milestone(value):
    with open(MILESTONE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_milestone": value}, f, ensure_ascii=False, indent=2)


def load_posted_ids():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_posted_ids(ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)


def http_get_json(url, params):
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"
    try:
        with urllib.request.urlopen(full_url) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"HTTP {e.code} ошибка при запросе к {url}", file=sys.stderr)
        print(f"Подробности: {error_body}", file=sys.stderr)
        raise


# ========== ИЗМЕНЕНИЕ 1: Новая функция — получаем ID uploads-плейлиста ==========
def get_uploads_playlist_id():
    """Получаем ID плейлиста 'Загруженные' канала (1 юнит API)."""
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


# ========== ИЗМЕНЕНИЕ 2: Заменили search.list на playlistItems.list ==========
def find_candidate_videos():
    """
    Берём последние видео из uploads-плейлиста канала (1 юнит API)
    вместо search.list (100 юнитов).
    """
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
    """Получаем данные сразу по всем видео одним запросом (1 юнит API)."""
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
    """Получаем текущее число подписчиков и название канала (1 юнит API)."""
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
]

DEFAULT_THEME_EMOJI = "🔴"


def detect_theme_emoji(title):
    lowered = title.lower()
    for keyword, emoji in GAME_EMOJIS:
        if keyword in lowered:
            return emoji
    return DEFAULT_THEME_EMOJI


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


def generate_announcement_text(content_type, title, channel_title, start_time_str=""):
    templates_map = {
        "live": LIVE_TEMPLATES,
        "upcoming": UPCOMING_TEMPLATES,
        "video": VIDEO_TEMPLATES,
        "shorts": SHORTS_TEMPLATES,
    }
    template = random.choice(templates_map[content_type])
    emoji = detect_theme_emoji(title)
    return template.format(channel=channel_title, title=title, when=start_time_str, emoji=emoji)


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
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")
    return result


MILESTONE_TEMPLATES = [
    "🎉 Ура! У {channel} уже {count} подписчиков!\nСпасибо, что вы с нами — это только начало 🚀",
    "🎊 Юбилей! {channel} набрал(а) {count} подписчиков!\nОгромное спасибо каждому из вас ❤️",
    "🥳 {count} подписчиков у {channel}!\nСпасибо за поддержку, дальше — больше!",
]


def check_subscriber_milestone():
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
        text = random.choice(MILESTONE_TEMPLATES).format(channel=channel_title, count=current_milestone)
        try:
            result = send_telegram_message(TELEGRAM_CHAT_ID, text)
            print(f"Опубликовано поздравление с {current_milestone} подписчиками")
            try:
                message_id = result["result"]["message_id"]
                react_to_message(TELEGRAM_CHAT_ID, message_id, "🎉")
            except Exception as e:
                print(f"Не удалось поставить реакцию: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Ошибка отправки поздравления: {e}", file=sys.stderr)
            return
        save_last_milestone(current_milestone)


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
    with urllib.request.urlopen(req) as resp:
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
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")
    return result


SHORTS_MAX_DURATION_SECONDS = 60
MAX_POSTS_PER_RUN = 3
SECONDS_BETWEEN_POSTS = 3
CATCH_UP_ONLY = os.environ.get("CATCH_UP_ONLY", "false").lower() == "true"


def main():
    posted_ids = load_posted_ids()
    candidates = find_candidate_videos()
    candidates_to_check = [vid for vid in candidates if vid not in posted_ids]

    if CATCH_UP_ONLY:
        posted_ids.update(candidates_to_check)
        save_posted_ids(posted_ids)
        print(
            f"Режим CATCH_UP_ONLY: помечено как уже показанные - "
            f"{len(candidates_to_check)} видео. Ничего не опубликовано."
        )
        return

    details_by_id = get_video_details_batch(candidates_to_check)

    new_posts = 0
    for video_id in candidates_to_check:
        if new_posts >= MAX_POSTS_PER_RUN:
            print(f"Достигнут лимит {MAX_POSTS_PER_RUN} постов за запуск, остальное - в следующий раз")
            break

        details = details_by_id.get(video_id)
        if not details:
            continue

        snippet = details["snippet"]
        live_details = details.get("liveStreamingDetails")
        content_details = details.get("contentDetails", {})

        title = snippet["title"]
        channel_title = snippet["channelTitle"]
        thumbnail_url = best_thumbnail(snippet["thumbnails"])
        start_time_str = ""

        if live_details:
            is_live = "actualStartTime" in live_details and "actualEndTime" not in live_details
            is_upcoming = "scheduledStartTime" in live_details and "actualStartTime" not in live_details

            if not is_live and not is_upcoming:
                continue

            content_type = "live" if is_live else "upcoming"
            scheduled_start = live_details.get("scheduledStartTime")
            start_time_str = format_start_time(scheduled_start) if scheduled_start else ""
        else:
            duration_seconds = parse_duration_seconds(content_details.get("duration", ""))
            content_type = "shorts" if duration_seconds <= SHORTS_MAX_DURATION_SECONDS else "video"

        try:
            text = generate_announcement_text(content_type, title, channel_title, start_time_str)
        except Exception as e:
            print(f"Ошибка генерации текста для {video_id}: {e}", file=sys.stderr)
            continue

        video_link = f"https://www.youtube.com/watch?v={video_id}"
        caption = text
        buttons = [
            {"text": "▶️ YouTube", "url": video_link},
            {"text": "🟣 Twitch", "url": TWITCH_URL},
            {"text": "⚫️ TikTok", "url": TIKTOK_URL},
        ]

        try:
            result = send_telegram_photo(thumbnail_url, caption, buttons)
            print(f"Опубликовано: {title} ({video_id})")
            try:
                message_id = result["result"]["message_id"]
                react_to_message(TELEGRAM_CHAT_ID, message_id, "🔥")
            except Exception as e:
                print(f"Не удалось поставить реакцию: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Ошибка отправки в Telegram для {video_id}: {e}", file=sys.stderr)
            continue

        posted_ids.add(video_id)
        new_posts += 1
        time.sleep(SECONDS_BETWEEN_POSTS)

    save_posted_ids(posted_ids)
    print(f"Готово. Новых постов: {new_posts}")

    check_subscriber_milestone()


if __name__ == "__main__":
    main()
