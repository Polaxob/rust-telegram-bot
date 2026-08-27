import aiohttp
import config


BM_BASE = "https://api.battlemetrics.com"


async def _headers() -> dict:
    headers = {"Authorization": f"Bearer {config.BM_API_KEY}"}
    return headers


async def search_player(name: str) -> dict | None:
    """Найти игрока по имени в BattleMetrics."""
    url = f"{BM_BASE}/players"
    params = {
        "filter[search]": name,
        "filter[game]": "rust",
        "page[size]": 1,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=await _headers()) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            players = data.get("data", [])
            return players[0] if players else None


async def get_player_sessions(player_id: str, limit: int = 5) -> list[dict]:
    """Получить историю сессий игрока."""
    url = f"{BM_BASE}/players/{player_id}/sessions"
    params = {
        "page[size]": limit,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=await _headers()) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("data", [])


async def get_player_server(player_id: str) -> dict | None:
    """Получить текущий сервер игрока (если онлайн)."""
    url = f"{BM_BASE}/players/{player_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=await _headers()) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            player_data = data.get("data", {})
            attrs = player_data.get("attributes", {})
            # Проверяем, онлайн ли игрок
            if attrs.get("online"):
                server_id = attrs.get("serverId")
                if server_id:
                    return await get_server_info(server_id)
            return None


async def get_server_info(server_id: str) -> dict | None:
    """Получить информацию о сервере."""
    url = f"{BM_BASE}/servers/{server_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=await _headers()) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            attrs = data.get("data", {}).get("attributes", {})
            return {
                "name": attrs.get("name", "Неизвестно"),
                "players": attrs.get("players", 0),
                "maxPlayers": attrs.get("maxPlayers", 0),
                "country": attrs.get("country", ""),
                "address": attrs.get("ip", ""),
                "port": attrs.get("port", ""),
                "map": attrs.get("map", ""),
            }


async def search_servers(name: str, limit: int = 5) -> list[dict]:
    """Поиск серверов по названию."""
    url = f"{BM_BASE}/servers"
    params = {
        "filter[search]": name,
        "filter[game]": "rust",
        "page[size]": limit,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=await _headers()) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            servers = []
            for s in data.get("data", []):
                attrs = s.get("attributes", {})
                servers.append({
                    "name": attrs.get("name", ""),
                    "players": attrs.get("players", 0),
                    "maxPlayers": attrs.get("maxPlayers", 0),
                    "country": attrs.get("country", ""),
                    "map": attrs.get("map", ""),
                })
            return servers
