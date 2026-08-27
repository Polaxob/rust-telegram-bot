import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import config
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
        "<code>/profile nickname</code> — поиск по нику (онлайн первыми)\n"
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
#  Поиск игроков по нику
# ────────────────────────────────────────────

async def _show_search_results(update: Update, nick: str):
    """Поиск по нику: список игроков, онлайн первыми."""
    await update.message.chat.send_action("typing")

    players = await steam_api.search_players(nick, limit=10)
    if not players:
        # Если поиск пуст — попробуем резолвить как кастомную ссылку (id/nick)
        steam_id = await steam_api.resolve_vanity_url(nick)
        if steam_id:
            msg, reply_markup = await _build_profile_message(steam_id, nick)
            if msg:
                await update.message.reply_text(
                    msg, parse_mode="HTML", reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )
                return
        await update.message.reply_text(
            f"❌ Steam не нашёл игроков с ником «{nick}».\n\n"
            "Это может быть, если ник сменили недавно или профиль не "
            "попал в поиск Steam.\n\n"
            "🔥 <b>Надёжный способ:</b> пришли ссылку на свой профиль:\n"
            "<code>/profile steamcommunity.com/id/твой_ник</code>\n"
            "или SteamID из клиента Steam:\n"
            "Профиль → Изменить профиль → Ссылка на аккаунт.",
        )
        return

    # Параллельно получаем summaries, чтобы определить кто онлайн
    steam_ids = [p["steam_id"] for p in players]
    summaries = await asyncio.gather(
        *(steam_api.get_player_summary(sid) for sid in steam_ids),
        return_exceptions=True,
    )

    enriched = []
    for p, s in zip(players, summaries):
        online = isinstance(s, dict) and s.get("personastate", 0) > 0
        country = (s.get("loccountrycode", "") if isinstance(s, dict) else "") or p.get("country", "")
        enriched.append({**p, "online": online, "country": country})

    # Онлайн первыми, потом оффлайн
    enriched.sort(key=lambda x: not x["online"])

    lines = [f"🔎 <b>Найдено по нику «{nick}»:</b>\n"]
    keyboard_rows = []
    for i, p in enumerate(enriched, 1):
        dot = "🟢" if p["online"] else "🔴"
        flag = COUNTRY_FLAGS.get(p["country"], "")
        lines.append(f"{dot} <b>{p['name']}</b> {flag}")
        keyboard_rows.append([
            InlineKeyboardButton(f"{i}. {p['name']}", callback_data=f"pick:{p['steam_id']}")
        ])

    reply_markup = InlineKeyboardMarkup(keyboard_rows)
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=reply_markup
    )


COUNTRY_FLAGS = {
    "US": "🇺🇸", "DE": "🇩🇪", "FR": "🇫🇷", "GB": "🇬🇧",
    "RU": "🇷🇺", "NL": "🇳🇱", "AU": "🇦🇺", "SE": "🇸🇪",
    "FI": "🇫🇮", "NO": "🇳🇴", "PL": "🇵🇱", "BR": "🇧🇷",
    "UA": "🇺🇦", "KZ": "🇰🇿", "TR": "🇹🇷", "IL": "🇮🇱",
    "BY": "🇧🇾", "CA": "🇨🇦", "CZ": "🇨🇿", "ES": "🇪🇸",
    "IT": "🇮🇹", "JP": "🇯🇵", "KR": "🇰🇷", "MX": "🇲🇽",
    "CN": "🇨🇳", "IN": "🇮🇳", "AR": "🇦🇷", "CH": "🇨🇭",
    "AT": "🇦🇹", "BE": "🇧🇪", "BG": "🇧🇬", "HR": "🇭🇷",
    "DK": "🇩🇰", "EE": "🇪🇪", "GR": "🇬🇷", "HU": "🇭🇺",
    "IE": "🇮🇪", "LV": "🇱🇻", "LT": "🇱🇹", "LU": "🇱🇺",
    "MD": "🇲🇩", "PT": "🇵🇹", "RO": "🇷🇴", "RS": "🇷🇸",
    "SK": "🇸🇰", "SI": "🇸🇮", "ZA": "🇿🇦", "NG": "🇳🇬",
    "EG": "🇪🇬", "AE": "🇦🇪", "SA": "🇸🇦", "IR": "🇮🇷",
    "PK": "🇵🇰", "BD": "🇧🇩", "TH": "🇹🇭", "VN": "🇻🇳",
    "PH": "🇵🇭", "ID": "🇮🇩", "MY": "🇲🇾", "SG": "🇸🇬",
    "NZ": "🇳🇿", "CL": "🇨🇱", "CO": "🇨🇴", "PE": "🇵🇪",
}


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

    # Определяем тип ввода: SteamID64 (17 цифр) или ссылка — показываем профиль напрямую
    is_direct = user_input.isdigit() and len(user_input) == 17
    is_url = "steamcommunity.com" in user_input

    if not is_direct and not is_url:
        # Поиск по нику: показываем список игроков (онлайн первыми)
        await _show_search_results(update, user_input)
        return

    steam_id = await steam_api.resolve_input_to_steam_id(user_input)
    if not steam_id:
        await update.message.reply_text(
            f"❌ Не удалось найти игрока «{user_input}».\n"
            "Проверь правильность ввода.",
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
#  Callback: Выбран игрок из списка поиска
# ────────────────────────────────────────────

async def callback_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Открываю профиль...")

    data = query.data.split(":", 1)
    steam_id = data[1] if len(data) > 1 else ""
    if not steam_id.isdigit():
        await query.edit_message_text("❌ Ошибка: неверный SteamID.")
        return

    msg, reply_markup = await _build_profile_message(steam_id, steam_id)
    if msg is None:
        await query.edit_message_text(
            "❌ Не удалось получить данные из Steam.\n"
            "Возможно, SteamID неверный или профиль приватный.",
        )
        return

    await query.edit_message_text(
        msg, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True
    )


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

    if not info:
        await update.message.reply_text(
            f"❌ Сервер <code>{address}</code> не отвечает.\n\n"
            "Проверь IP и порт. Убедись, что сервер онлайн.",
            parse_mode="HTML",
        )
        return

    msg = (
        f"🖥 <b>Сервер Rust</b>\n\n"
        f"📛 <b>{info['name']}</b>\n"
        f"🗺 Карта: {info['map']}\n"
        f"👥 Игроки: <b>{info['players']}/{info['max_players']}</b>\n"
        f"{'🔒 VAC' if info['vac'] else ''}\n"
        f"\n🔗 <a href=\"https://www.gametracker.com/server_info/{address}\">GameTracker</a>"
    )

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

    loop = asyncio.get_event_loop()
    players = await loop.run_in_executor(None, __import__("a2s_query").query_players, address)

    if players is None:
        await update.message.reply_text(
            f"❌ Сервер <code>{address}</code> не отвечает.",
            parse_mode="HTML",
        )
        return

    if not players:
        await update.message.reply_text(f"👥 На сервере <code>{address}</code> никого нет.")
        return

    lines = [f"👥 <b>Игроки на сервере</b> ({len(players)}):\n"]
    for i, p in enumerate(players[:30], 1):
        dur = format_duration(p["duration"])
        name = p["name"] if p["name"] else "???"
        lines.append(f"{i}. {name} — {dur}")

    if len(players) > 30:
        lines.append(f"\n... и ещё {len(players) - 30} игрок(ов)")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


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
    app.add_handler(CallbackQueryHandler(callback_pick, pattern=r"^pick:"))

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
