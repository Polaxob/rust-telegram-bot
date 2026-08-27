import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


HEADER = b'\xFF\xFF\xFF\xFF'
INFO_REQUEST = HEADER + b'\x54\x53\x6F\x75\x72\x63\x65\x20\x45\x6E\x67\x69\x6E\x65\x20\x51\x75\x65\x72\x79\x00'

# Простой кэш: addr -> (порядковый, результат)
_INFO_CACHE = {}
_INFO_CACHE_TTL = 60  # секунд


def _cache_get(key: str):
    item = _INFO_CACHE.get(key)
    if item and time.time() - item[0] < _INFO_CACHE_TTL:
        return item[1]
    return None


def _cache_set(key: str, value):
    _INFO_CACHE[key] = (time.time(), value)


def _decode_string(raw: bytes, start: int = 0):
    """Читаем null-terminated строку, пробуя UTF-8 потом CP1251."""
    end = raw.index(b'\x00', start)
    data = raw[start:end]
    try:
        return data.decode('utf-8'), end + 1
    except UnicodeDecodeError:
        return data.decode('cp1251', errors='replace'), end + 1


def _send_recv(address: str, request: bytes, timeout: float = 1.5, attempts: int = 2) -> bytes | None:
    """Отправить UDP-пакет и получить ответ. Ретраи на случай потери пакетов."""
    ip, port = address.split(":")
    port = int(port)
    for _ in range(attempts):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.sendto(request, (ip, port))
            data, _ = sock.recvfrom(4096)
            sock.close()
            return data
        except Exception:
            continue
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


def _try_query(address: str, timeout: float = 1.5) -> dict | None:
    """Пробует A2S запрос к одному адресу (с кэшем)."""
    cached = _cache_get(address)
    if cached is not None:
        return cached
    data = _send_recv(address, INFO_REQUEST, timeout)
    result = _parse_info(data) if data else None
    _cache_set(address, result)
    return result


def _probe_ports(ip: str, ports: list[int], total_budget: float = 5.0) -> tuple[str | None, dict | None]:
    """Параллельный A2S_INFO опрос портов. Возвращает (address, info) первого ответа.

    INFO кэшируется (60 сек), поэтому повторные запросы к серверу почти мгновенные.
    """
    if not ports:
        return None, None
    ex = ThreadPoolExecutor(max_workers=len(ports))
    futs = {ex.submit(_try_query, f"{ip}:{p}"): p for p in ports}
    try:
        for fut in as_completed(futs, timeout=total_budget):
            result = fut.result()
            if result:
                return f"{ip}:{futs[fut]}", result
    except Exception:
        pass
    finally:
        ex.shutdown(wait=False)
    return None, None


def query_server(address: str, timeout: float = 5.0) -> dict | None:
    """
    Запрос информации о сервере через A2S с автоматическим подбором порта.
    Steam API часто отдаёт игровой порт, а A2S работает на другом.
    Порт пробуются параллельно — берётся ответ первого живого.
    """
    parts = address.split(":")
    ip = parts[0]
    given_port = int(parts[1]) if len(parts) > 1 else 28015

    ports_to_try = [given_port]
    common_ports = [28015, 2302, 27015, 27016, 20010]
    for p in common_ports:
        if p not in ports_to_try and len(ports_to_try) < 5:
            ports_to_try.append(p)

    _, result = _probe_ports(ip, ports_to_try, total_budget=min(timeout, 3.5))
    return result


def query_server_at(address: str, timeout: float = 1.5) -> dict | None:
    """Быстрый единичный A2S запрос к точному адресу (один порт, с ретраем)."""
    return _try_query(address, timeout=timeout)


def _players_with_challenge(address: str, timeout: float = 1.5, attempts: int = 2) -> list[dict] | None:
    """A2S_PLAYERS с обработкой challenge и ретраями (UDP теряет пакеты).

    Каждая попытка начинает рукопожатие заново: challenge-токен одноразовый,
    повторная отправка уже использованного токена сервером игнорируется.
    """
    ip, port = address.split(":")
    port = int(port)
    for _ in range(attempts):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            # Шаг 1: запрос без challenge
            sock.sendto(HEADER + b'\x55', (ip, port))
            data, _ = sock.recvfrom(4096)

            # Если пришёл challenge (тип 0x41 'A' в байте 4) — отвечаем с ним
            if len(data) >= 9 and data[4] == 0x41:
                sock.sendto(HEADER + b'\x55' + data[5:9], (ip, port))
                data, _ = sock.recvfrom(4096)

            sock.close()
            return _parse_players(data)
        except Exception:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            continue
    return None


def _parse_players(data: bytes) -> list[dict] | None:
    """Парсим A2S Players ответ."""
    if len(data) < 6:
        return None

    try:
        pos = 5  # header(4) + response type(1)
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
    """
    Запрос списка игроков с автоматическим подбором порта.

    1) Быстрый путь: A2S_PLAYERS сразу на указанном порту, без пачки пакетов —
       чтобы не спровоцировать анти-флуд сервера.
    2) Запасной путь (если указанный порт молчит): параллельная проверка
       распространённых портов через A2S_INFO и A2S_PLAYERS на первом живом.
    """
    parts = address.split(":")
    ip = parts[0]
    given_port = int(parts[1]) if len(parts) > 1 else 28015

    # 1) Быстрый путь: только указанный порт
    result = _players_with_challenge(f"{ip}:{given_port}", timeout=1.5, attempts=2)
    if result is not None:
        return result

    # 2) Запасной путь: распространённые порты параллельно
    common_ports = [28015, 2302, 27015, 27016, 20010]
    other_ports = [p for p in common_ports if p != given_port][:4]

    live_addr, _ = _probe_ports(ip, other_ports, total_budget=min(timeout, 2.5))
    if live_addr:
        result = _players_with_challenge(live_addr, timeout=1.5, attempts=2)
        if result is not None:
            return result
        # Сервер жив (INFO ответил), но список игроков не отдал — это не «не отвечает»
        return []
    return None