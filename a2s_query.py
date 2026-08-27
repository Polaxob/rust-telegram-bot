import socket
import struct


HEADER = b'\xFF\xFF\xFF\xFF'
INFO_REQUEST = HEADER + b'\x54\x53\x6F\x75\x72\x63\x65\x20\x45\x6E\x67\x69\x6E\x65\x20\x51\x75\x65\x72\x79\x00'
PLAYERS_REQUEST = HEADER + b'\x55'
RULES_REQUEST = HEADER + b'\x56'


def _send_recv(address: str, request: bytes, timeout: float = 2.0) -> bytes | None:
    """Отправить UDP-пакет и получить ответ."""
    ip, port = address.split(":")
    port = int(port)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(request, (ip, port))
        data, _ = sock.recvfrom(4096)
        sock.close()
        return data
    except Exception:
        return None


def _parse_info(data: bytes) -> dict | None:
    """Парсим A2S_INFO ответ."""
    if len(data) < 16:
        return None

    # Пропускаем заголовок (5 байт FF + 1 байт ответа)
    pos = 5 + 1  # header + response type
    try:
        protocol = data[pos]; pos += 1
        # server name
        name_end = data.index(b'\x00', pos)
        name = data[pos:name_end].decode('utf-8', errors='replace'); pos = name_end + 1
        # map
        map_end = data.index(b'\x00', pos)
        game_map = data[pos:map_end].decode('utf-8', errors='replace'); pos = map_end + 1
        # folder
        folder_end = data.index(b'\x00', pos)
        pos = folder_end + 1
        # game
        game_end = data.index(b'\x00', pos)
        game = data[pos:game_end].decode('utf-8', errors='replace'); pos = game_end + 1
        # players
        players = data[pos]; pos += 1
        max_players = data[pos]; pos += 1
        # protocol, name, map, folder, game already read
        # server type, visibility, vac
        pos += 1  # server type
        pos += 1  # visibility
        vac = bool(data[pos]); pos += 1

        return {
            "name": name,
            "map": game_map,
            "players": players,
            "max_players": max_players,
            "game": game,
            "tags": "",
            "vac": vac,
        }
    except (IndexError, ValueError):
        return None


def _try_query(address: str, timeout: float = 3.0) -> dict | None:
    """Пробует A2S запрос к одному адресу."""
    data = _send_recv(address, INFO_REQUEST, timeout)
    if data:
        return _parse_info(data)
    return None


def query_server(address: str, timeout: float = 5.0) -> dict | None:
    """
    Запрос информации о сервере через A2S с автоматическим подбором порта.
    Steam API часто отдаёт игровой порт, а A2S работает на другом.
    """
    parts = address.split(":")
    ip = parts[0]
    given_port = int(parts[1]) if len(parts) > 1 else 28015

    # Порты для попыток: данный, потом стандартные Rust порты (максимум 4)
    ports_to_try = [given_port]
    common_ports = [28015, 28016, 27015]
    for p in common_ports:
        if p not in ports_to_try and len(ports_to_try) < 4:
            ports_to_try.append(p)

    for port in ports_to_try:
        result = _try_query(f"{ip}:{port}", timeout=min(timeout, 2.0))
        if result:
            return result

    return None


def _parse_players(data: bytes) -> list[dict] | None:
    """Парсим A2S Players ответ."""
    if len(data) < 6:
        return None

    pos = 5 + 1  # header + response type
    num_players = data[pos]; pos += 1
    result = []
    try:
        for _ in range(num_players):
            idx = data[pos]; pos += 1
            name_end = data.index(b'\x00', pos)
            name = data[pos:name_end].decode('utf-8', errors='replace'); pos = name_end + 1
            score = struct.unpack('<i', data[pos:pos+4])[0]; pos += 4
            duration = struct.unpack('<f', data[pos:pos+4])[0]; pos += 4
            result.append({"name": name, "score": score, "duration": duration})
        return result
    except Exception:
        return None


def query_players(address: str, timeout: float = 5.0) -> list[dict] | None:
    """Запрос списка игроков с автоматическим подбором порта."""
    parts = address.split(":")
    ip = parts[0]
    given_port = int(parts[1]) if len(parts) > 1 else 28015

    ports_to_try = [given_port]
    common_ports = [28015, 28016, 27015]
    for p in common_ports:
        if p not in ports_to_try and len(ports_to_try) < 4:
            ports_to_try.append(p)

    for port in ports_to_try:
        data = _send_recv(f"{ip}:{port}", PLAYERS_REQUEST, timeout=min(timeout, 2.0))
        if data:
            result = _parse_players(data)
            if result is not None:
                return result

    return None
