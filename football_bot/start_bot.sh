#!/bin/bash
# start_bot.sh — ОДНА команда на Linux: поднимает Chrome с отладочным
# портом (если ещё не поднят) и запускает бота с логом в файл.
#
# ПЕРВЫЙ ЗАПУСК: откроется окно Chrome — залогинься на 7777.md руками,
#   потом нажми Enter в терминале. Дальше бот стартует сам.
# ПОСЛЕДУЮЩИЕ ЗАПУСКИ: если Chrome уже открыт на порту 9222 (и ты уже
#   залогинен) — скрипт это увидит и сразу запустит бота, ничего
#   спрашивать не будет.
#
# ЗАПУСК:
#   chmod +x start_bot.sh   (один раз)
#   ./start_bot.sh
set -e

CHROME_PORT=9222
PROFILE_DIR="$HOME/.selenium-chrome-7777"
BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Debian/Ubuntu (PEP 668) не даёт ставить пакеты в системный Python —
# если рядом есть venv (python3 -m venv venv), используем его питон,
# иначе обычный python3 (сработает, если пакеты стоят системно/через
# --break-system-packages).
if [ -x "$BOT_DIR/venv/bin/python3" ]; then
    PYBIN="$BOT_DIR/venv/bin/python3"
    echo "🐍 Использую venv: $PYBIN"
else
    PYBIN="python3"
fi

port_is_open() {
    (exec 3<>/dev/tcp/127.0.0.1/"$CHROME_PORT") 2>/dev/null
    local ok=$?
    exec 3<&- 2>/dev/null || true
    exec 3>&- 2>/dev/null || true
    return $ok
}

find_chrome() {
    for bin in google-chrome google-chrome-stable chromium-browser chromium; do
        if command -v "$bin" >/dev/null 2>&1; then
            echo "$bin"
            return 0
        fi
    done
    return 1
}

if port_is_open; then
    echo "✅ Chrome уже слушает порт $CHROME_PORT — пропускаю запуск браузера."
else
    CHROME_BIN=$(find_chrome) || {
        echo "❌ Не нашёл Chrome/Chromium. Установи: sudo apt install google-chrome-stable (или chromium)."
        exit 1
    }
    echo "🌐 Открываю $CHROME_BIN с отладочным портом $CHROME_PORT..."
    mkdir -p "$PROFILE_DIR"
    nohup "$CHROME_BIN" --remote-debugging-port=$CHROME_PORT --user-data-dir="$PROFILE_DIR" >/dev/null 2>&1 &
    disown
    sleep 5
    echo ""
    echo "⚠️  ЗАЛОГИНЬСЯ на 7777.md в открывшемся окне Chrome."
    echo "    Когда залогинишься — нажми Enter здесь, чтобы продолжить."
    read -r _
fi

echo ""
echo "🚀 Запускаю бота (лог: $BOT_DIR/data/bot.log)..."
mkdir -p "$BOT_DIR/data"
cd "$BOT_DIR"
"$PYBIN" -u sport_bot_v19.py 2>&1 | tee -a data/bot.log
