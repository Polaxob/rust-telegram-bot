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
    """Конвертация минут в читаемый формат: часы и минуты."""
    if minutes is None:
        return "Н/Д"
    hours = minutes // 60
    mins = minutes % 60
    if hours >= 24:
        days = hours // 24
        h = hours % 24
        if mins > 0:
            return f"{days} дн {h} ч {mins} мин"
        return f"{days} дн {h} ч"
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
        "<code>/profile 76561198012345678</code>\n"
        "<code>/profile nickname</code>\n"
        "<code>/profile steamcommunity.com/id/nickname</code>\n\n"
        "Покажет: аватар, имя, онлайн/оффлайн, часы в Rust, баны.\n\n"
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

    steam_id = await steam_api.resolve_input_to_steam_id(user_input)
    if not steam_id:
        await update.message.reply_text(
            f"❌ Не удалось найти игрока «{user_input}».\n"
            "Проверь правильность ввода.",
        )
        return

    summary_task = steam_api.get_player_summary(steam_id)
    games_task = steam_api.get_owned_games(steam_id)
    bans_task = steam_api.get_player_bans(steam_id)

    summary, games, bans = await asyncio.gather(
        summary_task, games_task, bans_task,
        return_exceptions=True,
    )

    if isinstance(summary, Exception) or summary is None:
        await update.message.reply_text(
            "❌ Не удалось получить данные из Steam.\n"
            "Возможно, SteamID неверный или профиль приватный.",
        )
        return

    name = summary.get("personaname", "Неизвестно")
    avatar = summary.get("avatarfull", "")
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
    in_rust = False
    game_id = summary.get("gameid", 0)
    game_extra = summary.get("gameextrainfo", "")
    game_name = summary.get("gamename", "")

    if game_id:
        # Есть gameid = игрок точно в игре
        if game_id == config.RUST_APP_ID:
            in_rust = True
            game_text = "\n🟢 <b>Сейчас играет в Rust</b>"
        elif game_extra:
            game_text = f"\n🎮 Сейчас в другой игре: <b>{game_extra}</b>"
        elif game_name:
            game_text = f"\n🎮 Сейчас в другой игре: <b>{game_name}</b>"
        else:
            game_text = "\n🎮 Сейчас играет"

    # Rust-онлайн
    rust_status = "🟢 Да — в сети" if in_rust else "🔴 Нет"

    # Последний вход (последняя активность в Steam)
    last_seen = ""
    if last_logoff:
        dt = datetime.fromtimestamp(last_logoff, tz=timezone.utc)
        ago = time_ago(dt.isoformat())
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
        "UA": "🇺🇦", "KZ": "🇰🇿", "TR": "🇹🇷", "UA": "🇺🇦",
    }.get(country, "")

    msg = (
        f"{'🟢' if persona_state > 0 else '🔴'} <b>{name}</b>\n\n"
        f"🆔 <code>{steam_id}</code>\n"
        f"{'🌍 ' + country_emoji + ' ' + country if country else ''}\n"
        f"📊 Статус: <b>{state_text}</b>"
        f"{game_text}\n"
        f"🟠 <b>В сети в Rust:</b> {rust_status}"
        f"{last_seen}\n\n"
        f"⏱ <b>Общее время в Rust:</b> {rust_hours}"
        f"{ban_text}\n\n"
        f"🔗 <a href=\"{profile_url}\">Открыть профиль в Steam</a>"
    )

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{steam_id}:{user_input}"),
         InlineKeyboardButton("📊 Статистика", callback_data=f"stats:{steam_id}:{user_input}"),
         InlineKeyboardButton("🎮 Серверы", callback_data=f"servers:{steam_id}:{user_input}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
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

    last_seen = ""
    if last_logoff:
        dt = datetime.fromtimestamp(last_logoff, tz=timezone.utc)
        ago = time_ago(dt.isoformat())
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
    if game_id:
        if game_id == config.RUST_APP_ID:
            in_rust = True
            game_text = "\n🟢 <b>Сейчас играет в Rust</b>"
        elif game_extra:
            game_text = f"\n🎮 Сейчас в другой игре: <b>{game_extra}</b>"
        else:
            game_text = "\n🎮 Сейчас играет"

    rust_status = "🟢 Да — в сети" if in_rust else "🔴 Нет"

    msg = (
        f"{'🟢' if persona_state > 0 else '🔴'} <b>{name}</b>\n\n"
        f"🆔 <code>{steam_id}</code>\n"
        f"📊 Статус: <b>{state_text}</b>"
        f"{game_text}\n"
        f"🟠 <b>В сети в Rust:</b> {rust_status}"
        f"{last_seen}\n\n"
        f"⏱ <b>Общее время в Rust:</b> {rust_hours}"
        f"{ban_text}\n\n"
        f"🔗 <a href=\"{profile_url}\">Открыть профиль в Steam</a>"
    )

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{steam_id}:{user_input}"),
         InlineKeyboardButton("📊 Статистика", callback_data=f"stats:{steam_id}:{user_input}"),
         InlineKeyboardButton("🎮 Серверы", callback_data=f"servers:{steam_id}:{user_input}")]
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
    if game_server_ip:
        # Есть IP сервера — запрашиваем через A2S
        import a2s_query
        loop = asyncio.get_event_loop()
        server_info = await loop.run_in_executor(None, a2s_query.query_server, game_server_ip)
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
                f"🟢 <b>Текущий сервер:</b>\n"
                f"  🌐 <code>{game_server_ip}</code>\n"
                f"  ⏳ Не удалось получить инфо (сервер может быть закрыт)"
            )
    elif game_id == config.RUST_APP_ID:
        current_text = (
            f"🟢 <b>Сейчас играет в Rust</b>\n"
            f"  ⚠️ IP сервера скрыт (приватный сервер)"
        )
    elif game_id:
        current_text = (
            f"🎮 <b>Сейчас в другой игре</b>\n"
            f"  📛 {game_extra or 'Неизвестно'}"
        )
    else:
        state = PERSONA_STATES.get(persona_state, "Оффлайн")
        if persona_state > 0:
            current_text = f"🟢 <b>{state}</b> — сейчас не в игре"
        else:
            current_text = f"🔴 <b>{state}</b> — сейчас не в игре"

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
        f"🎮 <b>Серверы: {name}</b>\n"
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

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
