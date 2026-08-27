import asyncio
import logging
import time
from datetime import datetime, timezone

import aiohttp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import config
import rcon_query
import steam_api

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
#  Вспомогательные функции
# ────────────────────────────────────────────

PERSONA_STATES = {
    0: "Оффлайн",
    1: "Онлайн",
    2: "Не беспокоить",
    3: "Отошёл",
    4: "Спит",
    5: "Ищет группу",
    6: "Играет",
}


def format_playtime(minutes: int | None) -> str:
    """Конвертация минут в читаемый формат: только часы и минуты."""
    if minutes is None:
        return "Н/Д"
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0:
        if mins > 0:
            return f"{hours} ч {mins} мин"
        return f"{hours} ч"
    return f"{mins} мин"


def format_duration(seconds: float) -> str:
    """Форматирование длительности сессии в читаемый вид."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f"{h}ч {m}м"
    return f"{m}м"


def time_ago(timestamp_str: str) -> str:
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return "только что"
        if secs < 3600:
            return f"{secs // 60} мин назад"
        if secs < 86400:
            return f"{secs // 3600} ч назад"
        return f"{secs // 86400} дн назад"
    except Exception:
        return "н/д"


# Кэш найденных веб-карт: "ip:port" -> (timestamp, url|None), живёт 10 минут
_MAP_URL_CACHE: dict = {}


def _pick_steam_server(servers: list[dict], address: str) -> dict | None:
    """Выбрать сервер из реестра Steam: только Rust, приоритет точному порту."""
    if not servers:
        return None
    _, _, port_str = address.rpartition(":")
    port = int(port_str) if port_str.isdigit() else None

    rust = [sv for sv in servers
            if sv.get("appid") == config.RUST_APP_ID
            or str(sv.get("gamedir", "")).lower() == "rust"]
    if not rust:
        rust = servers

    if port is not None:
        for sv in rust:
            if str(sv.get("addr", "")).endswith(f":{port}"):
                return sv
    return rust[0]


# Кэш списка игроков по адресу: "ip:port" -> (timestamp, список), живёт 60 сек.
# Нужен, чтобы кнопки ◀ ▶ листали без повторного запроса к серверу.
_PLAYERS_CACHE: dict = {}
_PLAYERS_CACHE_TTL = 60

PAGE_SIZE = 5


async def _fetch_players_cached(address: str) -> list | None:
    """Список игроков с кэшем. None — сервер не ответил ни по одному каналу."""
    cached = _PLAYERS_CACHE.get(address)
    if cached and time.time() - cached[0] < _PLAYERS_CACHE_TTL:
        return cached[1]
    loop = asyncio.get_event_loop()
    players = await loop.run_in_executor(None, __import__("a2s_query").query_players, address)
    if players is None and config.RCON_PASSWORD and config.RCON_SERVER:
        # UDP не дошёл — пробуем RCON (TCP) для своего сервера
        players = await loop.run_in_executor(None, rcon_query.query_players_rcon, address)
    if players is not None:
        _PLAYERS_CACHE[address] = (time.time(), players)
    return players


def _render_players_page(players: list, page: int, address: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Страница списка игроков (PAGE_SIZE штук) + кнопки ◀ ▶."""
    total = len(players)
    if total == 0:
        return f"👥 На сервере <code>{address}</code> никого нет.", None

    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    start = page * PAGE_SIZE

    lines = [f"👥 <b>Игроки на сервере</b> ({total}):\n"]
    for i, p in enumerate(players[start:start + PAGE_SIZE], start + 1):
        dur = format_duration(p.get("duration") or 0)
        name = p.get("name") or "???"
        lines.append(f"{i}. {name} — {dur}")

    lines.append(f"\nСтр. {page + 1}/{pages}")

    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀", callback_data=f"players|{address}|{page - 1}"))
    if page < pages - 1:
        row.append(InlineKeyboardButton("▶", callback_data=f"players|{address}|{page + 1}"))
    markup = InlineKeyboardMarkup([row]) if row else None

    return "\n".join(lines), markup


async def find_server_map_url(ip: str, port: int) -> str | None:
    """Поиск ссылки на живую карту Rust-сервера (Leaf webmap и популярные порты).
    Проверяет все кандидаты параллельно, результат кэшируется на 10 минут."""
    key = f"{ip}:{port}"
    cached = _MAP_URL_CACHE.get(key)
    if cached and time.time() - cached[0] < 600:
        return cached[1]

    candidates = []
    for p in (8081, 8080, 8082, 28016):  # Leaf webmap и популярные порты
        candidates.append(f"http://{ip}:{p}/")
        candidates.append(f"https://{ip}:{p}/")
    candidates.append(f"http://{ip}:{port}/")        # игровой порт (иногда webmap)
    candidates.append(f"http://{ip}:{port + 1}/")    # на один больше игрового

    async def _check(url: str) -> str | None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                async with session.get(url, ssl=False, allow_redirects=True) as resp:
                    if resp.status == 200:
                        return url
        except Exception:
            pass
        return None

    results = await asyncio.gather(*[_check(u) for u in candidates])
    found = next((u for u in results if u), None)
    _MAP_URL_CACHE[key] = (time.time(), found)
    return found


# ────────────────────────────────────────────
#  Команда /start
# ────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎮 <b>Rust Player Bot</b>\n\n"
        "Проверяй статистику игроков и серверы Rust в Telegram!\n\n"
        "📋 <b>Команды:</b>\n"
        "/profile <code>SteamID</code> — профиль игрока\n"
        "/server <code>ip:port</code> — информация о сервере\n"
        "/players <code>ip:port</code> — кто онлайн на сервере\n"
        "/help — помощь\n\n"
        "💡 <b>Примеры:</b>\n"
        "<code>/profile 76561198012345678</code>\n"
        "<code>/profile nickname</code>\n"
        "<code>/server 185.25.217.34:28015</code>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ────────────────────────────────────────────
#  Команда /help
# ────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ <b>Как пользоваться</b>\n\n"
        "🔹 <b>Профиль игрока:</b>\n"
        "<code>/profile nickname</code> — найти по нику\n"
        "<code>/profile 76561198012345678</code> — по SteamID\n"
        "<code>/profile steamcommunity.com/id/nickname</code> — по ссылке\n\n"
        "Покажет: аватар, имя, онлайн/оффлайн, часы в Rust, баны, страну.\n\n"
        "🔹 <b>Инфо о сервере:</b>\n"
        "<code>/server 185.25.217.34:28015</code>\n"
        "Покажет: название, карта, кол-во игроков.\n\n"
        "🔹 <b>Игроки на сервере:</b>\n"
        "<code>/players 185.25.217.34:28015</code>\n"
        "Покажет список текущих игроков.\n\n"
        "💡 IP и порт сервера можно найти на\n"
        "battlemetrics.com/servers/rust"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ────────────────────────────────────────────
#  Команда /profile
# ────────────────────────────────────────────

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Укажи SteamID или ник.\n\n"
            "Примеры:\n"
            "<code>/profile 76561198012345678</code>\n"
            "<code>/profile nickname</code>",
            parse_mode="HTML",
        )
        return

    user_input = " ".join(context.args)
    await update.message.chat.send_action("typing")

    # 1) Пробуем резолвить как SteamID / кастомную ссылку
    steam_id = await steam_api.resolve_input_to_steam_id(user_input)

    # 2) Если не получилось — ищем по нику, открываем лучшего игрока сразу
    if not steam_id:
        players = await steam_api.search_players(user_input, limit=5)
        if players:
            want = user_input.lower()
            exact = next((p for p in players if p["name"].lower() == want), None)
            steam_id = (exact or players[0])["steam_id"]
            logger.info("Поиск по нику «%s»: найден %s", user_input, steam_id)
        else:
            await update.message.reply_text(
                f"❌ Не удалось найти игрока «{user_input}».\n"
                "Проверь правильность ввода или пришли ссылку на профиль:\n"
                "<code>/profile steamcommunity.com/id/твой_ник</code>",
            )
            return

    msg, reply_markup = await _build_profile_message(steam_id, user_input)
    if msg is None:
        await update.message.reply_text(
            "❌ Не удалось получить данные из Steam.\n"
            "Возможно, SteamID неверный или профиль приватный.",
        )
        return

    await update.message.reply_text(
        msg, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True
    )


async def _build_profile_message(steam_id: str, user_input: str) -> tuple[str | None, InlineKeyboardMarkup | None]:
    """Загрузить данные и собрать сообщение профиля + клавиатуру."""
    summary_task = steam_api.get_player_summary(steam_id)
    games_task = steam_api.get_owned_games(steam_id)
    bans_task = steam_api.get_player_bans(steam_id)

    summary, games, bans = await asyncio.gather(
        summary_task, games_task, bans_task,
        return_exceptions=True,
    )

    if isinstance(summary, Exception) or summary is None:
        return None, None

    name = summary.get("personaname", "Неизвестно")
    profile_url = summary.get("profileurl", "")
    persona_state = summary.get("personastate", 0)
    state_text = PERSONA_STATES.get(persona_state, "Неизвестно")
    last_logoff = summary.get("lastlogoff")
    country = summary.get("loccountrycode", "")
    visibility = summary.get("communityvisibilitystate", 1)
    is_private = visibility in (1, 2)

    # Часы в Rust
    rust_hours = "🔒 Профиль приватный — данные скрыты"
    if not is_private and isinstance(games, list):
        for game in games:
            if game.get("appid") == config.RUST_APP_ID:
                rust_hours = format_playtime(game.get("playtime_forever"))
                break
        else:
            rust_hours = "Игра не найдена в библиотеке"

    # Текущая игра и Rust-статус
    game_text = ""
    game_id = summary.get("gameid", 0)
    game_extra = summary.get("gameextrainfo", "")
    game_name = summary.get("gamename", "")

    # Определяем Rust: gameid ИЛИ gameextrainfo содержит "Rust"
    is_rust = game_id == config.RUST_APP_ID or "rust" in game_extra.lower()

    if game_id:
        if is_rust:
            game_text = "\n🟢 <b>Сейчас играет в Rust</b>"
        elif game_extra:
            game_text = f"\n🎮 Сейчас в другой игре: <b>{game_extra}</b>"
        elif game_name:
            game_text = f"\n🎮 Сейчас в другой игре: <b>{game_name}</b>"
        else:
            game_text = "\n🎮 Сейчас играет"

    # Последний вход
    last_seen = ""
    if last_logoff:
        dt = datetime.fromtimestamp(last_logoff, tz=timezone.utc)
        ago = time_ago(dt.isoformat())
        if persona_state > 0:
            last_seen = f"\n🕐 Последний раз выходил из Steam: {ago}"
        else:
            last_seen = f"\n🕐 Последняя активность в Steam: {ago}"

    # Баны
    ban_text = ""
    if isinstance(bans, dict):
        vac = bans.get("VACBanned", False)
        num_bans = bans.get("NumberOfGameBans", 0)
        if vac or num_bans > 0:
            ban_parts = []
            if vac:
                ban_parts.append("⚠️ VAC-бан")
            if num_bans > 0:
                ban_parts.append(f"⚠️ Игровых банов: {num_bans}")
            ban_text = "\n" + "\n".join(ban_parts)

    country_emoji = {
        "US": "🇺🇸", "DE": "🇩🇪", "FR": "🇫🇷", "GB": "🇬🇧",
        "RU": "🇷🇺", "NL": "🇳🇱", "AU": "🇦🇺", "SE": "🇸🇪",
        "FI": "🇫🇮", "NO": "🇳🇴", "PL": "🇵🇱", "BR": "🇧🇷",
        "UA": "🇺🇦", "KZ": "🇰🇿", "TR": "🇹🇷", "IL": "🇮🇱",
    }.get(country, "")

    country_name = {
        "US": "США", "DE": "Германия", "FR": "Франция", "GB": "Великобритания",
        "RU": "Россия", "NL": "Нидерланды", "AU": "Австралия", "SE": "Швеция",
        "FI": "Финляндия", "NO": "Норвегия", "PL": "Польша", "BR": "Бразилия",
        "UA": "Украина", "KZ": "Казахстан", "TR": "Турция", "IL": "Израиль",
        "BY": "Беларусь", "CA": "Канада", "CZ": "Чехия", "ES": "Испания",
        "IT": "Италия", "JP": "Япония", "KR": "Южная Корея", "MX": "Мексика",
        "CN": "Китай", "IN": "Индия", "AR": "Аргентина", "CH": "Швейцария",
        "AT": "Австрия", "BE": "Бельгия", "BG": "Болгария", "HR": "Хорватия",
        "DK": "Дания", "EE": "Эстония", "GR": "Греция", "HU": "Венгрия",
        "IE": "Ирландия", "LV": "Латвия", "LT": "Литва", "LU": "Люксембург",
        "MD": "Молдова", "PT": "Португалия", "RO": "Румыния", "RS": "Сербия",
        "SK": "Словакия", "SI": "Словения", "ZA": "ЮАР", "NG": "Нигерия",
        "EG": "Египет", "AE": "ОАЭ", "SA": "Саудовская Аравия", "IR": "Иран",
        "PK": "Пакистан", "BD": "Бангладеш", "TH": "Таиланд", "VN": "Вьетнам",
        "PH": "Филиппины", "ID": "Индонезия", "MY": "Малайзия", "SG": "Сингапур",
        "NZ": "Новая Зеландия", "CL": "Чили", "CO": "Колумбия", "PE": "Перу",
    }.get(country, "")

    msg = (
        f"{'🟢' if persona_state > 0 else '🔴'} <b>{name}</b>\n\n"
        f"🆔 <code>{steam_id}</code>\n"
        f"{'🌍 ' + country_emoji + ' ' + country_name if country_name else ''}\n"
        f"📊 Статус: <b>{state_text}</b>"
        f"{game_text}"
        f"{last_seen}\n\n"
        f"⏱ <b>Общее время в Rust:</b> {rust_hours}"
        f"{ban_text}\n\n"
        f"🔗 <a href=\"{profile_url}\">Открыть профиль в Steam</a>"
    )

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{steam_id}:{user_input}"),
         InlineKeyboardButton("📊 Статистика", callback_data=f"stats:{steam_id}:{user_input}"),
         InlineKeyboardButton("🎮 Игры", callback_data=f"servers:{steam_id}:{user_input}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    return msg, reply_markup


# ────────────────────────────────────────────
#  Callback: Обновить профиль
# ────────────────────────────────────────────

async def callback_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Обновляю...")

    data = query.data.split(":", 2)
    steam_id = data[1]
    user_input = data[2] if len(data) > 2 else steam_id

    summary = await steam_api.get_player_summary(steam_id)
    if not summary:
        await query.edit_message_text("❌ Не удалось обновить данные.")
        return

    persona_state = summary.get("personastate", 0)
    state_text = PERSONA_STATES.get(persona_state, "Неизвестно")
    name = summary.get("personaname", "Неизвестно")
    profile_url = summary.get("profileurl", "")
    last_logoff = summary.get("lastlogoff")
    country = summary.get("loccountrycode", "")

    country_emoji = {
        "US": "🇺🇸", "DE": "🇩🇪", "FR": "🇫🇷", "GB": "🇬🇧",
        "RU": "🇷🇺", "NL": "🇳🇱", "AU": "🇦🇺", "SE": "🇸🇪",
        "FI": "🇫🇮", "NO": "🇳🇴", "PL": "🇵🇱", "BR": "🇧🇷",
        "UA": "🇺🇦", "KZ": "🇰🇿", "TR": "🇹🇷", "IL": "🇮🇱",
    }.get(country, "")

    country_name = {
        "US": "США", "DE": "Германия", "FR": "Франция", "GB": "Великобритания",
        "RU": "Россия", "NL": "Нидерланды", "AU": "Австралия", "SE": "Швеция",
        "FI": "Финляндия", "NO": "Норвегия", "PL": "Польша", "BR": "Бразилия",
        "UA": "Украина", "KZ": "Казахстан", "TR": "Турция", "IL": "Израиль",
        "BY": "Беларусь", "CA": "Канада", "CZ": "Чехия", "ES": "Испания",
        "IT": "Италия", "JP": "Япония", "KR": "Южная Корея", "MX": "Мексика",
        "CN": "Китай", "IN": "Индия", "AR": "Аргентина", "CH": "Швейцария",
        "AT": "Австрия", "BE": "Бельгия", "BG": "Болгария", "HR": "Хорватия",
        "DK": "Дания", "EE": "Эстония", "GR": "Греция", "HU": "Венгрия",
        "IE": "Ирландия", "LV": "Латвия", "LT": "Литва", "LU": "Люксембург",
        "MD": "Молдова", "PT": "Португалия", "RO": "Румыния", "RS": "Сербия",
        "SK": "Словакия", "SI": "Словения", "ZA": "ЮАР", "NG": "Нигерия",
        "EG": "Египет", "AE": "ОАЭ", "SA": "Саудовская Аравия", "IR": "Иран",
        "PK": "Пакистан", "BD": "Бангладеш", "TH": "Таиланд", "VN": "Вьетнам",
        "PH": "Филиппины", "ID": "Индонезия", "MY": "Малайзия", "SG": "Сингапур",
        "NZ": "Новая Зеландия", "CL": "Чили", "CO": "Колумбия", "PE": "Перу",
    }.get(country, "")

    last_seen = ""
    if last_logoff:
        dt = datetime.fromtimestamp(last_logoff, tz=timezone.utc)
        ago = time_ago(dt.isoformat())
        if persona_state > 0:
            last_seen = f"\n🕐 Последний раз выходил из Steam: {ago}"
        else:
            last_seen = f"\n🕐 Последняя активность в Steam: {ago}"

    games = await steam_api.get_owned_games(steam_id)
    rust_hours = "🔒 Профиль приватный — данные скрыты"
    visibility = summary.get("communityvisibilitystate", 1)
    is_private = visibility in (1, 2)
    if not is_private and isinstance(games, list):
        for game in games:
            if game.get("appid") == config.RUST_APP_ID:
                rust_hours = format_playtime(game.get("playtime_forever"))
                break
        else:
            rust_hours = "Игра не найдена в библиотеке"

    bans = await steam_api.get_player_bans(steam_id)
    ban_text = ""
    if isinstance(bans, dict):
        if bans.get("VACBanned") or bans.get("NumberOfGameBans", 0) > 0:
            ban_parts = []
            if bans.get("VACBanned"):
                ban_parts.append("⚠️ VAC-бан")
            if bans.get("NumberOfGameBans", 0) > 0:
                ban_parts.append(f"⚠️ Игровых банов: {bans['NumberOfGameBans']}")
            ban_text = "\n" + "\n".join(ban_parts)

    game_text = ""
    in_rust = False
    game_id = summary.get("gameid", 0)
    game_extra = summary.get("gameextrainfo", "")
    is_rust = game_id == config.RUST_APP_ID or "rust" in game_extra.lower()
    if game_id:
        if is_rust:
            in_rust = True
            game_text = "\n🟢 <b>Сейчас играет в Rust</b>"
        elif game_extra:
            game_text = f"\n🎮 Сейчас в другой игре: <b>{game_extra}</b>"
        else:
            game_text = "\n🎮 Сейчас играет"

    msg = (
        f"{'🟢' if persona_state > 0 else '🔴'} <b>{name}</b>\n\n"
        f"🆔 <code>{steam_id}</code>\n"
        f"{'🌍 ' + country_emoji + ' ' + country_name if country_name else ''}\n"
        f"📊 Статус: <b>{state_text}</b>"
        f"{game_text}"
        f"{last_seen}\n\n"
        f"⏱ <b>Общее время в Rust:</b> {rust_hours}"
        f"{ban_text}\n\n"
        f"🔗 <a href=\"{profile_url}\">Открыть профиль в Steam</a>"
    )

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{steam_id}:{user_input}"),
         InlineKeyboardButton("📊 Статистика", callback_data=f"stats:{steam_id}:{user_input}"),
         InlineKeyboardButton("🎮 Игры", callback_data=f"servers:{steam_id}:{user_input}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)


# ────────────────────────────────────────────
#  Callback: Статистика игрока
# ────────────────────────────────────────────

async def callback_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Загружаю статистику...")

    data = query.data.split(":", 2)
    steam_id = data[1]
    user_input = data[2] if len(data) > 2 else steam_id

    # Параллельно запрашиваем всё
    summary_task = steam_api.get_player_summary(steam_id)
    games_task = steam_api.get_owned_games(steam_id)
    bans_task = steam_api.get_player_bans(steam_id)
    level_task = steam_api.get_steam_level(steam_id)
    friends_task = steam_api.get_friend_count(steam_id)
    recent_task = steam_api.get_recent_games(steam_id)

    summary, games, bans, level, friends, recent = await asyncio.gather(
        summary_task, games_task, bans_task, level_task, friends_task, recent_task,
        return_exceptions=True,
    )

    if isinstance(summary, Exception) or summary is None:
        await query.edit_message_text("❌ Не удалось загрузить статистику.")
        return

    name = summary.get("personaname", "Неизвестно")
    profile_url = summary.get("profileurl", "")
    visibility = summary.get("communityvisibilitystate", 1)
    is_private = visibility in (1, 2)
    created = summary.get("timecreated")

    # ─── Уровень Steam ───
    steam_level = "🔒 скрыт"
    if not isinstance(level, Exception) and level:
        steam_level = str(level.get("player_level", "?"))

    # ─── Количество друзей ───
    friend_count = "🔒 скрыт"
    if friends is not None and not isinstance(friends, Exception):
        friend_count = str(friends)

    # ─── Игры и общее время ───
    total_games = 0
    total_hours = 0
    rust_hours = "🔒 скрыт"
    top_games = []

    if not is_private and isinstance(games, list):
        total_games = len(games)
        for g in games:
            mins = g.get("playtime_forever", 0)
            total_hours += mins
            if g.get("appid") == config.RUST_APP_ID:
                rust_hours = format_playtime(mins)

        # Топ-5 по времени
        sorted_games = sorted(games, key=lambda x: x.get("playtime_forever", 0), reverse=True)
        for g in sorted_games[:5]:
            mins = g.get("playtime_forever", 0)
            if mins > 0:
                top_games.append(f"  • {g.get('name', '?')} — {format_playtime(mins)}")

    total_hours_str = format_playtime(total_hours) if total_hours > 0 else "н/д"

    # ─── Последние 2 недели ───
    recent_text = ""
    if isinstance(recent, list) and recent:
        recent_lines = []
        for g in recent[:5]:
            mins = g.get("playtime_forever", 0)
            recent_2weeks = g.get("playtime_2weeks", 0)
            if recent_2weeks > 0:
                recent_lines.append(f"  • {g.get('name', '?')} — {format_playtime(recent_2weeks)}")
        if recent_lines:
            recent_text = "\n\n📅 <b>За последние 2 недели:</b>\n" + "\n".join(recent_lines)

    # ─── Баны ───
    ban_summary = ""
    if isinstance(bans, dict):
        vac = bans.get("VACBanned", False)
        game_bans = bans.get("NumberOfGameBans", 0)
        days_since = bans.get("DaysSinceLastBan", 0)
        if vac or game_bans > 0:
            ban_parts = []
            if vac:
                ban_parts.append("VAC-бан")
            if game_bans > 0:
                ban_parts.append(f"{game_bans} игр. бан(ов)")
            ban_summary = f"⚠️ {' + '.join(ban_parts)}"
            if days_since > 0:
                ban_summary += f" ({days_since} дн. назад)"
        else:
            ban_summary = "✅ Чисто"

    # ─── Дата регистрации ───
    account_age = ""
    if created and not is_private:
        try:
            dt = datetime.fromtimestamp(created, tz=timezone.utc)
            account_age = f"\n🎂 Аккаунт создан: {dt.strftime('%d.%m.%Y')}"
        except Exception:
            pass

    # ─── Топ-игры ───
    top_text = ""
    if top_games:
        top_text = "\n🏆 <b>Топ-5 игр:</b>\n" + "\n".join(top_games)

    # ─── Блок банов (подробно) ───
    ban_detail = ""
    if isinstance(bans, dict):
        comm_ban = bans.get("CommunityBanned", False)
        trade_ban = bans.get("EconomyBanned", False)
        if comm_ban or trade_ban:
            extras = []
            if comm_ban:
                extras.append("社区 бан")
            if trade_ban:
                extras.append("Торговый бан")
            ban_detail = f"\n🚫 Дополнительно: {', '.join(extras)}"

    # ─── Собираем сообщение ───
    msg = (
        f"📊 <b>Статистика: {name}</b>\n"
        f"{'═' * 22}\n\n"
        f"🎮 <b>Steam</b>\n"
        f"  ⭐ Уровень: {steam_level}\n"
        f"  👥 Друзья: {friend_count}\n"
        f"  📚 Игр в библиотеке: {total_games}"
        f"{account_age}\n\n"
        f"⏱ <b>Время в играх</b>\n"
        f"  🕐 Общее время: {total_hours_str}\n"
        f"  🟠 Время в Rust: {rust_hours}"
        f"{top_text}"
        f"{recent_text}\n\n"
        f"🛡 <b>Безопасность</b>\n"
        f"  {ban_summary}"
        f"{ban_detail}\n\n"
        f"🔗 <a href=\"{profile_url}\">Открыть профиль в Steam</a>"
    )

    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data=f"refresh:{steam_id}:{user_input}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)


# ────────────────────────────────────────────
#  Callback: Серверы игрока
# ────────────────────────────────────────────

async def callback_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Загружаю данные о серверах...")

    data = query.data.split(":", 2)
    steam_id = data[1]
    user_input = data[2] if len(data) > 2 else steam_id

    # Получаем данные игрока
    summary = await steam_api.get_player_summary(steam_id)
    if not summary:
        await query.edit_message_text("❌ Не удалось загрузить данные.")
        return

    name = summary.get("personaname", "Неизвестно")
    persona_state = summary.get("personastate", 0)
    game_id = summary.get("gameid", 0)
    game_server_ip = summary.get("gameserverip", "")
    game_extra = summary.get("gameextrainfo", "")

    # Недавние игры
    recent = await steam_api.get_recent_games(steam_id)

    # ─── Текущий сервер ───
    current_text = ""
    if game_id and game_server_ip:
        import a2s_query
        loop = asyncio.get_event_loop()

        real_ip = game_server_ip.split(":")[0]
        steam_port = int(game_server_ip.split(":")[1]) if ":" in game_server_ip else 28015

        # 1) Сразу ищем реальные серверы через мастер-сервер Steam (быстро, HTTP ~0.3 сек)
        registered = await steam_api.get_servers_at_address(real_ip)
        rust_servers = [sv for sv in registered if sv.get("gamedir", "").lower() in ("rust", "252490") or sv.get("game", "").lower() == "rust"]
        if not rust_servers:
            rust_servers = registered  # любой gamedir, вдруг не заполнен

        # Сортируем по близости порта к релейному порту Steam
        # (20000 → реальный 20010, а не 10010)
        def _port_diff(sv):
            try:
                return abs(int(sv.get("addr", "0:0").split(":")[1]) - steam_port)
            except (IndexError, ValueError):
                return 10**9
        rust_servers.sort(key=_port_diff)

        # 2) Один точный A2S к ближайшему порту (сервер отвечает мгновенно)
        server_info = None
        for sv in rust_servers:
            addr = sv.get("addr", "")
            if not addr:
                continue
            server_info = await loop.run_in_executor(None, a2s_query.query_server_at, addr)
            if server_info:
                game_server_ip = addr
                break

        # 3) Fallback: если мастер-сервер пуст — единичный запрос к релейному адресу
        if not server_info:
            server_info = await loop.run_in_executor(None, a2s_query.query_server_at, game_server_ip)

        if server_info:
            current_text = (
                f"🟢 <b>Текущий сервер:</b>\n"
                f"  📛 {server_info['name']}\n"
                f"  👥 {server_info['players']}/{server_info['max_players']}\n"
                f"  🗺 {server_info['map']}\n"
                f"  🌐 <code>{game_server_ip}</code>"
            )
        else:
            current_text = (
                f"🟢 <b>Сейчас на сервере</b>\n"
                f"  🌐 <code>{game_server_ip}</code>"
            )
    elif game_id and (game_id == config.RUST_APP_ID or "rust" in game_extra.lower()):
        current_text = (
            f"🟢 <b>Сейчас играет в Rust</b>\n"
            f"  ⚠️ Адрес сервера неизвестен"
        )
    elif game_id:
        current_text = (
            f"🎮 <b>Сейчас в другой игре</b>\n"
            f"  📛 {game_extra or 'Неизвестно'}"
        )
    elif persona_state > 0:
        current_text = "🟢 <b>В сети</b> — сейчас не в игре"
    else:
        current_text = "🔴 <b>Оффлайн</b>"

    # ─── Недавние серверы (из recently played) ───
    recent_text = ""
    if isinstance(recent, list) and recent:
        lines = []
        for g in recent[:7]:
            name_game = g.get("name", "?")
            mins = g.get("playtime_2weeks", 0)
            total = g.get("playtime_forever", 0)
            if mins > 0:
                lines.append(
                    f"  • <b>{name_game}</b>\n"
                    f"    📅 За 2 недели: {format_playtime(mins)}  |  🕐 Всего: {format_playtime(total)}"
                )
        if lines:
            recent_text = "\n\n📅 <b>Активность за 2 недели:</b>\n" + "\n".join(lines)

    # ─── Собираем сообщение ───
    msg = (
        f"🎮 <b>Игры: {name}</b>\n"
        f"{'═' * 22}\n\n"
        f"{current_text}"
        f"{recent_text}\n\n"
        f"💡 IP сервера: <code>/server ip:port</code>\n"
        f"👥 Игроки: <code>/players ip:port</code>"
    )

    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data=f"refresh:{steam_id}:{user_input}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)


# ────────────────────────────────────────────
#  Команда /server
# ────────────────────────────────────────────

async def cmd_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Укажи IP и порт сервера.\n\n"
            "Пример: <code>/server 185.25.217.34:28015</code>\n\n"
            "💡 IP и порт найди на battlemetrics.com/servers/rust",
            parse_mode="HTML",
        )
        return

    address = context.args[0]
    await update.message.chat.send_action("typing")

    # Запускаем A2S запрос в отдельном потоке (он блокирующий)
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, __import__("a2s_query").query_server, address)

    ip, _, port_str = address.rpartition(":")
    port = int(port_str) if port_str.isdigit() else 28015

    if not info:
        # UDP не ответил — пробуем статус из реестра Steam (HTTP)
        steam_servers = await steam_api.get_server_list(address)
        target = _pick_steam_server(steam_servers, address)
        if target:
            msg = (
                f"🖥 <b>Сервер Rust</b> (данные Steam)\n\n"
                f"📛 <b>{target.get('name', '?')}</b>\n"
                f"🗺 Карта: {target.get('map', '?')}\n"
                f"👥 Игроки: <b>{target.get('players', 0)}/{target.get('max_players', 0)}</b>\n"
                f"🌐 <code>{target.get('addr', address)}</code>\n\n"
                f"⚠️ Сервер не отвечает на UDP-запросы с сети хостинга бота.\n"
                f"Данные из реестра Steam — обновляются раз в несколько минут."
            )
            await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
            return

        await update.message.reply_text(
            f"❌ Сервер <code>{address}</code> не отвечает.\n\n"
            "Проверь IP и порт. Убедись, что сервер онлайн.",
            parse_mode="HTML",
        )
        return

    # Ищем живую карту сервера (Leaf webmap и популярные порты)
    map_url = await find_server_map_url(ip, port)

    msg = (
        f"🖥 <b>Сервер Rust</b>\n\n"
        f"📛 <b>{info['name']}</b>\n"
        f"🗺 Карта: {info['map']}\n"
        f"👥 Игроки: <b>{info['players']}/{info['max_players']}</b>\n"
        f"{'🔒 VAC' if info['vac'] else ''}"
    )
    if map_url:
        msg += f"\n\n🗺️ <a href=\"{map_url}\">Живая карта сервера</a>"

    await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)


# ────────────────────────────────────────────
#  Команда /players
# ────────────────────────────────────────────

async def cmd_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Укажи IP и порт сервера.\n\n"
            "Пример: <code>/players 185.25.217.34:28015</code>",
            parse_mode="HTML",
        )
        return

    address = context.args[0]
    await update.message.chat.send_action("typing")

    players = await _fetch_players_cached(address)

    if players is None:
        # UDP не ответил — показываем статус сервера из реестра Steam
        steam_servers = await steam_api.get_server_list(address)
        target = _pick_steam_server(steam_servers, address)
        if target:
            await update.message.reply_text(
                f"🟢 <b>Сервер онлайн</b> — <b>{target.get('players', 0)}</b>/{target.get('max_players', 0)} игроков\n\n"
                f"📛 {target.get('name', '?')}\n"
                f"🗺 Карта: {target.get('map', '?')}\n\n"
                f"ℹ️ Сервер не отвечает на UDP-запросы с сети хостинга — ники недоступны.\n"
                f"⚡ Статус: <code>/server {address}</code>",
                parse_mode="HTML",
            )
            return
        await update.message.reply_text(
            f"❌ Сервер <code>{address}</code> не отвечает.",
            parse_mode="HTML",
        )
        return

    text, markup = _render_players_page(players, 0, address)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


# ────────────────────────────────────────────
#  Callback: листание игроков (◀ ▶)
# ────────────────────────────────────────────

async def callback_players_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("|")
    if len(parts) != 3:
        await query.answer()
        return

    address, page_str = parts[1], parts[2]
    page = int(page_str) if page_str.isdigit() else 0

    players = await _fetch_players_cached(address)
    if players is None:
        await query.answer("Данные устарели — отправь /players заново", show_alert=True)
        return

    text, markup = _render_players_page(players, page, address)
    await query.answer()
    if markup is None:
        await query.edit_message_text(text, parse_mode="HTML")
    else:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


# ────────────────────────────────────────────
#  Запуск бота
# ────────────────────────────────────────────

def main():
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан!")
    if not config.STEAM_API_KEY:
        raise RuntimeError("STEAM_API_KEY не задан!")

    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("server", cmd_server))
    app.add_handler(CommandHandler("players", cmd_players))
    app.add_handler(CallbackQueryHandler(callback_refresh, pattern=r"^refresh:"))
    app.add_handler(CallbackQueryHandler(callback_stats, pattern=r"^stats:"))
    app.add_handler(CallbackQueryHandler(callback_servers, pattern=r"^servers:"))
    app.add_handler(CallbackQueryHandler(callback_players_page, pattern=r"^players\|"))

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
