import asyncio
import os
import aiohttp

THEODDS_API_KEY = os.getenv("THEODDS_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

async def fetch_live_matches():
    # Заглушка: здесь будет запрос к API для получения матчей
    await asyncio.sleep(1)
    return [
        {
            "sport": "basketball",
            "teams": ["Team A", "Team B"],
            "score": "80-75",
            "time_remaining": 6,  # минут
            "odds": 1.15
        }
    ]

async def notify_bot(bot):
    from aiogram.types import ParseMode
    while True:
        matches = await fetch_live_matches()
        for match in matches:
            if 5 <= match["time_remaining"] <= 8 and match["odds"] >= 1.1:
                msg = (f"Матч: {match['sport'].capitalize()}
"
                       f"Команды: {match['teams'][0]} vs {match['teams'][1]}
"
                       f"Счёт: {match['score']}
"
                       f"Осталось минут: {match['time_remaining']}
"
                       f"Коэффициент: {match['odds']}")
                await bot.send_message(ADMIN_ID, msg, parse_mode=ParseMode.HTML)
        await asyncio.sleep(120)  # опрос каждые 2 минуты
