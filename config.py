import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token (от @BotFather)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Steam Web API Key (https://steamcommunity.com/dev/apikey)
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")

# BattleMetrics API Key (https://battlemetrics.com/developers)
BM_API_KEY = os.getenv("BM_API_KEY", "")

# App ID Rust в Steam
RUST_APP_ID = 252490
