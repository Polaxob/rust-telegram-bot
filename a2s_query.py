import a2s
import socket


def _try_query(address: str, timeout: float = 3.0) -> dict | None:
    """Пробует A2S запрос к одному адресу."""
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
    except Exception:
        return None


def query_server(address: str, timeout: float = 5.0) -> dict | None:
    """
    Запрос информации о сервере через A2S с автоматическим подбором порта.
    Steam API часто отдаёт игровой порт (20000-27015), а A2S работает на 28015.
    """
    # Разбираем IP и порт
    parts = address.split(":")
    ip = parts[0]
    given_port = int(parts[1]) if len(parts) > 1 else 28015

    # Порты для попыток: сначала данный, потом стандартные
    ports_to_try = [given_port]
    if given_port != 28015:
        ports_to_try.append(28015)
    if given_port != 28016:
        ports_to_try.append(28016)

    for port in ports_to_try:
        result = _try_query(f"{ip}:{port}", timeout=timeout)
        if result:
            return result

    return None


def _try_players(address: str, timeout: float = 3.0) -> list[dict] | None:
    """Пробует A2S players запрос."""
    try:
        players = a2s.players(address, timeout=timeout)
        return [
            {"name": p.name, "score": p.score, "duration": p.duration}
            for p in players
        ]
    except Exception:
        return None


def query_players(address: str, timeout: float = 5.0) -> list[dict] | None:
    """Запрос списка игроков с автоматическим подбором порта."""
    parts = address.split(":")
    ip = parts[0]
    given_port = int(parts[1]) if len(parts) > 1 else 28015

    ports_to_try = [given_port]
    if given_port != 28015:
        ports_to_try.append(28015)
    if given_port != 28016:
        ports_to_try.append(28016)

    for port in ports_to_try:
        result = _try_players(f"{ip}:{port}", timeout=timeout)
        if result is not None:
            return result

    return None
