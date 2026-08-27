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
    if minutes is None:
        return "Н/Д"
    hours = minutes // 60
    mins = minutes % 60
    if hours >= 1000:
        days = hours // 24
        h = hours % 24
        return f"~{days}д {h}ч"
    if hours > 0:
        return f"{hours}ч {mins}м"
    return f"{mins}м"


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
    rust_hours = "🔒 Приватный профиль"
    if not is_private and isinstance(games, list):
        for game in games:
            if game.get("appid") == config.RUST_APP_ID:
                rust_hours = format_playtime(game.get("playtime_forever"))
                break
        else:
            rust_hours = "Не найдено"

    # Текущая игра
    game_text = ""
    if persona_state == 6:
        game_extra = summary.get("gameextrainfo", "")
        game_name = summary.get("gamename", "")
        if game_extra:
            game_text = f"\n🎯 Играет: <b>{game_extra}</b>"
        elif game_name:
            game_text = f"\n🎯 Играет: <b>{game_name}</b>"
        else:
            game_text = "\n🎯 Играет сейчас"

    # Последний вход
    last_seen = ""
    if last_logoff:
        last_seen = f"\n🕐 Последний вход: {time_ago(datetime.fromtimestamp(last_logoff, tz=timezone.utc).isoformat())}"

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
        f"{game_text}"
        f"{last_seen}\n\n"
        f"🎮 <b>Rust:</b> {rust_hours}"
        f"{ban_text}\n\n"
        f"🔗 <a href=\"{profile_url}\">Открыть профиль в Steam</a>"
    )

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{steam_id}:{user_input}")]
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
        last_seen = f"\n🕐 Последний вход: {time_ago(datetime.fromtimestamp(last_logoff, tz=timezone.utc).isoformat())}"

    games = await steam_api.get_owned_games(steam_id)
    rust_hours = "🔒 Приватный профиль"
    visibility = summary.get("communityvisibilitystate", 1)
    is_private = visibility in (1, 2)
    if not is_private and isinstance(games, list):
        for game in games:
            if game.get("appid") == config.RUST_APP_ID:
                rust_hours = format_playtime(game.get("playtime_forever"))
                break
        else:
            rust_hours = "Не найдено"

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
    if persona_state == 6:
        game_extra = summary.get("gameextrainfo", "")
        if game_extra:
            game_text = f"\n🎯 Играет: <b>{game_extra}</b>"
        else:
            game_text = "\n🎯 Играет сейчас"

    msg = (
        f"{'🟢' if persona_state > 0 else '🔴'} <b>{name}</b>\n\n"
        f"🆔 <code>{steam_id}</code>\n"
        f"📊 Статус: <b>{state_text}</b>"
        f"{game_text}"
        f"{last_seen}\n\n"
        f"🎮 <b>Rust:</b> {rust_hours}"
        f"{ban_text}\n\n"
        f"🔗 <a href=\"{profile_url}\">Открыть профиль в Steam</a>"
    )

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{steam_id}:{user_input}")]
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

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
