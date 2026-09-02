"""
Stream Announcer Bot + Live Polls
"""

import os, json, random, sys, urllib.request, urllib.parse, urllib.error, re, time
from datetime import datetime, timezone
import zoneinfo

# ---------- Config ----------
YOUTUBE_API_KEY       = os.environ["YOUTUBE_API_KEY"]
YOUTUBE_CHANNEL_ID    = os.environ["YOUTUBE_CHANNEL_ID"]
TELEGRAM_BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID      = os.environ["TELEGRAM_CHAT_ID"]
ADMIN_CHAT_ID         = os.environ.get("ADMIN_CHAT_ID", "")
TIMEZONE              = os.environ.get("TIMEZONE", "Europe/Moscow")
TIMEZONE_LABEL        = os.environ.get("TIMEZONE_LABEL", "МСК")
SECOND_TIMEZONE       = os.environ.get("SECOND_TIMEZONE", "Asia/Almaty")
SECOND_TIMEZONE_LABEL = os.environ.get("SECOND_TIMEZONE_LABEL", "Казахстан")

REPO      = "ErikHai123"
REPO_NAME = "stream-announcer"
TWITCH_URL = "https://www.twitch.tv/atomgit"
TIKTOK_URL = "https://www.tiktok.com/@atomgit"

SUBSCRIBER_MILESTONE_STEP = int(os.environ.get("SUBSCRIBER_MILESTONE_STEP", "10000"))

STATE_FILE       = os.path.join(os.path.dirname(__file__), "posted_ids.json")
MILESTONE_FILE   = os.path.join(os.path.dirname(__file__), "milestone_state.json")
STATS_FILE       = os.path.join(os.path.dirname(__file__), "daily_stats.json")
RANDOM_FILE      = os.path.join(os.path.dirname(__file__), "random_posted_ids.json")
LIVE_POLL_FILE   = os.path.join(os.path.dirname(__file__), "live_poll_state.json")

_RUN_LOCK_FILE = os.path.join(os.path.dirname(__file__), ".last_run")
_MIN_RUN_INTERVAL_SECONDS = 60
_POLL_DELAY_SECONDS       = 300

_POSTED_IDS_MAX_AGE_DAYS  = 30
_RANDOM_POSTED_MAX_AGE_DAYS = 90
_MAX_PAGES_FOR_RANDOM     = 5

# ---------- Stats ----------
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ Ошибка чтения stats: {e}", file=sys.stderr)
    return {"current_date":"","posts_today":0,"errors_today":0,
            "report_sent_today":False,"random_sent_today":False}

def save_stats(stats):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def increment_posts(stats):  stats["posts_today"] = stats.get("posts_today",0)+1
def increment_errors(stats):   stats["errors_today"] = stats.get("errors_today",0)+1

def send_daily_report(stats):
    if not ADMIN_CHAT_ID: return
    date_str = datetime.now(zoneinfo.ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
    posts, errors = stats.get("posts_today",0), stats.get("errors_today",0)
    text = f"📊 Отчёт за {date_str}\n✅ Постов: {posts}\n❌ Ошибок: {errors}"
    if errors > 10:
        text += f"\n🔗 Логи: https://github.com/{REPO}/{REPO_NAME}/actions"
    elif posts == 0 and errors == 0:
        text += "\n💤 Сегодня тихо, но бот на месте!"
    send_telegram_message(ADMIN_CHAT_ID, text)
    print(f"📤 Отчёт отправлен: {posts} постов, {errors} ошибок")

# ---------- Rate limit ----------
def _check_rate_limit():
    if os.path.exists(_RUN_LOCK_FILE):
        try:
            with open(_RUN_LOCK_FILE, "r", encoding="utf-8") as f:
                last_run = float(f.read().strip())
            if time.time() - last_run < _MIN_RUN_INTERVAL_SECONDS:
                print(f"⏳ Слишком частый запуск ({int(time.time()-last_run)}с назад). Пропускаем.")
                return False
        except (ValueError, OSError): pass
    with open(_RUN_LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(time.time()))
    return True

# ---------- posted_ids ----------
def load_posted_ids():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                today = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
                return {vid:today for vid in data}
            elif isinstance(data, dict): return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ Ошибка posted_ids: {e}", file=sys.stderr)
    return {}

def save_posted_ids(ids_dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(ids_dict, f, ensure_ascii=False, indent=2)

def clean_old_posted_ids(posted_ids, days=_POSTED_IDS_MAX_AGE_DAYS):
    if not posted_ids: return posted_ids
    today = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).date()
    to_remove = []
    for vid, date_str in list(posted_ids.items()):
        try:
            post_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if (today - post_date).days > days: to_remove.append(vid)
        except (ValueError, TypeError): to_remove.append(vid)
    for vid in to_remove: del posted_ids[vid]
    if to_remove: print(f"🧹 Очищено старых ID: {len(to_remove)}")
    return posted_ids

# ---------- Random posted ----------
def load_random_posted_ids():
    if os.path.exists(RANDOM_FILE):
        try:
            with open(RANDOM_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError): pass
    return {}
def save_random_posted_ids(ids_dict):
    with open(RANDOM_FILE, "w", encoding="utf-8") as f:
        json.dump(ids_dict, f, ensure_ascii=False, indent=2)

def clean_old_random_posted_ids(random_posted_ids, days=_RANDOM_POSTED_MAX_AGE_DAYS):
    if not random_posted_ids: return random_posted_ids
    today = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).date()
    to_remove = []
    for vid, date_str in list(random_posted_ids.items()):
        try:
            post_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if (today - post_date).days > days: to_remove.append(vid)
        except (ValueError, TypeError): to_remove.append(vid)
    for vid in to_remove: del random_posted_ids[vid]
    if to_remove: print(f"🧹 Очищено старых random ID: {len(to_remove)}")
    return random_posted_ids

def get_all_uploads(max_pages=_MAX_PAGES_FOR_RANDOM):
    playlist_id = get_uploads_playlist_id()
    if not playlist_id: return []
    video_ids, next_page_token, pages = [], None, 0
    while pages < max_pages:
        params = {"part":"snippet","playlistId":playlist_id,"maxResults":50,"key":YOUTUBE_API_KEY}
        if next_page_token: params["pageToken"] = next_page_token
        try:
            data = http_get_json("https://www.googleapis.com/youtube/v3/playlistItems", params)
        except Exception as e:
            print(f"⚠️ Ошибка загрузки uploads: {e}", file=sys.stderr); break
        for item in data.get("items",[]): video_ids.append(item["snippet"]["resourceId"]["videoId"])
        next_page_token = data.get("nextPageToken"); pages += 1
        if not next_page_token: break
    return video_ids

def get_random_unposted_video(posted_ids, random_posted_ids):
    all_videos = get_all_uploads()
    available = [vid for vid in all_videos if vid not in posted_ids and vid not in random_posted_ids]
    return random.choice(available) if available else None

# ---------- Live poll state ----------
def load_live_poll_state():
    if os.path.exists(LIVE_POLL_FILE):
        try:
            with open(LIVE_POLL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError): pass
    return {}

def save_live_poll_state(state):
    with open(LIVE_POLL_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def clean_live_poll_state(state):
    if not state: return state
    now = datetime.now(timezone.utc)
    to_remove = []
    for vid, data in list(state.items()):
        try:
            announced = datetime.fromisoformat(data.get("announced_at",""))
            if (now - announced).total_seconds() > 43200: to_remove.append(vid)
        except (ValueError, TypeError): to_remove.append(vid)
    for vid in to_remove: del state[vid]
    if to_remove: print(f"🧹 Очищено старых live-poll: {len(to_remove)}")
    return state

# ---------- Milestone ----------
def load_last_milestone():
    if os.path.exists(MILESTONE_FILE):
        try:
            with open(MILESTONE_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("last_milestone",0), True
        except (json.JSONDecodeError, OSError): pass
    return 0, False
def save_last_milestone(value):
    with open(MILESTONE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_milestone":value}, f, ensure_ascii=False, indent=2)

# ---------- HTTP ----------
def http_get_json(url, params, timeout=15):
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"
    try:
        req = urllib.request.Request(full_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"HTTP {e.code}: {url}", file=sys.stderr)
        print(f"Подробности: {error_body}", file=sys.stderr); raise
    except urllib.error.URLError as e:
        print(f"Сеть: {url} — {e.reason}", file=sys.stderr); raise

# ---------- YouTube ----------
def get_uploads_playlist_id():
    data = http_get_json("https://www.googleapis.com/youtube/v3/channels",
                         {"part":"contentDetails","id":YOUTUBE_CHANNEL_ID,"key":YOUTUBE_API_KEY})
    items = data.get("items",[])
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"] if items else None

def find_candidate_videos():
    playlist_id = get_uploads_playlist_id()
    if not playlist_id: return []
    data = http_get_json("https://www.googleapis.com/youtube/v3/playlistItems",
                         {"part":"snippet","playlistId":playlist_id,"maxResults":50,"key":YOUTUBE_API_KEY})
    return [it["snippet"]["resourceId"]["videoId"] for it in data.get("items",[])]

def get_video_details_batch(video_ids):
    if not video_ids: return {}
    data = http_get_json("https://www.googleapis.com/youtube/v3/videos",
                         {"part":"snippet,liveStreamingDetails,contentDetails",
                          "id":",".join(video_ids),"key":YOUTUBE_API_KEY})
    return {it["id"]:it for it in data.get("items",[])}

def get_channel_info():
    data = http_get_json("https://www.googleapis.com/youtube/v3/channels",
                         {"part":"snippet,statistics","id":YOUTUBE_CHANNEL_ID,"key":YOUTUBE_API_KEY})
    items = data.get("items",[])
    if not items: return None, None
    return int(items[0]["statistics"]["subscriberCount"]), items[0]["snippet"]["title"]

def best_thumbnail(thumbnails):
    for k in ("maxres","standard","high","medium","default"):
        if k in thumbnails: return thumbnails[k]["url"]
    return None

def format_start_time(iso_ts):
    dt_utc = datetime.fromisoformat(iso_ts.replace("Z","+00:00")).astimezone(timezone.utc)
    local = dt_utc.astimezone(zoneinfo.ZoneInfo(TIMEZONE))
    main_str = local.strftime("%d.%m.%Y в %H:%M") + f" ({TIMEZONE_LABEL})"
    second = dt_utc.astimezone(zoneinfo.ZoneInfo(SECOND_TIMEZONE))
    second_str = second.strftime("%H:%M") + f" ({SECOND_TIMEZONE_LABEL})"
    return f"{main_str} / {second_str}"

def parse_duration_seconds(iso_duration):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration or "")
    if not m: return 0
    h, mn, s = (int(g) if g else 0 for g in m.groups())
    return h*3600 + mn*60 + s

def extract_video_id(url):
    if not url or not url.strip(): return None
    for pat in [r"(?:v=|\/)([0-9A-Za-z_-]{11})",r"(?:embed\/)([0-9A-Za-z_-]{11})",
                r"(?:youtu\.be\/)([0-9A-Za-z_-]{11})",r"(?:shorts\/)([0-9A-Za-z_-]{11})"]:
        m = re.search(pat, url)
        if m: return m.group(1)
    return None

# ---------- Emojis ----------
GAME_EMOJIS = [
    ("gta","🚗"),("гта","🚗"),("farcry","🔫"),("far cry","🔫"),("cyberpunk","🤖"),
    ("cs2","🔫"),("csgo","🔫"),("cs 1.6","🔫"),("counter-strike","🔫"),
    ("minecraft","⛏️"),("майнкрафт","⛏️"),("f1","🏎️"),("formula","🏎️"),("fifa","⚽"),
    ("repo","🤖"),("battlefield","💣"),("valorant","🔫"),("dota","⚔️"),("fortnite","🔫"),
    ("warzone","🔫"),("elden ring","⚔️"),("stalker","☢️"),("roblox","🧱"),("apex","🔫"),
    ("overwatch","🔫"),("wow","⚔️"),("world of warcraft","⚔️"),("rocket league","⚽"),
    ("fall guys","🎮"),("among us","🎮"),
]
DEFAULT_THEME_EMOJI = "🔴"

def detect_theme_emoji(title):
    lowered = title.lower()
    for kw, em in GAME_EMOJIS:
        if kw in lowered: return em
    return DEFAULT_THEME_EMOJI

# ---------- Poll config ----------
GAME_POLL_CONFIG = {
    "gta": {"question":"🚗 Какие карты сегодня будем проходить?",
            "options":["Паркуры","Скилл-тесты","Ларги","Стенки","Спуски","Дрифт-трассы","Паркур-лолы"]},
    "гта": {"question":"🚗 Какие карты сегодня будем проходить?",
            "options":["Паркуры","Скилл-тесты","Ларги","Стенки","Спуски","Дрифт-трассы","Паркур-лолы"]},
    "cs2": {"question":"🔫 Какой режим сегодня ждёте?",
            "options":["ММ (соло)","FaceIt","Кастомки","Зомби-мод","1v1 Арена","Эскалация"]},
    "csgo": {"question":"🔫 Какой режим сегодня ждёте?",
             "options":["ММ (соло)","FaceIt","Кастомки","Зомби-мод","1v1 Арена","Эскалация"]},
    "minecraft": {"question":"⛏️ Что строим/делаем сегодня?",
                  "options":["Хардкор выживание","Паркур карта","ПвП арена","Авто-ферма","Бедварс","Скайблок"]},
    "майнкрафт": {"question":"⛏️ Что строим/делаем сегодня?",
                  "options":["Хардкор выживание","Паркур карта","ПвП арена","Авто-ферма","Бедварс","Скайблок"]},
    "fifa": {"question":"⚽ Какой режим FIFA сегодня?",
             "options":["FUT Champions","Карьера","Pro Clubs","Volta","Драфт"]},
    "f1": {"question":"🏎️ Какой формат гонки сегодня?",
           "options":["Гранд-При (50%)","Гранд-При (100%)","Спринт","Квалификация","Мультиплеер"]},
    "formula": {"question":"🏎️ Какой формат гонки сегодня?",
                "options":["Гранд-При (50%)","Гранд-При (100%)","Спринт","Квалификация","Мультиплеер"]},
    "cyberpunk": {"question":"🤖 Что сегодня в Cyberpunk?",
                  "options":["Сюжетка","Рандомные квесты","Полиция vs Гангстеры","Фотомод","Боссы"]},
    "repo": {"question":"🤖 Какой уровень сложности в Repo?",
             "options":["Лёгкий","Средний","Сложный","Кошмар","Соло-челлендж"]},
    "battlefield": {"question":"💣 Какой режим Battlefield сегодня?",
                    "options":["Захват","Прорыв","Штурм","Техника","Снайпер only"]},
    "valorant": {"question":"🔫 Какой агент/режим в Valorant?",
                 "options":["Рейтинг","Связка","Дезматч","Эскалация","Кастомки"]},
    "dota": {"question":"⚔️ Какая роль сегодня в Dota?",
             "options":["Керри","Мид","Оффлейн","Саппорт 4","Саппорт 5","Все рандом"]},
    "fortnite": {"question":"🔫 Что играем в Fortnite?",
                 "options":["Рояль","Творческий","ЗБС","Ранкед","Кастомки"]},
    "warzone": {"question":"🔫 Какой режим Warzone?",
                "options":["Большая карта","Решта","Ранкед","Соло","Отряд"]},
    "elden ring": {"question":"⚔️ Что сегодня в Elden Ring?",
                   "options":["Боссы","ПвП арена","НГ+","Кооп","Исследование"]},
    "stalker": {"question":"☢️ Какая зона сегодня в Stalker?",
                "options":["Кордон","Бар","Припять","ЧАЭС","Подземелья","Аномалии"]},
    "roblox": {"question":"🧱 Какая игра в Roblox сегодня?",
               "options":["Doors","Tower of Hell","Brookhaven","BedWars","Мини-игры"]},
    "apex": {"question":"🔫 Какой режим Apex сегодня?",
             "options":["Баттл-ройал","Арена","Ранкед","ЛТМ","Трио"]},
    "overwatch": {"question":"🔫 Какой режим Overwatch?",
                  "options":["Рейтинг","Быстрая игра","Аркада","Кастомки","Пуш"]},
    "wow": {"question":"⚔️ Что сегодня в WoW?",
            "options":["Подземелья","Рейд","PvP","Прокачка","Торговля"]},
    "world of warcraft": {"question":"⚔️ Что сегодня в WoW?",
                          "options":["Подземелья","Рейд","PvP","Прокачка","Торговля"]},
    "rocket league": {"question":"⚽ Какой режим Rocket League?",
                      "options":["1v1","2v2","3v3","Хоккей","Румбл","Дропшот"]},
    "fall guys": {"question":"🎮 Какой раунд Fall Guys ждёте?",
                  "options":["Захват короны","Командные","Выживание","Гонки","Финал"]},
    "among us": {"question":"🎮 Какая карта Among Us сегодня?",
                 "options":["The Skeld","MIRA HQ","Polus","Airship","Fungle"]},
    "farcry": {"question":"🔫 Что сегодня в Far Cry?",
               "options":["Сюжет","Аванпосты","Охота","Кооп","Экспедиции"]},
    "far cry": {"question":"🔫 Что сегодня в Far Cry?",
                "options":["Сюжет","Аванпосты","Охота","Кооп","Экспедиции"]},
}

def detect_game_for_poll(title):
    lowered = title.lower()
    for keyword, config in GAME_POLL_CONFIG.items():
        if keyword in lowered:
            return config
    return None

# ---------- Templates ----------
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
    tm = {"live":LIVE_TEMPLATES,"upcoming":UPCOMING_TEMPLATES,
          "video":VIDEO_TEMPLATES,"shorts":SHORTS_TEMPLATES,"random":RANDOM_TEMPLATES}
    tpl = random.choice(tm[content_type])
    return tpl.format(channel=channel_title, title=title, when=start_time_str, emoji=detect_theme_emoji(title))

# ---------- Telegram ----------
def send_telegram_message(chat_id, text):
    body = urllib.parse.urlencode({"chat_id":chat_id,"text":text}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                 data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")
    return result

def react_to_message(chat_id, message_id, emoji="🔥"):
    body = urllib.parse.urlencode({
        "chat_id":chat_id,"message_id":message_id,
        "reaction":json.dumps([{"type":"emoji","emoji":emoji}]),
    }).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMessageReaction",
                                 data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")
    return result

def send_telegram_photo(photo_url, caption, buttons=None):
    params = {"chat_id":TELEGRAM_CHAT_ID,"photo":photo_url,"caption":caption}
    if buttons:
        params["reply_markup"] = json.dumps({"inline_keyboard":[buttons]})
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                                 data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"❌ Telegram HTTP {e.code}: {e.read().decode('utf-8')}", file=sys.stderr)
        print(f"📊 Caption: {len(caption)} симв.", file=sys.stderr)
        raise RuntimeError(f"Telegram error {e.code}")
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")
    return result

def send_telegram_poll(chat_id, question, options, allows_multiple=True):
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "question": question,
        "options": json.dumps(options),
        "is_anonymous": "false",
        "allows_multiple_answers": "true" if allows_multiple else "false",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPoll",
        data=body, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram poll error: {result}")
    return result

# ---------- Milestone ----------
MILESTONE_TEMPLATES = [
    "🎉 Ура! У {channel} уже {count} подписчиков!\nСпасибо, что вы с нами — это только начало 🚀",
    "🎊 Юбилей! {channel} набрал(а) {count} подписчиков!\nОгромное спасибо каждому из вас ❤️",
    "🥳 {count} подписчиков у {channel}!\nСпасибо за поддержку, дальше — больше!",
]

def check_subscriber_milestone(stats):
    try:
        count, channel_title = get_channel_info()
    except Exception as e:
        print(f"Не удалось получить число подписчиков: {e}", file=sys.stderr); return
    if count is None: return
    current = (count // SUBSCRIBER_MILESTONE_STEP) * SUBSCRIBER_MILESTONE_STEP
    last, existed = load_last_milestone()
    if not existed:
        save_last_milestone(current)
        print(f"Отметка подписчиков инициализирована: {current}"); return
    if current > last and current > 0:
        text = random.choice(MILESTONE_TEMPLATES).format(channel=channel_title, count=current)
        try:
            result = send_telegram_message(TELEGRAM_CHAT_ID, text)
            print(f"Опубликовано поздравление с {current} подписчиками")
            increment_posts(stats)
            try:
                react_to_message(TELEGRAM_CHAT_ID, result["result"]["message_id"], "🎉")
            except Exception as e:
                print(f"Не удалось поставить реакцию: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Ошибка отправки поздравления: {e}", file=sys.stderr)
            increment_errors(stats); return
        save_last_milestone(current)

# ---------- Main ----------
SHORTS_MAX_DURATION_SECONDS = 60
MAX_POSTS_PER_RUN = 3
SECONDS_BETWEEN_POSTS = 3
CATCH_UP_ONLY = os.environ.get("CATCH_UP_ONLY","false").lower() == "true"
FORCE_VIDEO_URL = os.environ.get("FORCE_VIDEO_URL","")

def main():
    stats = load_stats()
    now = datetime.now(zoneinfo.ZoneInfo("Europe/Moscow"))
    today_str = now.strftime("%Y-%m-%d")

    if stats.get("current_date") != today_str:
        stats = {"current_date":today_str,"posts_today":0,"errors_today":0,
                 "report_sent_today":False,"random_sent_today":False}

    if now.hour == 23 and not stats.get("report_sent_today", False):
        try:
            send_daily_report(stats)
            stats["report_sent_today"] = True
        except Exception as e:
            print(f"❌ Ошибка отчёта: {e}", file=sys.stderr)
            increment_errors(stats)
        save_stats(stats)

    if not _check_rate_limit():
        save_stats(stats); return

    posted_ids = load_posted_ids()
    posted_ids = clean_old_posted_ids(posted_ids)

    # --- Random video at 15:00 ---
    if now.hour == 15 and not stats.get("random_sent_today", False):
        random_posted_ids = load_random_posted_ids()
        random_posted_ids = clean_old_random_posted_ids(random_posted_ids)
        rvid = get_random_unposted_video(posted_ids, random_posted_ids)
        if rvid:
            print(f"🎲 Рандом: {rvid}")
            try:
                det = get_video_details_batch([rvid]).get(rvid)
                if det:
                    snip = det["snippet"]
                    title, chtitle = snip["title"], snip["channelTitle"]
                    thumb = best_thumbnail(snip["thumbnails"])
                    text = generate_announcement_text("random", title, chtitle)
                    link = f"https://www.youtube.com/watch?v={rvid}"
                    res = send_telegram_photo(thumb, text, [{"text":"▶️ YouTube","url":link}])
                    print(f"✅ Рандом опубликован: {title}")
                    increment_posts(stats)
                    try:
                        react_to_message(TELEGRAM_CHAT_ID, res["result"]["message_id"], "❤️")
                    except Exception as e:
                        print(f"Реакция не поставлена: {e}", file=sys.stderr)
                    posted_ids[rvid] = today_str; save_posted_ids(posted_ids)
                    random_posted_ids[rvid] = today_str; save_random_posted_ids(random_posted_ids)
                    stats["random_sent_today"] = True
                else:
                    print(f"⚠️ Нет деталей для {rvid}", file=sys.stderr)
                    increment_errors(stats)
            except Exception as e:
                print(f"❌ Ошибка рандома: {e}", file=sys.stderr)
                increment_errors(stats)
        else:
            print("ℹ️ Нет видео для рандома")
        save_stats(stats)

    # --- Main logic ---
    try:
        candidates = find_candidate_videos()
    except Exception as e:
        print(f"❌ Ошибка YouTube: {e}", file=sys.stderr)
        increment_errors(stats); save_posted_ids(posted_ids); save_stats(stats); return

    candidates_to_check = [vid for vid in candidates if vid not in posted_ids]
    force_id = extract_video_id(FORCE_VIDEO_URL)
    if force_id:
        print(f"🔗 Принудительно: {force_id}")
        if force_id in posted_ids:
            posted_ids.pop(force_id, None)
            print("🔄 Удалено из posted_ids")
        if force_id not in candidates_to_check:
            candidates_to_check.insert(0, force_id)

    print(f"📋 {len(candidates)} видео, 🆕 {len(candidates_to_check)} новых")

    if CATCH_UP_ONLY:
        tsm = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
        for vid in candidates_to_check: posted_ids[vid] = tsm
        save_posted_ids(posted_ids)
        print(f"CATCH_UP_ONLY: {len(candidates_to_check)} помечены.")
        save_stats(stats); return

    try:
        details_by_id = get_video_details_batch(candidates_to_check)
    except Exception as e:
        print(f"❌ Ошибка деталей: {e}", file=sys.stderr)
        increment_errors(stats); save_posted_ids(posted_ids); save_stats(stats); return

    new_posts = 0
    tsm = datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
    live_poll_state = load_live_poll_state()
    live_poll_state = clean_live_poll_state(live_poll_state)

    for video_id in candidates_to_check:
        if new_posts >= MAX_POSTS_PER_RUN:
            print(f"Лимит {MAX_POSTS_PER_RUN} постов достигнут."); break

        det = details_by_id.get(video_id)
        if not det:
            print(f"⚠️ Нет деталей {video_id}", file=sys.stderr)
            increment_errors(stats); continue

        snip = det["snippet"]
        live = det.get("liveStreamingDetails")
        cd = det.get("contentDetails",{})
        title = snip["title"]
        chtitle = snip["channelTitle"]
        thumb = best_thumbnail(snip["thumbnails"])
        start_str = ""

        if live:
            is_live = "actualStartTime" in live and "actualEndTime" not in live
            is_upcoming = "scheduledStartTime" in live and "actualStartTime" not in live
            if not is_live and not is_upcoming:
                print(f"⏭️ Пропуск завершённого: {title}"); continue
            ctype = "live" if is_live else "upcoming"
            ss = live.get("scheduledStartTime")
            start_str = format_start_time(ss) if ss else ""
        else:
            ds = parse_duration_seconds(cd.get("duration",""))
            ctype = "shorts" if ds <= SHORTS_MAX_DURATION_SECONDS else "video"

        try:
            text = generate_announcement_text(ctype, title, chtitle, start_str)
        except Exception as e:
            print(f"Ошибка текста {video_id}: {e}", file=sys.stderr)
            increment_errors(stats); continue

        link = f"https://www.youtube.com/watch?v={video_id}"
        if ctype in ("live","upcoming"):
            buttons = [{"text":"▶️ YouTube","url":link},
                       {"text":"🟣 Twitch","url":TWITCH_URL},
                       {"text":"⚫️ TikTok","url":TIKTOK_URL}]
        else:
            buttons = [{"text":"▶️ YouTube","url":link}]

        print(f"📤 {title} ({video_id}) | {ctype}")
        try:
            res = send_telegram_photo(thumb, text, buttons)
            print(f"✅ Опубликовано: {title}")
            increment_posts(stats)
            try:
                react_to_message(TELEGRAM_CHAT_ID, res["result"]["message_id"], "🔥")
            except Exception as e:
                print(f"Реакция: {e}", file=sys.stderr)

            if ctype == "live":
                if video_id not in live_poll_state:
                    live_poll_state[video_id] = {
                        "announced_at": datetime.now(timezone.utc).isoformat(),
                        "poll_sent": False,
                        "poll_message_id": None,
                        "title": title,
                    }
                    print(f"📝 LIVE {video_id} добавлен в очередь опроса")
        except Exception as e:
            print(f"❌ Ошибка Telegram {video_id}: {e}", file=sys.stderr)
            increment_errors(stats); continue

        posted_ids[video_id] = tsm
        new_posts += 1
        save_posted_ids(posted_ids)
        time.sleep(SECONDS_BETWEEN_POSTS)

    save_posted_ids(posted_ids)
    print(f"Готово. Новых: {new_posts}")

    # --- Send delayed polls (5 min after announce) ---
    now_utc = datetime.now(timezone.utc)
    for vid, st in list(live_poll_state.items()):
        if st.get("poll_sent"): continue
        try:
            announced = datetime.fromisoformat(st["announced_at"])
            elapsed = (now_utc - announced).total_seconds()
        except (ValueError, TypeError):
            continue
        if elapsed >= _POLL_DELAY_SECONDS:
            poll_cfg = detect_game_for_poll(st.get("title",""))
            if poll_cfg:
                try:
                    print(f"📊 Опрос для {vid}: {poll_cfg['question']}")
                    pres = send_telegram_poll(TELEGRAM_CHAT_ID,
                                               poll_cfg["question"],
                                               poll_cfg["options"],
                                               allows_multiple=True)
                    st["poll_sent"] = True
                    st["poll_message_id"] = pres["result"]["message_id"]
                    increment_posts(stats)
                    print(f"✅ Опрос отправлен: {poll_cfg['question']}")
                    try:
                        react_to_message(TELEGRAM_CHAT_ID, pres["result"]["message_id"], "📊")
                    except Exception as e:
                        print(f"Реакция на опрос: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"❌ Ошибка опроса {vid}: {e}", file=sys.stderr)
                    increment_errors(stats)
            else:
                print(f"ℹ️ Нет конфига опроса для '{st.get('title','')}' ({vid})")
                st["poll_sent"] = True

    save_live_poll_state(live_poll_state)

    check_subscriber_milestone(stats)
    save_stats(stats)

if __name__ == "__main__":
    main()
