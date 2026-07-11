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
from datetime import datetime, timezone
import zoneinfo

# ---------- Конфиг из переменных окружения ----------
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
YOUTUBE_CHANNEL_ID = os.environ["YOUTUBE_CHANNEL_ID"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")

# Ссылки на другие площадки, добавляются в конец каждого поста
TWITCH_URL = "https://www.twitch.tv/atomgit"
TIKTOK_URL = "https://www.tiktok.com/@atomgit"

STATE_FILE = os.path.join(os.path.dirname(__file__), "posted_ids.json")


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


def find_candidate_videos():
    """
    Смотрим последние видео канала (без фильтра eventType, который у YouTube
    часто обновляется с большой задержкой) и дальше в main() проверяем
    каждое видео на признаки стрима через liveStreamingDetails.
    """
    data = http_get_json(
        "https://www.googleapis.com/youtube/v3/search",
        {
            "part": "snippet",
            "channelId": YOUTUBE_CHANNEL_ID,
            "type": "video",
            "order": "date",
            "maxResults": 10,
            "key": YOUTUBE_API_KEY,
        },
    )
    return [item["id"]["videoId"] for item in data.get("items", [])]


def get_video_details(video_id):
    data = http_get_json(
        "https://www.googleapis.com/youtube/v3/videos",
        {
            "part": "snippet,liveStreamingDetails",
            "id": video_id,
            "key": YOUTUBE_API_KEY,
        },
    )
    items = data.get("items", [])
    if not items:
        return None
    return items[0]


def best_thumbnail(thumbnails):
    for key in ("maxres", "standard", "high", "medium", "default"):
        if key in thumbnails:
            return thumbnails[key]["url"]
    return None


def format_start_time(iso_ts):
    dt_utc = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    local = dt_utc.astimezone(zoneinfo.ZoneInfo(TIMEZONE))
    return local.strftime("%d.%m.%Y в %H:%M") + f" ({TIMEZONE.split('/')[-1]})"


LIVE_TEMPLATES = [
    "🔴 Внимание! {channel} начал стрим прямо сейчас!\n«{title}»\nЗаходи, пока горячо 👇",
    "🔴 Мы уже в эфире! {channel} стримит:\n«{title}»\nПодключайся, будет интересно!",
    "🔴 Стрим уже идёт! «{title}» от {channel} — залетай в трансляцию 🚀",
]

UPCOMING_TEMPLATES = [
    "📢 Скоро стрим! {channel} проведёт трансляцию «{title}»\n🕒 {when}\nСтавь напоминание, чтобы не пропустить!",
    "📅 Анонс: «{title}»\nКанал: {channel}\n⏰ Начало: {when}\nЖдём всех на YouTube!",
    "🎬 Готовь чай/кофе — уже {when} стартует «{title}» от {channel}. Не пропусти!",
]


def generate_announcement_text(title, channel_title, start_time_str, is_live):
    """Генерируем текст анонса по шаблону (без внешних AI-сервисов, бесплатно)."""
    if is_live:
        template = random.choice(LIVE_TEMPLATES)
        return template.format(channel=channel_title, title=title)
    else:
        template = random.choice(UPCOMING_TEMPLATES)
        return template.format(channel=channel_title, title=title, when=start_time_str)


def send_telegram_photo(photo_url, caption):
    body = urllib.parse.urlencode(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": photo_url,
            "caption": caption,
        }
    ).encode("utf-8")
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


def main():
    posted_ids = load_posted_ids()
    candidates = find_candidate_videos()

    new_posts = 0
    for video_id in candidates:
        if video_id in posted_ids:
            continue

        details = get_video_details(video_id)
        if not details:
            continue

        snippet = details["snippet"]
        live_details = details.get("liveStreamingDetails")

        # Пропускаем обычные видео - это не стрим вообще
        if not live_details:
            continue

        is_live = "actualStartTime" in live_details and "actualEndTime" not in live_details
        is_upcoming = "scheduledStartTime" in live_details and "actualStartTime" not in live_details

        # Пропускаем уже завершившиеся стримы - анонсировать их незачем
        if not is_live and not is_upcoming:
            continue

        title = snippet["title"]
        channel_title = snippet["channelTitle"]
        thumbnail_url = best_thumbnail(snippet["thumbnails"])

        scheduled_start = live_details.get("scheduledStartTime")
        start_time_str = format_start_time(scheduled_start) if scheduled_start else ""

        try:
            text = generate_announcement_text(title, channel_title, start_time_str, is_live)
        except Exception as e:
            print(f"Ошибка генерации текста для {video_id}: {e}", file=sys.stderr)
            continue

        video_link = f"https://www.youtube.com/watch?v={video_id}"
        caption = (
            f"{text}\n\n"
            f"▶️ YouTube: {video_link}\n"
            f"🟣 Twitch: {TWITCH_URL}\n"
            f"⬛ TikTok: {TIKTOK_URL}"
        )

        try:
            send_telegram_photo(thumbnail_url, caption)
            print(f"Опубликовано: {title} ({video_id})")
        except Exception as e:
            print(f"Ошибка отправки в Telegram для {video_id}: {e}", file=sys.stderr)
            continue

        posted_ids.add(video_id)
        new_posts += 1

    save_posted_ids(posted_ids)
    print(f"Готово. Новых постов: {new_posts}")


if __name__ == "__main__":
    main()
