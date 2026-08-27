# 🎮 Rust Player Telegram Bot

Telegram-бот для проверки статистики игроков Rust: онлайн-статус, часы в игре, текущий сервер, история сессий и баны.

## Возможности

- `/profile <SteamID>` — полный профиль игрока
- `/servers <название>` — поиск серверов Rust
- Онлайн/оффлайн статус
- Часы в Rust
- Текущий сервер (если игрок онлайн)
- Последние посещённые серверы
- VAC / игровые баны
- Кнопка «Обновить» для актуализации данных

## Быстрый старт

### 1. Получи API-ключи (все бесплатно)

| Сервис | Где получить |
|--------|-------------|
| Telegram Bot Token | [@BotFather](https://t.me/BotFather) в Telegram |
| Steam Web API Key | [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) |
| BattleMetrics API Key | [battlemetrics.com/developers](https://battlemetrics.com/developers) |

### 2. Установи зависимости

```bash
pip install -r requirements.txt
```

### 3. Задай переменные окружения

**Windows (PowerShell):**
```powershell
$env:BOT_TOKEN="твой_токен"
$env:STEAM_API_KEY="твой_ключ"
$env:BM_API_KEY="твой_ключ"
```

**Linux / macOS:**
```bash
export BOT_TOKEN="твой_токен"
export STEAM_API_KEY="твой_ключ"
export BM_API_KEY="твой_ключ"
```

Или скопируй `.env.example` в `.env` и заполни.

### 4. Запусти

```bash
python bot.py
```

## Деплой 24/7 (Render.com)

1. Загрузи проект на GitHub
2. Зайди на [render.com](https://render.com) → New → Blueprint
3. Подключи репозиторий
4. В настройках сервиса добавь переменные окружения:
   - `BOT_TOKEN`
   - `STEAM_API_KEY`
   - `BM_API_KEY`
5. Бот запустится автоматически и будет работать 24/7

## Структура проекта

```
rust-telegram-bot/
├── bot.py            # Основной код бота
├── config.py         # Конфигурация (переменные окружения)
├── steam_api.py      # Работа с Steam Web API
├── bm_api.py         # Работа с BattleMetrics API
├── requirements.txt  # Зависимости
├── Dockerfile        # Для деплоя в Docker
├── render.yaml       # Конфигурация для Render.com
└── .env.example      # Пример файла окружения
```

## Лицензия

MIT
