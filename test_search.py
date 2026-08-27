import asyncio, sys, io
sys.path.insert(0, r"D:\rust-telegram-bot")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import steam_api

async def main():
    for nick in ["Roy", "Mi", "Игорь"]:
        players = await steam_api.search_players(nick, limit=5)
        print(f"--- «{nick}»: найдено {len(players)}")
        for p in players:
            print("   ", p.get("steam_id"), "|", p.get("name"), "|", p.get("country"))

asyncio.run(main())