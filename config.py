import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token (от @BotFather)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Steam Web API Key (https://steamcommunity.com/dev/apikey)
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")

# BattleMetrics API Key (https://battlemetrics.com/developers)
BM_API_KEY = os.getenv("BM_API_KEY", "")

# RCON — ники для своего сервера, когда UDP с хостинга недоступен.
# RCON_SERVER: "ip:port" игрового сервера (пароль шлётся только ему)
# RCON_PORT: порт RCON (по умолчанию 28016)
try:
    RCON_PORT = int(os.getenv("RCON_PORT", "28016"))
except ValueError:
    RCON_PORT = 28016
RCON_SERVER = os.getenv("RCON_SERVER", "")
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "")

# Telegram ID владельца — сюда приходят сообщения из кнопки «Поддержка»
try:
    OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "") or 0) or None
except ValueError:
    OWNER_CHAT_ID = None

# App ID Rust в Steam
RUST_APP_ID = 252490
