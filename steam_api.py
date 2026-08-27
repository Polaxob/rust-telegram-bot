import aiohttp
import config


async def get_player_summary(steam_id: str) -> dict | None:
    """Получить информацию об игроке из Steam."""
    url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
    params = {
        "key": config.STEAM_API_KEY,
        "steamids": steam_id,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            players = data.get("response", {}).get("players", [])
            return players[0] if players else None


async def get_owned_games(steam_id: str) -> list[dict]:
    """Получить список игр игрока."""
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
    params = {
        "key": config.STEAM_API_KEY,
        "steamid": steam_id,
        "include_appinfo": 1,
        "include_played_free_games": 1,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("response", {}).get("games", [])


async def get_player_bans(steam_id: str) -> dict | None:
    """Получить информацию о банах игрока."""
    url = "https://api.steampowered.com/ISteamUser/GetPlayerBans/v1/"
    params = {
        "key": config.STEAM_API_KEY,
        "steamids": steam_id,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            players = data.get("players", [])
            return players[0] if players else None


async def resolve_vanity_url(vanity_url: str) -> str | None:
    """Конвертировать кастомный URL Steam в SteamID."""
    url = "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
    params = {
        "key": config.STEAM_API_KEY,
        "vanityurl": vanity_url,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            response = data.get("response", {})
            if response.get("success") == 1:
                return response.get("steamid")
            return None


async def resolve_input_to_steam_id(user_input: str) -> str | None:
    """
    Универсальный парсер: принимает SteamID64, SteamID3, или кастомный URL.
    Возвращает SteamID64 или None.
    """
    user_input = user_input.strip()

    # Если это числовой SteamID64 (начинается с 76561198)
    if user_input.isdigit() and len(user_input) == 17:
        return user_input

    # Если это кастомный URL (steamcommunity.com/id/xxx)
    if "steamcommunity.com" in user_input:
        parts = user_input.rstrip("/").split("/")
        if "id" in parts:
            vanity = parts[parts.index("id") + 1]
            return await resolve_vanity_url(vanity)
        elif "profiles" in parts:
            sid = parts[parts.index("profiles") + 1]
            if sid.isdigit():
                return sid

    # Если это просто ник / кастомный URL без полного пути
    return await resolve_vanity_url(user_input)


async def get_steam_level(steam_id: str) -> dict | None:
    """Получить уровень Steam-аккаунта."""
    url = "https://api.steampowered.com/IPlayerService/GetSteamLevel/v1/"
    params = {
        "key": config.STEAM_API_KEY,
        "steamid": steam_id,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("response", {})


async def get_friend_count(steam_id: str) -> int | None:
    """Получить количество друзей."""
    url = "https://api.steampowered.com/ISteamUser/GetFriendList/v1/"
    params = {
        "key": config.STEAM_API_KEY,
        "steamid": steam_id,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            friends = data.get("friendslist", {}).get("friends", [])
            return len(friends)


async def get_recent_games(steam_id: str) -> list[dict]:
    """Получить недавно запущенные игры (за 2 недели)."""
    url = "https://api.steampowered.com/IPlayerService/GetRecentlyPlayedGames/v1/"
    params = {
        "key": config.STEAM_API_KEY,
        "steamid": steam_id,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("response", {}).get("games", [])
