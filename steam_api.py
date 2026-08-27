import aiohttp
import config
import asyncio

# Блокировка, чтобы не долбить Steam поиск параллельно
_search_lock = asyncio.Lock()


async def search_players(text: str, limit: int = 10) -> list[dict]:
    """
    Поиск игроков Steam по нику через SearchCommunityAjax (работает без логина).
    Возвращает список: [{'steam_id', 'name', 'profile_url', 'avatar', 'country'}, ...]
    """
    if not text or len(text.strip()) < 3:
        return []
    text = text.strip()

    async with _search_lock:
        async with aiohttp.ClientSession() as session:
            # Шаг 1: получаем анонимную сессию (sessionid cookie)
            try:
                async with session.get("https://steamcommunity.com/", timeout=10) as resp:
                    await resp.text()
            except Exception:
                return []

            sessionid = ""
            for c in session.cookie_jar:
                if c.key == "sessionid":
                    sessionid = c.value
                    break

            # Шаг 2: AJAX-поиск пользователей
            url = "https://steamcommunity.com/search/SearchCommunityAjax"
            params = {
                "text": text,
                "filter": "users",
                "sessionid": sessionid,
                "steamid_user": "",
                "page": 1,
            }
            headers = {
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/126.0 Safari/537.36"),
                "Referer": f"https://steamcommunity.com/search/users/#text={text}",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            }
            try:
                async with session.get(url, params=params, headers=headers, timeout=12) as resp:
                    data = await resp.json()
            except Exception:
                return []

            if data.get("success") != 1:
                return []

            html = data.get("html", "")
            return _parse_search_results(html, limit)


def _parse_search_results(html: str, limit: int) -> list[dict]:
    """Распарсить HTML результатов поиска Steam."""
    import re

    results = []
    # Каждый результат — блок search_row
    for block in re.findall(r'<div class="search_row".*?(?=<div class="search_row"|$)', html, re.S):
        m_account = re.search(r'data-miniprofile="(\d+)"', block)
        m_name = re.search(r'class="searchPersonaName"[^>]*>([^<]+)</a>', block)
        m_url = re.search(r'href="(https://steamcommunity\.com/[^"]+)"', block)
        m_country = re.search(r'countryflags/([a-z]{2})\.gif', block, re.I)
        m_avatar = re.search(r'<img src="(https://[^"]+_medium\.jpg)"', block)

        if not m_account or not m_name or not m_url:
            continue

        account_id = int(m_account.group(1))
        steam_id = str(76561197960265728 + account_id)
        results.append({
            "steam_id": steam_id,
            "name": m_name.group(1).strip(),
            "profile_url": m_url.group(1),
            "avatar": m_avatar.group(1) if m_avatar else "",
            "country": m_country.group(1).lower() if m_country else "",
        })
        if len(results) >= limit:
            break

    return results


async def get_player_summary(steam_id: str) -> dict | None:
    """Получить информацию об игроке из Steam."""
    url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
    params = {
        "key": config.STEAM_API_KEY,
        "steamids": steam_id,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=10) as resp:
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
        async with session.get(url, params=params, timeout=10) as resp:
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
        async with session.get(url, params=params, timeout=10) as resp:
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
        async with session.get(url, params=params, timeout=10) as resp:
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
        async with session.get(url, params=params, timeout=10) as resp:
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
        async with session.get(url, params=params, timeout=10) as resp:
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
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("response", {}).get("games", [])


async def get_servers_at_address(ip: str) -> list[dict]:
    """
    Найти серверы, зарегистрированные на IP в мастер-сервере Steam.
    Steam API отдаёт для игрока релейный адрес (например port 20000),
    а реальные серверы на этом IP могут быть на других портах (20010 и т.д.).
    Возвращает список: [{'addr': 'ip:port', 'gamedir': 'rust', ...}]
    """
    url = "https://api.steampowered.com/ISteamApps/GetServersAtAddress/v1/"
    params = {
        "key": config.STEAM_API_KEY,
        "addr": ip,
        "format": "json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("response", {}).get("servers", [])
