import a2s
import socket


def query_server(address: str, timeout: float = 5.0) -> dict | None:
    """
    Запрос информации о Rust сервере через A2S.
    address — 'ip:port', например '185.25.217.34:28015'
    Возвращает dict с инфо о сервере или None.
    """
    try:
        info = a2s.info(address, timeout=timeout)
        return {
            "name": info.server_name,
            "map": info.map_name,
            "players": info.players,
            "max_players": info.max_players,
            "game": info.game,
            "tags": info.keywords if hasattr(info, "keywords") else "",
            "vac": info.vac_enabled if hasattr(info, "vac_enabled") else False,
        }
    except (socket.timeout, ConnectionRefusedError, OSError, Exception) as e:
        print(f"A2S error for {address}: {e}")
        return None


def query_players(address: str, timeout: float = 5.0) -> list[dict] | None:
    """
    Запрос списка игроков на сервере через A2S.
    Возвращает список dict с name/duration/score или None.
    """
    try:
        players = a2s.players(address, timeout=timeout)
        result = []
        for p in players:
            result.append({
                "name": p.name,
                "score": p.score,
                "duration": p.duration,
            })
        return result
    except Exception as e:
        print(f"A2S players error for {address}: {e}")
        return None
