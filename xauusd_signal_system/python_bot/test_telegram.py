# -*- coding: utf-8 -*-
"""
Разовая проверка, что TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID в .env настроены
правильно — шлёт одно тестовое сообщение, не дожидаясь реального сигнала.

Запуск:
    python3 test_telegram.py
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram_notify import send_telegram

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

print(f"TELEGRAM_BOT_TOKEN задан: {bool(token)}")
print(f"TELEGRAM_CHAT_ID задан: {bool(chat_id)}")

ok = send_telegram(token, chat_id, "✅ Тестовое сообщение от XAUUSD signal bot — если видите это, Telegram настроен правильно.")
print("Отправлено успешно" if ok else "Отправка НЕ удалась — смотрите ошибку выше")
