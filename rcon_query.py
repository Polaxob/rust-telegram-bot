"""RCON доступ к серверу Rust через WebSocket (без сторонних зависимостей).

Используется как фолбэк, когда UDP-запросы (A2S) не доходят до сервера
с сети хостинга: RCON работает по TCP, его хостинги обычно не блокируют.

Протокол — официальный Rust WebRcon:
  1. WebSocket handshake (RFC 6455) на rcon-порт
  2. {"Identifier": 1, "Message": "<пароль>", "Name": "WebRcon"} — авторизация
  3. {"Identifier": 2, "Message": "playerlist", "Name": "WebRcon"} — список игроков

Работает ТОЛЬКО для сервера, указанного в RCON_SERVER (ip:port), —
пароль не рассылается по произвольным адресам.
"""

import base64
import json
import logging
import os
import re
import socket
import struct
import time

import config

logger = logging.getLogger(__name__)

RECV_TIMEOUT = 6.0  # сек, ожидание ответа от сервера
CONNECT_TIMEOUT = 6.0

_STEAMID_RE = re.compile(r"\((\d{17})\)")
_CONNECTED_RE = re.compile(
    r"connected (?:(\d+)d ?)?(?:(\d+)h ?)?(?:(\d+)m)?", re.IGNORECASE
)


class RconError(Exception):
    pass


# ────────────────────────────────────────────
#  Мини-клиент WebSocket (RFC 6455)
# ────────────────────────────────────────────

def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RconError("Соединение закрыто сервером")
        buf += chunk
    return buf


def _ws_handshake(sock: socket.socket, host: str, port: int) -> None:
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode("latin-1"))

    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RconError("WebSocket: соединение закрыто при handshake")
        buf += chunk

    head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1", "replace")
    status_line = head.split("\r\n", 1)[0]
    if " 101 " not in status_line:
        raise RconError(f"WebSocket: handshake отклонён ({status_line.strip()})")


def _ws_send_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([0x80 | n])  # бит маски + длина
    elif n < 65536:
        header += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", n)
    sock.sendall(header + mask + masked)


def _ws_recv_frame(sock: socket.socket) -> bytes:
    b0, b1 = _recv_exact(sock, 2)
    opcode = b0 & 0x0F
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    if b1 & 0x80:
        raise RconError("WebSocket: неожиданный masked-фрейм от сервера")
    payload = _recv_exact(sock, length) if length else b""

    if opcode == 0x8:  # close
        raise RconError("WebSocket: сервер закрыл соединение")
    if opcode == 0x9:  # ping → pong
        _ws_send_frame(sock, 0xA, payload)
        return _ws_recv_frame(sock)
    if opcode not in (0x1, 0x2):  # text / binary
        return _ws_recv_frame(sock)
    return payload


def _recv_json(sock: socket.socket) -> dict | None:
    """Читаем текстовые фреймы, пока не соберётся валидный JSON."""
    acc = b""
    deadline = time.time() + RECV_TIMEOUT
    while time.time() < deadline:
        acc += _ws_recv_frame(sock)
        try:
            return json.loads(acc.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
    raise RconError("RCON: таймаут ожидания ответа")


def _send_json(sock: socket.socket, identifier: int, message: str) -> None:
    payload = json.dumps(
        {"Identifier": identifier, "Message": message, "Name": "WebRcon"},
        separators=(",", ":"),
    )
    _ws_send_frame(sock, 0x1, payload.encode("utf-8"))


# ────────────────────────────────────────────
#  Парсинг вывода команды playerlist
# ────────────────────────────────────────────

def parse_playerlist(text: str) -> list[dict]:
    """Ники из консольного вывода Rust. Поддерживает разные форматы:
      "1. marg (7656119...) connected 2h 5m, ping 34ms"
      "#1 marg (7656119...) ..."
      "marg/7656119.../2h5m/34ms"
    Заголовки ("players online: 16") пропускаются.
    """
    players = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _STEAMID_RE.search(line)
        if not m:
            continue
        steam_id = m.group(1)
        name = line[: m.start()].strip()
        # срезаем номер строки в начале: "1." / "1)" / "#1 " / "#1"
        name = re.sub(r"^(#?\d+)[.)]?\s+", "", name).strip()
        duration = 0
        dm = _CONNECTED_RE.search(line)
        if dm:
            days = int(dm.group(1) or 0)
            hours = int(dm.group(2) or 0)
            mins = int(dm.group(3) or 0)
            duration = days * 86400 + hours * 3600 + mins * 60
        players.append({"name": name, "steam_id": steam_id, "duration": duration})
    return players


# ────────────────────────────────────────────
#  Сессия RCON
# ────────────────────────────────────────────

def _rcon_session(host: str, port: int, password: str) -> list[dict]:
    """Полная сессия: подключение → авторизация → playerlist → список игроков."""
    sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    try:
        sock.settimeout(RECV_TIMEOUT)
        _ws_handshake(sock, host, port)

        _send_json(sock, 1, password)
        auth = _recv_json(sock)
        if not auth or auth.get("Message") != "Auth":
            raise RconError("RCON: отказ в авторизации (неверный пароль?)")

        _send_json(sock, 2, "playerlist")
        resp = _recv_json(sock)
        message = (resp or {}).get("Message", "")
        return parse_playerlist(message)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def query_players_rcon(address: str) -> list | None:
    """Ники игроков через RCON для адреса 'ip:port'.

    Возвращает список игроков, [] если на сервере никого нет,
    None если RCON не настроен / недоступен / не для этого сервера.
    """
    if not config.RCON_PASSWORD or not config.RCON_SERVER:
        return None

    host, _, port_str = address.rpartition(":")
    if not host:
        return None

    cfg_host = config.RCON_SERVER.strip()
    if not cfg_host:
        return None
    cfg_port_part = ""
    if ":" in cfg_host:
        cfg_host, _, cfg_port_part = cfg_host.rpartition(":")
    if cfg_host.lower() != host.lower():
        return None
    if cfg_port_part.isdigit() and int(cfg_port_part) != int(port_str or 0):
        return None

    try:
        return _rcon_session(host, config.RCON_PORT, config.RCON_PASSWORD)
    except Exception as exc:  # сеть, таймаут, отказ — честный None
        logger.info("RCON к %s:%s не удался: %s", host, config.RCON_PORT, exc)
        return None