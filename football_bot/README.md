# Football live-signal bot (7777.md)

Файлы:
- `sport_bot_v19.py` — основной бот: анализ live-матчей через API-Sports, сигналы в Telegram, авто-ставки (dry-run по умолчанию), команды в Telegram (жив?/когда?/итоги).
- `auto_bet_7777.py` — модуль авто-ставок на 7777.md через Selenium (подключается к уже открытому Chrome, `AUTO_BET_DRY_RUN = True` по умолчанию — ничего реально не кликает, пока не откалибровано).
- `inspect_7777.py` — разведочный скрипт (read-only): находит кнопки/рынки/поле ставки на странице, нужен для калибровки `auto_bet_7777.py`.
- `start_bot.sh` — запуск одной командой на Linux.
- `start_bot.ps1` — запуск одной командой на Windows.

## Переменные окружения (нужны перед запуском)

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
API_SPORTS_KEY_1=...
API_SPORTS_KEY_2=...   (опционально, второй ключ)
```

## Запуск

**Linux:**
```bash
chmod +x start_bot.sh
./start_bot.sh
```

**Windows (PowerShell):**
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # один раз, если ругается
.\start_bot.ps1
```

Оба скрипта: поднимают Chrome с отладочным портом 9222 (если ещё не открыт), ждут ручного логина на 7777.md, ставят недостающие пакеты и запускают бота с логом в `data/bot.log`.
