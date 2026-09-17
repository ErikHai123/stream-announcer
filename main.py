"""
Stream Announcer Bot + Live Polls (fixed for upcoming)
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

_RUN_LOCK_FILE = os.path.join(os.path.dirname(__file__), ".last_run")
_MIN_RUN_INTERVAL_SECONDS = 60

_POSTED_IDS_MAX_COUNT     = 1000
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

def clean_old_posted_ids(posted_ids, max_count=_POSTED_IDS_MAX_COUNT):
    if not posted_ids: return posted_ids
    # Убираем записи с некорректной/повреждённой датой
    invalid, valid_items = [], []
    for vid, date_str in posted_ids.items():
        try:
            post_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            valid_items.append((vid, post_date))
        except (ValueError, TypeError):
            invalid.append(vid)
    for vid in invalid: del posted_ids[vid]
    # Держим не больше max_count записей — лишние (самые старые) удаляем
    if len(valid_items) > max_count:
        valid_items.sort(key=lambda x: x[1])  # старые первыми
        to_remove = [vid for vid, _ in valid_items[: len(valid_items) - max_count]]
        for vid in to_remove: del posted_ids[vid]
        print(f"🧹 Очищено старых ID (лимит {max_count}): {len(to_remove)}")
    if invalid:
        print(f"🧹 Удалено ID с некорректной датой: {len(invalid)}")
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

# ---------- Игры: единая база (название, эмодзи, варианты написания, опрос) ----------
GAMES = {
    "gta5": {
        "name": "GTA 5", "emoji": "🚗",
        "aliases": ["gta online", "гта онлайн", "gta v", "gta5", "gta 5", "гташка", "гта5", "гта 5", "гта", "gta"],
        "poll_question": "🚗 Какие карты сегодня будем проходить?",
        "poll_options": ["Паркуры", "Скилл-тесты", "Ларги", "Стенки", "Спуски", "Дрифт-трассы", "Паркур-лолы"],
    },
    "gta6": {
        "name": "GTA 6", "emoji": "🚔",
        "aliases": ["gta vi", "gta6", "gta 6", "гташка 6", "гта6", "гта 6"],
        "poll_question": "🚔 Что смотрим в GTA 6 сегодня?",
        "poll_options": ["Сюжетка", "Открытый мир", "Мультиплеер", "Просто ролик", "Обзор"],
    },
    "cs": {
        "name": "CS2", "emoji": "🔫",
        "aliases": ["counter-strike 2", "counter strike 2", "counter-strike", "counter strike",
                    "cs 1.6", "кс 1.6", "cs go", "csgo", "cs2", "кс го", "ксго", "кс2", "кс 2",
                    "контр-страйк", "контрстрайк", "контра"],
        "poll_question": "🔫 Какой режим сегодня ждёте?",
        "poll_options": ["ММ (соло)", "FaceIt", "Кастомки", "Зомби-мод", "1v1 Арена", "Эскалация"],
    },
    "minecraft": {
        "name": "Minecraft", "emoji": "⛏️",
        "aliases": ["minecraft", "майнкрафте", "майнкрафтом", "майнкрафт"],
        "poll_question": "⛏️ Что строим/делаем сегодня?",
        "poll_options": ["Хардкор выживание", "Паркур карта", "ПвП арена", "Авто-ферма", "Бедварс", "Скайблок"],
    },
    "valorant": {
        "name": "Valorant", "emoji": "🔫",
        "aliases": ["valorant", "валорант"],
        "poll_question": "🔫 Какой агент/режим в Valorant?",
        "poll_options": ["Рейтинг", "Связка", "Дезматч", "Эскалация", "Кастомки"],
    },
    "dota": {
        "name": "Dota 2", "emoji": "⚔️",
        "aliases": ["dota 2", "dota2", "dota", "дота 2", "дота2", "дотка", "дотой", "дота"],
        "poll_question": "⚔️ Какая роль сегодня в Dota?",
        "poll_options": ["Керри", "Мид", "Оффлейн", "Саппорт 4", "Саппорт 5", "Все рандом"],
    },
    "lol": {
        "name": "League of Legends", "emoji": "⚔️",
        "aliases": ["league of legends", "лига легенд"],
        "poll_question": "⚔️ Какая роль сегодня в LoL?",
        "poll_options": ["Топ", "Лес", "Мид", "АДК", "Саппорт", "Все рандом"],
    },
    "fortnite": {
        "name": "Fortnite", "emoji": "🔫",
        "aliases": ["fortnite", "фортнайта", "фортнайт", "форт найт"],
        "poll_question": "🔫 Что играем в Fortnite?",
        "poll_options": ["Рояль", "Творческий", "ЗБС", "Ранкед", "Кастомки"],
    },
    "pubg": {
        "name": "PUBG", "emoji": "🔫",
        "aliases": ["pubg", "пабг", "пубг"],
        "poll_question": "🔫 Какой режим PUBG сегодня?",
        "poll_options": ["Соло", "Дуо", "Сквад", "Классик", "Ranked"],
    },
    "apex": {
        "name": "Apex Legends", "emoji": "🔫",
        "aliases": ["apex legends", "apex", "апекс легендс", "апекс"],
        "poll_question": "🔫 Какой режим Apex сегодня?",
        "poll_options": ["Баттл-ройал", "Арена", "Ранкед", "ЛТМ", "Трио"],
    },
    "warzone": {
        "name": "Call of Duty: Warzone", "emoji": "🔫",
        "aliases": ["call of duty", "warzone", "варзона", "варзон", "колда", "колду"],
        "poll_question": "🔫 Какой режим Warzone?",
        "poll_options": ["Большая карта", "Решта", "Ранкед", "Соло", "Отряд"],
    },
    "rust": {
        "name": "Rust", "emoji": "🪓",
        "aliases": ["rust", "раст"],
        "poll_question": "🪓 Что сегодня в Rust?",
        "poll_options": ["Рейд", "Фарм ресурсов", "Строительство базы", "PvP", "Вайп"],
    },
    "roblox": {
        "name": "Roblox", "emoji": "🧱",
        "aliases": ["roblox", "роблокс"],
        "poll_question": "🧱 Какая игра в Roblox сегодня?",
        "poll_options": ["Doors", "Tower of Hell", "Brookhaven", "BedWars", "Мини-игры"],
    },
    "wow": {
        "name": "World of Warcraft", "emoji": "⚔️",
        "aliases": ["world of warcraft", "ворлд оф варкрафт", "вов", "wow"],
        "poll_question": "⚔️ Что сегодня в WoW?",
        "poll_options": ["Подземелья", "Рейд", "PvP", "Прокачка", "Торговля"],
    },
    "wot": {
        "name": "World of Tanks", "emoji": "🎯",
        "aliases": ["world of tanks", "ворлд оф танкс", "танки", "wot"],
        "poll_question": "🎯 Какой режим World of Tanks?",
        "poll_options": ["Случайный бой", "Ранговый", "Клановый", "Исторический", "Песочница"],
    },
    "standoff2": {
        "name": "Standoff 2", "emoji": "🔫",
        "aliases": ["standoff 2", "standoff2", "стендофф", "стандофф"],
        "poll_question": "🔫 Какой режим Standoff 2?",
        "poll_options": ["Классика", "Дефматч", "Ранкед", "Кастомки"],
    },
    "genshin": {
        "name": "Genshin Impact", "emoji": "🌸",
        "aliases": ["genshin impact", "genshin", "геншин импакт", "геншин"],
        "poll_question": "🌸 Что делаем в Genshin сегодня?",
        "poll_options": ["Данжи", "Фарм артефактов", "Ивент", "Спиральная бездна", "Прокачка"],
    },
    "brawlstars": {
        "name": "Brawl Stars", "emoji": "💥",
        "aliases": ["brawl stars", "бравлстарс", "бравл старс"],
        "poll_question": "💥 Какой режим Brawl Stars?",
        "poll_options": ["Кубок трофеев", "Осада", "Нокаут", "Захват", "Ганк"],
    },
    "amongus": {
        "name": "Among Us", "emoji": "🎮",
        "aliases": ["among us", "амонгас", "амонг ас"],
        "poll_question": "🎮 Какая карта Among Us сегодня?",
        "poll_options": ["The Skeld", "MIRA HQ", "Polus", "Airship", "Fungle"],
    },
    "fallguys": {
        "name": "Fall Guys", "emoji": "🎮",
        "aliases": ["fall guys", "фолгайс", "фолл гайс"],
        "poll_question": "🎮 Какой раунд Fall Guys ждёте?",
        "poll_options": ["Захват короны", "Командные", "Выживание", "Гонки", "Финал"],
    },
    "eldenring": {
        "name": "Elden Ring", "emoji": "⚔️",
        "aliases": ["elden ring", "элден ринг"],
        "poll_question": "⚔️ Что сегодня в Elden Ring?",
        "poll_options": ["Боссы", "ПвП арена", "НГ+", "Кооп", "Исследование"],
    },
    "tarkov": {
        "name": "Escape from Tarkov", "emoji": "☠️",
        "aliases": ["escape from tarkov", "тарков"],
        "poll_question": "☠️ Какой рейд в Tarkov сегодня?",
        "poll_options": ["Таможня", "Резерв", "Берег", "Завод", "Рейд без страховки"],
    },
    "rocketleague": {
        "name": "Rocket League", "emoji": "⚽",
        "aliases": ["rocket league", "рокетлига", "рокет лига"],
        "poll_question": "⚽ Какой режим Rocket League?",
        "poll_options": ["1v1", "2v2", "3v3", "Хоккей", "Румбл", "Дропшот"],
    },
    "battlefield": {
        "name": "Battlefield", "emoji": "💣",
        "aliases": ["battlefield", "батлфилд", "батл филд"],
        "poll_question": "💣 Какой режим Battlefield сегодня?",
        "poll_options": ["Захват", "Прорыв", "Штурм", "Техника", "Снайпер only"],
    },
    "overwatch": {
        "name": "Overwatch", "emoji": "🔫",
        "aliases": ["overwatch", "овервотче", "овервотч"],
        "poll_question": "🔫 Какой режим Overwatch?",
        "poll_options": ["Рейтинг", "Быстрая игра", "Аркада", "Кастомки", "Пуш"],
    },
    "fifa": {
        "name": "FIFA / EA FC", "emoji": "⚽",
        "aliases": ["ea fc", "fifa", "фифа", "еа фс"],
        "poll_question": "⚽ Какой режим FIFA сегодня?",
        "poll_options": ["FUT Champions", "Карьера", "Pro Clubs", "Volta", "Драфт"],
    },
    "f1": {
        "name": "Formula 1", "emoji": "🏎️",
        "aliases": ["formula 1", "формула 1", "формула", "f1"],
        "poll_question": "🏎️ Какой формат гонки сегодня?",
        "poll_options": ["Гранд-При (50%)", "Гранд-При (100%)", "Спринт", "Квалификация", "Мультиплеер"],
    },
    "farcry": {
        "name": "Far Cry", "emoji": "🔫",
        "aliases": ["far cry", "фар край", "farcry"],
        "poll_question": "🔫 Что сегодня в Far Cry?",
        "poll_options": ["Сюжет", "Аванпосты", "Охота", "Кооп", "Экспедиции"],
    },
    "cyberpunk": {
        "name": "Cyberpunk 2077", "emoji": "🤖",
        "aliases": ["cyberpunk 2077", "cyberpunk", "киберпанк 2077", "киберпанк"],
        "poll_question": "🤖 Что сегодня в Cyberpunk?",
        "poll_options": ["Сюжетка", "Рандомные квесты", "Полиция vs Гангстеры", "Фотомод", "Боссы"],
    },
    "stalker2": {
        "name": "S.T.A.L.K.E.R. 2", "emoji": "☢️",
        "aliases": ["stalker 2", "сталкер 2", "stalker", "сталк", "сталкер"],
        "poll_question": "☢️ Какая зона сегодня в Stalker?",
        "poll_options": ["Кордон", "Бар", "Припять", "ЧАЭС", "Подземелья", "Аномалии"],
    },
    "atomicheart": {
        "name": "Atomic Heart", "emoji": "⚛️",
        "aliases": ["atomic heart", "атомик харт", "атомик"],
        "poll_question": "⚛️ Что сегодня в Atomic Heart?",
        "poll_options": ["Сюжет", "Побочки", "Боссы", "Исследование Р.У.Р."],
    },
    "residentevil": {
        "name": "Resident Evil", "emoji": "🧟",
        "aliases": ["resident evil requiem", "resident evil", "резидент ивел", "резидент эвил"],
        "poll_question": "🧟 Что сегодня в Resident Evil?",
        "poll_options": ["Сюжет", "Боссы", "Хоррор на максимум", "Спидран", "Совместное прохождение"],
    },
    "blackmyth": {
        "name": "Black Myth: Wukong", "emoji": "🐒",
        "aliases": ["black myth wukong", "black myth", "чёрный миф", "вуконг"],
        "poll_question": "🐒 Что сегодня в Black Myth: Wukong?",
        "poll_options": ["Боссы", "Сюжет", "Исследование", "Фарм снаряги"],
    },
    "marvelrivals": {
        "name": "Marvel Rivals", "emoji": "🦸",
        "aliases": ["marvel rivals", "марвел райвелс", "марвел"],
        "poll_question": "🦸 Какой режим Marvel Rivals?",
        "poll_options": ["Быстрая игра", "Ранкед", "Кастомки", "Соревновательный"],
    },
    "palworld": {
        "name": "Palworld", "emoji": "🐾",
        "aliases": ["palworld", "пэлворлд", "палворлд"],
        "poll_question": "🐾 Что делаем в Palworld?",
        "poll_options": ["Ловим палов", "Строим базу", "Босс-рейд", "PvP арена"],
    },
    "bg3": {
        "name": "Baldur's Gate 3", "emoji": "⚔️",
        "aliases": ["baldur's gate 3", "baldurs gate 3", "балдурс гейт", "бг3"],
        "poll_question": "⚔️ Что сегодня в Baldur's Gate 3?",
        "poll_options": ["Сюжет", "Компания", "Исследование", "Боссы", "Романтическая линия"],
    },
    "helldivers": {
        "name": "Helldivers 2", "emoji": "🪖",
        "aliases": ["helldivers 2", "helldivers", "хелдайверс"],
        "poll_question": "🪖 Какая миссия в Helldivers 2?",
        "poll_options": ["Истребление", "Эвакуация", "Высокая сложность", "Кооп на 4"],
    },
    "crimsondesert": {
        "name": "Crimson Desert", "emoji": "🗡️",
        "aliases": ["crimson desert", "кримсон дезерт"],
        "poll_question": "🗡️ Что сегодня в Crimson Desert?",
        "poll_options": ["Сюжет", "Открытый мир", "Боссы", "Исследование"],
    },
    "subnautica": {
        "name": "Subnautica", "emoji": "🌊",
        "aliases": ["subnautica 2", "subnautica", "субнотика", "субнавтика"],
        "poll_question": "🌊 Что делаем в Subnautica?",
        "poll_options": ["Исследование глубин", "Строительство базы", "Крафт", "Выживание", "Встреча с боссом"],
    },
    "warface": {
        "name": "Warface", "emoji": "🔫",
        "aliases": ["warface", "варфейс"],
        "poll_question": "🔫 Какой режим Warface?",
        "poll_options": ["PvE", "PvP", "Рейд", "Штурм", "Кастомки"],
    },
    "mortalkombat": {
        "name": "Mortal Kombat", "emoji": "🥊",
        "aliases": ["mortal kombat", "мортал комбат"],
        "poll_question": "🥊 Что сегодня в Mortal Kombat?",
        "poll_options": ["Сюжет", "Онлайн бои", "Башни", "Фаталити-сессия"],
    },
    "ittakestwo": {
        "name": "It Takes Two", "emoji": "🤝",
        "aliases": ["it takes two", "ит тейкс ту"],
        "poll_question": "🤝 Как проходим It Takes Two?",
        "poll_options": ["По сюжету", "На 100%", "Соревновательные мини-игры"],
    },
    "lethalcompany": {
        "name": "Lethal Company", "emoji": "👻",
        "aliases": ["lethal company", "летальная компания", "летал компани"],
        "poll_question": "👻 Какая планета в Lethal Company сегодня?",
        "poll_options": ["Лёгкая", "Средняя", "Опасная", "Экстрим", "На выживание"],
    },
    "contentwarning": {
        "name": "Content Warning", "emoji": "📹",
        "aliases": ["content warning", "контент ворнинг"],
        "poll_question": "📹 Что снимаем в Content Warning?",
        "poll_options": ["Хоррор-контент", "Смешные моменты", "Опасные вылазки"],
    },
    "phasmophobia": {
        "name": "Phasmophobia", "emoji": "👻",
        "aliases": ["phasmophobia", "фазмофобия"],
        "poll_question": "👻 Какая карта в Phasmophobia?",
        "poll_options": ["Дом", "Психушка", "Школа", "Высокая сложность", "Профессионал"],
    },
    "splitfiction": {
        "name": "Split Fiction", "emoji": "📖",
        "aliases": ["split fiction", "сплит фикшн"],
        "poll_question": "📖 Как проходим Split Fiction?",
        "poll_options": ["По сюжету", "Кооп-испытания", "На 100%"],
    },
    "peak": {
        "name": "PEAK", "emoji": "🏔️",
        "aliases": ["peak"],
        "poll_question": "🏔️ Как штурмуем PEAK сегодня?",
        "poll_options": ["Кооп восхождение", "Соло забег", "Спидран", "Хардкор без потерь"],
    },
    "repo": {
        "name": "R.E.P.O.", "emoji": "🤖",
        "aliases": ["r.e.p.o.", "репо", "repo"],
        "poll_question": "🤖 Какой уровень сложности в R.E.P.O.?",
        "poll_options": ["Лёгкий", "Средний", "Сложный", "Кошмар", "Соло-челлендж"],
    },
}

# Плоский список (алиас, ключ_игры), отсортированный от длинных алиасов к коротким —
# так "гта 6" распознаётся раньше короткого "гта", и более специфичные варианты
# (например "cs 1.6") не перебиваются общими ("cs").
_GAME_ALIASES_SORTED = sorted(
    ((alias, key) for key, g in GAMES.items() for alias in g["aliases"]),
    key=lambda pair: len(pair[0]),
    reverse=True,
)

DEFAULT_THEME_EMOJI = "🔴"

def match_game(title):
    lowered = title.lower()
    for alias, key in _GAME_ALIASES_SORTED:
        if alias in lowered:
            return GAMES[key]
    return None

def detect_theme_emoji(title):
    game = match_game(title)
    return game["emoji"] if game else DEFAULT_THEME_EMOJI

def detect_game_name(title):
    game = match_game(title)
    return game["name"] if game else None

def detect_game_for_poll(title):
    game = match_game(title)
    if game:
        return {"question": game["poll_question"], "options": game["poll_options"]}
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
    "{emoji} Архив ожил! «{title}» от {channel}\nЗабытая жемчужина, го пересматривать",
    "{emoji} А вы помните это? «{title}»\n{channel} — идеально зайдёт под чай ☕",
    "{emoji} Раскопали в архивах: «{title}»\nОт {channel} — заслуживает второго просмотра",
    "{emoji} Пятничный (ну или любой) флешбек: «{title}»\n{channel} — заходи вспомнить",
    "{emoji} Один из тех роликов, что стоит пересмотреть: «{title}»\nОт {channel} 👇",
    "{emoji} Стоп, а ты это видел? «{title}»\n{channel} ждёт на просмотре",
    "{emoji} Возвращаем в ленту: «{title}»\nОт {channel} — незаслуженно забытое видео",
    "{emoji} Ролик дня из закромов: «{title}»\n{channel} — залетай, не пожалеешь!",
]

RANDOM_TEMPLATES_GAME = [
    "{emoji} Вспоминаем {game}! «{title}» от {channel}\nЕсли пропустил — самое время наверстать 👇",
    "{emoji} Го пересмотрим {game}: «{title}»\n{channel} — залетай, это того стоит!",
    "{emoji} Ламповый {game}-ролик на пересмотр: «{title}»\nОт {channel} — заходи!",
    "{emoji} {game} не стареет: «{title}»\n{channel} ждёт тебя на просмотре",
    "{emoji} Ретро {game} из архивов: «{title}»\nОт {channel} — самое время пересмотреть!",
    "{emoji} Для фанатов {game}: «{title}»\n{channel} — залетай вспомнить хорошие моменты",
    "{emoji} {game}-флешбек дня: «{title}»\nОт {channel} — не проходи мимо",
]

RANDOM_BUTTON_LABELS = [
    "▶️ Смотреть на YouTube",
    "🎬 Пересмотреть",
    "👀 Глянуть видео",
    "📺 Открыть видео",
    "🍿 Смотреть",
]

RANDOM_REACTIONS = ["❤️", "🔥", "🎉", "🤩", "👍", "😁"]

def generate_announcement_text(content_type, title, channel_title, start_time_str=""):
    tm = {"live":LIVE_TEMPLATES,"upcoming":UPCOMING_TEMPLATES,
          "video":VIDEO_TEMPLATES,"shorts":SHORTS_TEMPLATES,"random":RANDOM_TEMPLATES}
    pool = tm[content_type]
    game_name = None
    if content_type == "random":
        game_name = detect_game_name(title)
        if game_name:
            pool = RANDOM_TEMPLATES + RANDOM_TEMPLATES_GAME
    tpl = random.choice(pool)
    return tpl.format(channel=channel_title, title=title, when=start_time_str,
                       emoji=detect_theme_emoji(title), game=game_name or "")

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
        "is_anonymous": "true",
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

QUIET_HOURS_START = int(os.environ.get("QUIET_HOURS_START", "0"))  # 00:00
QUIET_HOURS_END = int(os.environ.get("QUIET_HOURS_END", "6"))      # 06:00

def in_quiet_hours(now_local):
    """Тихие часы (например 0–6 МСК). Поддерживает и интервалы через полночь."""
    if QUIET_HOURS_START == QUIET_HOURS_END:
        return False  # отключено
    h = now_local.hour
    if QUIET_HOURS_START < QUIET_HOURS_END:
        return QUIET_HOURS_START <= h < QUIET_HOURS_END
    return h >= QUIET_HOURS_START or h < QUIET_HOURS_END

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

    if in_quiet_hours(now) and not FORCE_VIDEO_URL and not CATCH_UP_ONLY:
        print(f"🌙 Тихие часы ({QUIET_HOURS_START:02d}:00–{QUIET_HOURS_END:02d}:00 МСК), пропускаем публикацию.")
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
                    button_label = random.choice(RANDOM_BUTTON_LABELS)
                    res = send_telegram_photo(thumb, text, [{"text":button_label,"url":link}])
                    print(f"✅ Рандом опубликован: {title}")
                    increment_posts(stats)
                    try:
                        react_to_message(TELEGRAM_CHAT_ID, res["result"]["message_id"], random.choice(RANDOM_REACTIONS))
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
        scheduled_start_raw = None

        if live:
            is_live = "actualStartTime" in live and "actualEndTime" not in live
            is_upcoming = "scheduledStartTime" in live and "actualStartTime" not in live
            if not is_live and not is_upcoming:
                print(f"⏭️ Пропуск завершённого: {title}"); continue
            ctype = "live" if is_live else "upcoming"
            ss = live.get("scheduledStartTime")
            scheduled_start_raw = ss
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

            # Опрос отправляем сразу вместе с анонсом (не дожидаясь начала стрима)
            if ctype in ("live", "upcoming"):
                poll_cfg = detect_game_for_poll(title)
                if poll_cfg:
                    try:
                        print(f"📊 Опрос для {video_id}: {poll_cfg['question']}")
                        pres = send_telegram_poll(TELEGRAM_CHAT_ID,
                                                   poll_cfg["question"],
                                                   poll_cfg["options"],
                                                   allows_multiple=True)
                        increment_posts(stats)
                        print(f"✅ Опрос отправлен: {poll_cfg['question']}")
                        try:
                            react_to_message(TELEGRAM_CHAT_ID, pres["result"]["message_id"], "🤩")
                        except Exception as e:
                            print(f"Реакция на опрос: {e}", file=sys.stderr)
                    except Exception as e:
                        print(f"❌ Ошибка опроса {video_id}: {e}", file=sys.stderr)
                        increment_errors(stats)
                else:
                    print(f"ℹ️ Нет конфига опроса для '{title}' ({video_id})")
        except Exception as e:
            print(f"❌ Ошибка Telegram {video_id}: {e}", file=sys.stderr)
            increment_errors(stats); continue

        posted_ids[video_id] = tsm
        new_posts += 1
        save_posted_ids(posted_ids)
        time.sleep(SECONDS_BETWEEN_POSTS)

    save_posted_ids(posted_ids)
    print(f"Готово. Новых: {new_posts}")

    check_subscriber_milestone(stats)
    save_stats(stats)

if __name__ == "__main__":
    main()
