import socket
import struct


HEADER = b'\xFF\xFF\xFF\xFF'
INFO_REQUEST = HEADER + b'\x54\x53\x6F\x75\x72\x63\x65\x20\x45\x6E\x67\x69\x6E\x65\x20\x51\x75\x65\x72\x79\x00'


def _decode_string(raw: bytes, start: int = 0):
    """Читаем null-terminated строку, пробуя UTF-8 потом CP1251."""
    end = raw.index(b'\x00', start)
    data = raw[start:end]
    try:
        return data.decode('utf-8'), end + 1
    except UnicodeDecodeError:
        return data.decode('cp1251', errors='replace'), end + 1


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
    """Парсим A2S_INFO ответ (Source Engine + Rust)."""
    if len(data) < 10:
        return None

    try:
        pos = 6  # header(4) + response type(1) + protocol(1)
        name, pos = _decode_string(data, pos)
        game_map, pos = _decode_string(data, pos)
        folder, pos = _decode_string(data, pos)
        game, pos = _decode_string(data, pos)

        # appid (short) — Rust = 252490
        appid = struct.unpack('<H', data[pos:pos+2])[0]; pos += 2

        players = data[pos]; pos += 1
        max_players = data[pos]; pos += 1
        bots = data[pos]; pos += 1

        return {
            "name": name,
            "map": game_map,
            "players": players,
            "max_players": max_players,
            "game": game,
            "appid": appid,
            "tags": "",
            "vac": False,
        }
    except Exception:
        return None


def _try_query(address: str, timeout: float = 2.0) -> dict | None:
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

    ports_to_try = [given_port]
    common_ports = [28015, 2302, 27015, 27016, 20010]
    for p in common_ports:
        if p not in ports_to_try and len(ports_to_try) < 5:
            ports_to_try.append(p)

    for port in ports_to_try:
        result = _try_query(f"{ip}:{port}", timeout=min(timeout, 2.0))
        if result:
            return result

    return None


def _players_with_challenge(address: str, timeout: float = 2.0) -> list[dict] | None:
    """A2S_PLAYERS с обработкой challenge."""
    ip, port = address.split(":")
    port = int(port)
    try:
        # Шаг 1: запрос без challenge
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(HEADER + b'\x55', (ip, port))
        data, _ = sock.recvfrom(4096)

        # Если пришёл challenge (тип 0x41 'A' в байте 4)
        if len(data) >= 9 and data[4] == 0x41:
            challenge = data[5:9]  # 4 байта challenge
            sock.sendto(HEADER + b'\x55' + challenge, (ip, port))
            data, _ = sock.recvfrom(4096)

        sock.close()
        return _parse_players(data)
    except Exception:
        return None


def _parse_players(data: bytes) -> list[dict] | None:
    """Парсим A2S Players ответ."""
    if len(data) < 6:
        return None

    try:
        pos = 5 + 1  # header + response type
        num_players = data[pos]; pos += 1
        result = []
        for _ in range(num_players):
            idx = data[pos]; pos += 1
            name, pos = _decode_string(data, pos)
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
    common_ports = [28015, 2302, 27015, 27016]
    for p in common_ports:
        if p not in ports_to_try and len(ports_to_try) < 4:
            ports_to_try.append(p)

    for port in ports_to_try:
        result = _players_with_challenge(f"{ip}:{port}", timeout=min(timeout, 2.0))
        if result is not None:
            return result

    return None