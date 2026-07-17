# sport_bot_v19.py — ИНТЕГРАЦИЯ АВТО-СТАВКИ (dry-run) + KEEP-ALIVE
# ═══════════════════════════════════════════════════════════════
# ЧТО ИЗМЕНЕНО ПО СРАВНЕНИЮ С v9 (по результатам аудита):
#
# BUG-FIX 1 [КРИТИЧНО] check_finished_bets() был недостижим,
#   когда signals_today >= MAX_SIGNALS_PER_DAY или план пуст —
#   раньше main() делал `continue` до того, как доходил до кода,
#   который проверяет результаты ставок. Итог: часть ставок за
#   "богатый на сигналы" день никогда не получала WIN/LOSE в
#   Telegram и не попадала в CSV.
#   FIX: проверка результатов вынесена в начало цикла (как
#   heartbeat) и выполняется НЕЗАВИСИМО от лимита сигналов и
#   наличия плана.
#
# BUG-FIX 2 [СРЕДНЕ] validate_api_keys() тратил 1-2 запроса на
#   /status при каждом рестарте бота, но не увеличивал счётчики
#   api_calls_key1/2 → внутренний учёт квоты расходился с
#   реальным потреблением на стороне API-Sports.
#   FIX: /status теперь тоже считается в state.
#
# BUG-FIX 3 [СРЕДНЕ] Проверка результатов была ограничена 1
#   API-вызовом за цикл + не чаще раза в 15 мин → в вечер с
#   несколькими одновременно завершающимися матчами результаты
#   могли растягиваться на 1-1.5 часа.
#   FIX: лимит поднят до RESULT_CHECKS_PER_CYCLE (по умолчанию 5),
#   троттлинг на ставку (15 мин) сохранён — бюджет по-прежнему
#   защищён, потому что количество "зревших" ставок ограничено
#   MAX_SIGNALS_PER_DAY.
#
# BUG-FIX 4 [ВАЖНО, но это не "баг", а признание] Проценты вида
#   "~82%", "~88%" в старой версии были придуманы, а не измерены.
#   FIX: добавлена historical_win_rate() — считает РЕАЛЬНЫЙ win
#   rate по каждому типу ставки из signals.csv и показывает его
#   в сообщении, если накопилось достаточно данных. Если данных
#   мало — сообщение честно помечено как "эвристика, не проверено".
#
# BUG-FIX 5 [МЕЛКО] Сообщение всегда советовало "коэф 1.20-1.45"
#   независимо от типа ставки — на разгромных счетах (напр. 4-0)
#   бот предлагал UNDER, который у букмекера будет стоить 1.02-1.08,
#   т.е. без ценности. FIX: статичный совет убран, вместо него —
#   предупреждение о низкой ценности для "почти гарантированных"
#   линий + реальный win rate вместо мифического кэфа.
#
# BUG-FIX 6 [МЕЛКО] CSV не хранил исход доп. ставок (extras),
#   поэтому нельзя было посчитать historical_win_rate для них.
#   FIX: добавлена колонка extras_result.
#
# SECURITY: ключи вынесены в переменные окружения (os.getenv) —
#   секреты, зашитые прямо в файле, который вы кому-то показали
#   или загрузили, нужно считать скомпрометированными и
#   перевыпустить (новый ключ API-Sports + /revoke и /newtoken
#   в @BotFather для Telegram-токена). Если переменные окружения
#   не заданы, используются значения по умолчанию ниже — но их
#   надо заменить на новые.
#
# NEW: интеграция с Claude API (Anthropic) — ai_confirm_signal().
#   Перед отправкой сигнала бот опционально спрашивает у Claude
#   вероятность захода конкретной ставки по структурированному
#   контексту матча и либо гасит слабый сигнал (AI_CONFIRM_MODE =
#   "gate"), либо просто добавляет мнение ИИ в сообщение
#   (AI_CONFIRM_MODE = "advisory"). Также добавлен ai_daily_review()
#   — раз в сутки Claude анализирует signals.csv и присылает
#   краткий разбор эффективности в Telegram.
#
#   ВАЖНО: у Claude нет доступа к живым спортивным данным сверх
#   того, что вы ему передаёте в промпте, и он не может "обыграть"
#   букмекера сам по себе. Это дополнительный фильтр поверх
#   эвристики и способ автоматизировать разбор статистики -
#   не замена реальному бэктесту.
#
# ═══════════════════════════════════════════════════════════════
# v11 — ВЫВОДЫ ИЗ РЕАЛЬНОЙ ИСТОРИИ (разобрал экспорт Telegram-чата
# бота за 06.04–12.07, 173 завершённые ставки):
#
# DATA-FIX 7 [КРУПНОЕ] Реальный win rate по типам ставки за 3+
#   месяца: Двойной шанс (DC) 49W/1L = 98.0%, BTTS_NO 13W/2L =
#   86.7%, Тотал UNDER 40W/29L = 58.0%. DC вдвое надёжнее тотала.
#   FIX: в pick_signal_bundle() ветка "разрыв ≥2 гола" раньше
#   всегда выбирала TOTAL_UNDER как основную ставку — теперь,
#   как и при разрыве в 1 гол, основной становится DC на лидера,
#   а TOTAL_UNDER остаётся дополнительной (страховочной) ставкой.
#
# DATA-FIX 8 [ИСПРАВЛЕНО В v12] Раньше тут было предположение, что
#   "Тотал матча" закрывается у букмекера на 78-86 минуте раньше
#   остальных рынков — так казалось по 11 логам "Авто-ставка не
#   удалась" (все они были именно на Тотале, на 78-79').
#   ⚠️ ЭТО ОПРОВЕРГНУТО ЖИВЫМ ЗАМЕРОМ (см. DATA-FIX 10 ниже):
#   реально понаблюдали за 7777.md во время матчей — Тотал живёт
#   с реальными коэффициентами наравне со всеми рынками почти до
#   90-й минуты, никакого раннего закрытия именно у Тотала нет.
#   Старые сбои авто-ставки вероятнее объясняются тем, что к
#   моменту клика конкретная цена/линия уже сдвинулась (кэфы и
#   линии меняются постоянно, это тоже видно по живым замерам) —
#   а не тем, что рынок был закрыт. Раньше был AUTO_BET_SKIP_MARKETS,
#   пропускавший авто-ставку на Тотал — убрано, т.к. основано на
#   неверной предпосылке.
#
# DATA-FIX 10 [НОВОЕ v12] Живой замер 7777.md (Selenium-монитор,
#   реальный матч, минута считалась от кикоффа): список рынков
#   стабилен и все линии активны с 35' и минимум до ~85-87' игрового
#   времени. Весь блок ставок разом пропадает только у самого конца
#   (в нашем замере — около 108-109 мин от кикоффа, это где-то
#   90'+добавленное время, с учётом 15-мин перерыва). До этого —
#   никакой частичной блокировки отдельных рынков не увидели.
#   FIX: сигнальное окно расширено — раньше бот пропускал матчи,
#   где решающий момент наступал после 75' (сигнал просто не
#   успевал уйти). Теперь MAX_MINUTE=85 — сигналы будут ловиться
#   на 10 минут больше матчей, с запасом в 3-5 мин до реального
#   исчезновения рынков. MIN_MINUTE не трогали (70' — validated
#   окно с 86.5% win rate, менять без данных не стал).
#   ЦЕНА ВОПРОСА: более широкое окно = больше live-опросов на матч
#   (POLL_SECONDS_ACTIVE слегка увеличен, 90→110с, чтобы не съесть
#   вдвое больше API-бюджета на каждый матч).
#
# DATA-FIX 9 [ВАЖНО] С одним API-ключом (сейчас у вас так) бюджет
#   100 запросов/день физически не покрывает список из 55+ лиг —
#   по логам ключ почти каждый день висит на 95-100/100 по многу
#   часов при плане в 48-57 матчей/день, то есть бот не успевает
#   опросить большинство активных окон. FIX: список лиг по
#   умолчанию сужен до Топ-5 + еврокубки (это меньше отдельных
#   "окон" в сутках → бюджета хватает опрашивать их все). Полный
#   список оставлен ниже закомментированным — раскомментируй,
#   если добавишь второй ключ (см. APISPORTS_KEY_2).
# ═══════════════════════════════════════════════════════════════
#
# ═══════════════════════════════════════════════════════════════
# v13 — ПО ЗАПРОСУ: тихий Telegram + команды боту + защита от сбоев.
# Прошёл код пятью отдельными проходами: (1) бюджет/корректность
# API-запросов, (2) логика сигналов, (3) Telegram-сообщения на шум,
# (4) новая функциональность команд, (5) сохранность state между
# рестартами. Результаты по каждому проходу:
#
# ПРОХОД 1 (API-бюджет): пересчитал — счётчики и троттлинг из v10-12
# по-прежнему корректны, новые Telegram-команды используют отдельный
# API (Telegram getUpdates), НЕ трогают счётчик api_calls_key1/2 и
# бюджет 100 запросов/день API-Sports вообще никак не расходуют.
#
# ПРОХОД 2 (сигналы): pick_signal_bundle/eval_bet не менялись,
# перепроверил формулы win/lose — ошибок не нашёл (см. v10/v11 аудит).
#
# ПРОХОД 3 (шум в Telegram) — ОСНОВНОЕ ИЗМЕНЕНИЕ v13:
#   BUG-FIX 11: HEARTBEAT_ENABLED теперь False по умолчанию — бот
#   больше не шлёт "я жив" раз в час. В Telegram теперь идут только:
#   сигнал на ставку → результат (зашла/нет) → и один раз в начале
#   дня "план: N матчей", один раз в конце дня "итоги: N сигналов,
#   W побед, L поражений". "Статус за весь день" теперь по запросу
#   (см. проход 4), а не спамом.
#   BUG-FIX 12: send_plan_to_telegram() раньше слал список до 25
#   матчей построчно — теперь одна строка с количеством ("сколько
#   команд", как просили), без простыни текста.
#   NEW: send_daily_summary() — по смене даты в reset_daily_if_needed()
#   шлёт итоги ПРОШЕДШЕГО дня (сигналов/побед/поражений/win rate) один раз.
#
# ПРОХОД 4 (команды в Telegram) — NEW:
#   Бот теперь слушает входящие сообщения (Telegram getUpdates
#   long-polling, отдельный от API-Sports бюджет). Можно написать
#   боту своими словами:
#     "жив?" / "статус"        → cmd_status_text()  — жив, режим, план,
#                                 сигналов сегодня, API-бюджет, следующий матч
#     "когда игра?" / "когда"  → cmd_next_text()     — через сколько
#                                 следующее окно сигнала (по нашим запросам)
#     "итоги" / "сколько"      → cmd_stats_text()    — счёт побед/поражений
#                                 за сегодня прямо сейчас (не дожидаясь конца дня)
#     "помощь"                 → список команд
#   Реализовано через smart_sleep() — заменяет time.sleep() везде в
#   main(): вместо обычного сна бот каждые ~20с проверяет, не написали
#   ли ему, и тут же отвечает — даже если "спит" часами между матчами.
#
# ПРОХОД 5 (сохранность state / "не тупить после рестарта"):
#   BUG-FIX 13 [найдено при аудите]: save_state() писала state.json
#   напрямую (open+write). Если процесс упадёт/обесточится РОВНО в
#   момент записи — файл может остаться битым (обрезанным), и при
#   следующем запуске load_state() тихо откатится на пустой state,
#   потеряв ВСЮ историю открытых ставок и план. FIX: атомарная запись
#   (пишем во временный файл, затем os.replace — атомарно и на
#   Windows, и на Linux; либо файл полностью новый, либо остаётся
#   старый валидный, третьего не дано).
#   NEW: last_alive_ts — отметка времени обновляется на каждой
#   проверке Telegram-команд (примерно раз в 20с). При старте бот
#   сравнивает её с текущим временем и, если разрыв заметный,
#   отдельно пишет "не отвечал примерно N минут" — независимо от
#   того, был ли на сегодня план (раньше отчёт о простое зависел от
#   startup_recovery(), который молчал, если рестарт пришёлся на
#   смену дня).
#   NEW: last_error — последняя ошибка перед сном/падением сохраняется
#   в state и показывается в отчёте после рестарта.
#   NEW: если ошибки идут подряд (нет интернета, ключ умер и т.п.) —
#   после 3 подряд шлём ОДНО предупреждение в Telegram (не спамим на
#   каждую), и отдельное "восстановился", когда цикл снова пройдёт
#   без ошибок.
# ═══════════════════════════════════════════════════════════════
#
# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# v19 — ИНТЕГРАЦИЯ АВТО-СТАВКИ (auto_bet_7777.py, dry-run) + KEEP-ALIVE
#
# ⚠️ AUTO_BET_MODULE_DRY_RUN=True по умолчанию в auto_bet_7777.py —
#   реальные ставки НЕ выставляются, пока не откалибровано (см. шапку
#   auto_bet_7777.py — нужен discover.json + подтверждённый keepalive
#   тест). Это НЕ заглушка "для галочки" — вся логика поиска матча/
#   рынка/статуса работает по-настоящему, просто финальный клик по
#   кнопке подтверждения купона не происходит, пока не проверено вживую.
#
# ЧТО СДЕЛАНО ПО ЗАПРОСУ ПОЛЬЗОВАТЕЛЯ:
#   1. Детальные статусы авто-ставки в Telegram (не просто bool
#      "удалось/не удалось", как было): PLACED / DRY_RUN /
#      NOT_LOGGED_IN / MATCH_NOT_FOUND / NOT_FOUND / BLOCKED / FAILED.
#      См. describe_auto_bet_result().
#   2. Расчёт и показ реального выигрыша в лей при закрытии ставки
#      (auto_bet_stake × auto_bet_odds), не просто WIN/LOSE.
#   3. Фоновый keep-alive-поток (см. auto_bet_7777.KeepAliveThread) —
#      запускается один раз при старте бота (если AUTO_BET_ENABLED) и
#      крутится непрерывно НЕЗАВИСИМО от сигналов — отвечает на вопрос
#      "что сделано против авто-разлогина сайта". Шлёт в Telegram
#      громкое предупреждение при выходе из профиля и подтверждение
#      при повторном логине — само не логинится (пароль не трогает).
#   4. signals.csv расширен колонками auto_bet_status/stake/odds/payout
#      — для последующего анализа истории ставок (просили логи).
#
# 5-ПРОХОДНОЙ АУДИТ (sport_bot + auto_bet_7777) — что нашли и починили:
#   ПРОХОД 1 (безопасность кликов): place_bet_from_signal в DRY_RUN
#     не должен НИКОГДА кликать элементы, которые тратят деньги —
#     написал тест с "миной" (click() бросает AssertionError на
#     money-risk элементах) — прогнал, сработал: подтверждено, что
#     dry-run не кликает исход/подтверждение, только читает.
#   ПРОХОД 2 (сопоставление команд) [НАЙДЕН И ИСПРАВЛЕН БАГ]:
#     наивное сравнение строк (SequenceMatcher по всей строке) давало
#     "Manchester United" ~ "Manchester City" = 0.81 схожести —
#     реальный риск поставить на ДРУГОЙ матч с похожим названием одной
#     команды. Переписано на сравнение по словам + MIN(совпадение
#     хозяев, гостей) вместо среднего — тест подтвердил исправление.
#   ПРОХОД 3 (нормализация имён) [НАЙДЕН И ИСПРАВЛЕН БАГ]: дефис в
#     "Ararat-Armenia" удалялся, а не заменялся на пробел — команда
#     "слипалась" в одно слово и переставала совпадать с "Ararat
#     Armenia" от биржи. Исправлено, тест подтвердил.
#   ПРОХОД 4 (статусы/логи): все ветки (не залогинен/матч не найден/
#     рынок не найден/заблокирован/ошибка) — свои сообщения и в лог
#     (data/auto_bet.log), и в Telegram, ничего не проглатывается молча.
#   ПРОХОД 5 (сигналка sport_bot): pick_signal_bundle/eval_bet не
#     менялись с прошлого аудита, повторно прогнаны тесты v18 — без
#     регрессий, DC-стратегия идентична тому, что уже работает.
#
# ЧТО ОСТАЁТСЯ НЕОТКАЛИБРОВАННЫМ (см. TODO-CALIBRATE в auto_bet_7777.py):
#   - точный клик по карточке матча в списке live (сейчас — общий
#     текстовый поиск, не проверенный селектор),
#   - точный клик по кнопке исхода внутри рынка (аналогично),
#   - поле суммы и кнопка подтверждения в купоне (наименее проверено).
#   Присылай discover.json/discover.txt и результат keepalive-теста —
#   заменю эвристики на точные селекторы и включим реальные ставки.
# ═══════════════════════════════════════════════════════════════
#
# v14 — по вопросу пользователя "план пустой, а если матчи появятся
# позже?" нашлись две вещи:
#
# BUG-FIX 14 [НАЙДЕНО ПО ВОПРОСУ ПОЛЬЗОВАТЕЛЯ, важно] Если план
#   выходил пустым, main() пересобирал его КАЖДЫЕ 10 МИНУТ весь
#   день без ограничений (условие было "план не сегодняшний ИЛИ
#   план пуст" — второе верно постоянно, пока матчей нет). В обычный
#   день это ловит фикстуры, добавленные в API с опозданием — не
#   баг. Но если матчей нет вообще (например межсезонье) — бот мог
#   сжечь весь дневной лимit API ещё до вечера, пытаясь найти то,
#   чего не существует, и остаться без бюджета, если матч всё же
#   появится позже.
#   FIX (v14): пересборка при пустом плане ограничена по частоте.
#   ОБНОВЛЕНО В v16 (BUG-FIX 17): заменено на единый механизм
#   PLAN_REFRESH_* — план дозагружается в течение дня и в непустом
#   состоянии тоже, см. блок BUG-FIX 17 ниже.
#
# DATA-FIX 15 [сезонность] Топ-5 + еврокубки (сужено в v11 под
#   бюджет одного ключа) практически не играют в июле — межсезонье.
#   Добавлены 2 круглогодичные лиги с большим количеством матчей —
#   MLS (253) и Brasileirão Série A (71) — умеренная добавка к
#   бюджету (было 9 ID лиг, из которых летом реально активны ~0-2,
#   стало 11, из которых летом активны все ~2 новые + возможные
#   ранние квалификации еврокубков). Если жалко бюджета — просто
#   убери эти два ID из LEAGUE_IDS ниже, строка помечена.
# ═══════════════════════════════════════════════════════════════

import time
import json
import os
import re
import csv
import requests
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple

try:
    from auto_bet_7777 import (
        place_bet_from_signal, close_driver,
        start_keepalive, stop_keepalive,
        AUTO_BET_DRY_RUN as AUTO_BET_MODULE_DRY_RUN,
    )
    AUTO_BET_AVAILABLE = True
except ImportError:
    AUTO_BET_AVAILABLE = False
    AUTO_BET_MODULE_DRY_RUN = True
    # v19: place_bet_from_signal теперь возвращает СЛОВАРЬ статуса
    # (было bool) — см. auto_bet_7777.py. Заглушка повторяет форму.
    def place_bet_from_signal(_):
        return {"status": "ERROR", "detail": "auto_bet_7777 не установлен",
                "stake": 0.0, "odds": None}
    def close_driver(): pass
    def start_keepalive(): pass
    def stop_keepalive(): pass

try:
    from anthropic import Anthropic
    ANTHROPIC_SDK_AVAILABLE = True
except ImportError:
    ANTHROPIC_SDK_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════
# НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────
# API КЛЮЧИ — берём из переменных окружения, с fallback на
# значения по умолчанию. ПЕРЕВЫПУСТИ ключи ниже, если этот файл
# когда-либо покидал твою машину (см. блок SECURITY выше)!
# ─────────────────────────────────────────────────────────────
APISPORTS_KEY   = os.getenv("APISPORTS_KEY",   "77c9fe89613e25221d8b2a765faf09a6")
APISPORTS_KEY_2 = os.getenv("APISPORTS_KEY_2", "")

def _is_valid_key(k: str) -> bool:
    """Проверяем что ключ выглядит как настоящий (32 hex-символа)."""
    if not k or len(k) < 20:
        return False
    if any(w in k.lower() for w in ["вставь", "insert", "your_key", "xxx", "ключ"]):
        return False
    return True

# Собираем список только из рабочих ключей
API_KEYS: list = [k for k in [APISPORTS_KEY, APISPORTS_KEY_2] if _is_valid_key(k)]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7743296740:AAFAV5MskW3hFdYDgobWw56nSa8OpXFxav0")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "793477119")

BASE_URL = "https://v3.football.api-sports.io"
TIMEZONE = "Europe/Chisinau"

# ───────── СТРATEГИЯ ─────────────────────────────────────────
MIN_MINUTE            = 70   # оставлено как есть — 86.5% win rate на этом окне, не трогаем без данных
# DATA-FIX 10: было 75, живой замер показал что рынки живы до ~85-87' —
# расширили, чтобы ловить сигналы по матчам, где решающий момент
# наступает позже 75-й минуты (раньше такие матчи просто пропускались).
MAX_MINUTE            = 85
MAX_SIGNALS_PER_MATCH = 1
MAX_SIGNALS_PER_DAY   = 25

# DATA-FIX 19 (v18): переключатель стратегии одной строкой.
#   "DC"    — двойной шанс/BTTS на лидера (как сейчас у тебя работает,
#             логи 13-16.07 показали 18/18 верных прогнозов). Но: при
#             разрыве ≥2 гола этот рынок на бирже часто ЗАБЛОКИРОВАН 🔒
#             или даёт ~1.01 — см. предупреждение в _pick_signal_bundle_dc.
#   "TOTAL" — Тотал "больше не забьют" (v17), рынок на бирже открыт,
#             реальный кэф ~1.8-2.3, но win rate ниже (~58-60%).
SIGNAL_STRATEGY = "DC"   # ← поменяй на "TOTAL", когда решишь перейти

# DATA-FIX 18 (v17): минимальный коэффициент, при котором ставку
# имеет смысл делать. Бот НЕ знает точный кэф (нет /odds API), поэтому
# это правило для ТЕБЯ: если фактический коэф у букмекера ниже —
# пропусти, там нет ценности (см. анализ блокировок биржи).
# Для нашей главной ставки (TOTAL_UNDER, win rate ~58%) безубыток
# при кэфе 1/0.58 = 1.72, берём с запасом.
MIN_ODDS = 1.75

# ───────── АВТО-СТАВКА ───────────────────────────────────────
AUTO_BET_ENABLED = False
# DATA-FIX 8 (v12): AUTO_BET_SKIP_MARKETS убран — раньше здесь
# был список рынков, на которые авто-ставка не пыталась ставить
# (предполагая что Тотал закрывается раньше остальных). Живой
# замер это не подтвердил, см. change-log выше. Если когда-нибудь
# включите авто-ставку — она работает одинаково для всех типов.

# ───────── ТАЙМИНГИ ──────────────────────────────────────────
ACTIVE_FROM_MIN       = 68
# DATA-FIX 10: было 78, расширено под новый MAX_MINUTE=85 + запас.
ACTIVE_TO_MIN         = 90

# DATA-FIX 10: было 90, слегка увеличено — окно активности выросло
# вдвое (68-90 вместо 68-78), без этой правки API-бюджет тратился
# бы почти в 2 раза быстрее на каждый матч.
POLL_SECONDS_ACTIVE   = 110
SLEEP_CHUNK_SECONDS   = 600

# DATA-FIX 10: было 105, живой замер показал что матч реально
# заканчивается позже (~108-109 мин от кикоффа с учётом перерыва) —
# подняли, чтобы первая проверка результата чаще попадала в FT
# с первого раза, а не тратила лишний API-вызов на "ещё не закончился".
MATCH_RESULT_CHECK_AFTER_MIN = 110
RESULT_RETRY_INTERVAL_MIN    = 15
# FIX3: было "максимум 1 проверка за вызов" — теперь до 5 за вызов.
# Троттлинг конкретной ставки (15 мин) сохранён ниже в коде, так
# что дневной бюджет по-прежнему под контролем.
RESULT_CHECKS_PER_CYCLE      = 5

# ───────── API БЮДЖЕТ (100 запросов/день) ────────────────────
API_DAILY_LIMIT = 100
def get_total_limit() -> int:
    return API_DAILY_LIMIT * max(1, len(API_KEYS))
API_SOFT_LIMIT  = 95

# ───────── HEARTBEAT ─────────────────────────────────────────
# BUG-FIX 11: было True — слал "я жив" в Telegram каждый час весь день.
# Теперь по умолчанию выключено: в Telegram идут только сигнал/результат/
# план на день/итоги дня. Статус "жив ли бот" — теперь по запросу,
# см. TELEGRAM_COMMANDS_ENABLED ниже. Если всё же хочешь автосводку
# по расписанию — верни True.
HEARTBEAT_ENABLED          = False
HEARTBEAT_INTERVAL_SECONDS = 3600

# ───────── КОМАНДЫ В TELEGRAM (NEW v13) ───────────────────────
# Позволяет спросить бота прямо в чате: "жив?", "когда игра?", "итоги".
# Использует Telegram getUpdates (long-polling) — НЕ трогает бюджет
# API-Sports (100 запросов/день), это полностью отдельный лимит Telegram.
TELEGRAM_COMMANDS_ENABLED = True
# Как часто (максимум, секунд) бот проверяет новые сообщения во время
# "сна" между матчами — каждый обычный time.sleep() в коде заменён на
# smart_sleep(), который спит частями по TELEGRAM_POLL_CHUNK_SEC и
# между частями слушает Telegram, поэтому ответ на команду приходит
# в течение примерно этого времени, даже если бот "спит" часами.
TELEGRAM_POLL_CHUNK_SEC = 20
# После скольких ошибок подряд слать одно предупреждение в Telegram
# (не на каждую ошибку — иначе спам при долгом сбое сети/API).
ERROR_ALERT_THRESHOLD = 3
# Порог (минут) простоя, начиная с которого при рестарте отдельно
# пишем "не отвечал примерно N минут" — короткие обычные рестарты
# ботом не считаются поводом для алерта.
DOWNTIME_ALERT_THRESHOLD_MIN = 5

# ───────── ОБНОВЛЕНИЕ ПЛАНА В ТЕЧЕНИЕ ДНЯ (v16, BUG-FIX 17) ──────
# Раньше (v14) план пересобирался только в новый день ИЛИ пока он
# пустой. Если план стал НЕпустым (собрал N матчей утром), он больше
# не обновлялся до полуночи — а матчи, которые API добавляет позже
# (MLS США стартует ночью по UTC, дозагрузка расписания и т.п.), в
# план уже не попадали за весь день → пропущенные сигналы.
#
# v16: единый механизм. План пересобирается когда:
#   - новый день (всегда), ИЛИ
#   - прошло >= PLAN_REFRESH_INTERVAL_SEC с прошлой сборки И
#     сборок за сегодня < PLAN_REFRESH_MAX_PER_DAY.
# Работает и для пустого плана (ловит матчи, добавленные позже), и
# для непустого (дозагружает новые матчи дня). Полная пересборка
# безопасна: sent_per_match и open_bets хранятся отдельно, поэтому
# уже отправленные сигналы НЕ повторяются, а сыгравшие матчи просто
# отфильтровываются в build_24h_plan.
# Бюджет: максимум (1 + MAX_PER_DAY) сборок/день × 2 запроса.
PLAN_REFRESH_INTERVAL_SEC  = 4 * 3600   # обновлять не чаще раза в 4 часа
PLAN_REFRESH_MAX_PER_DAY   = 4          # + до 4 обновлений сверх утренней сборки

# ───────── CLAUDE AI (Anthropic) ──────────────────────────────
# Ключ Claude — ТОЛЬКО через переменную окружения, никогда в коде:
#   export ANTHROPIC_API_KEY="sk-ant-..."
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
AI_CONFIRM_ENABLED  = True            # выключатель всей ИИ-фичи одним флагом
AI_MODEL            = "claude-sonnet-5"
# "gate"     — слабые по мнению ИИ сигналы НЕ отправляются
# "advisory" — сигнал отправляется всегда, мнение ИИ просто добавляется в текст
AI_CONFIRM_MODE      = "gate"
# DATA-FIX 19 (v18): порог ИИ-гейта теперь зависит от SIGNAL_STRATEGY —
# у DC (win rate ~98%) и TOTAL (win rate ~58%) разная "нормальная"
# уверенность, единый порог 0.62 зарубил бы почти все TOTAL-сигналы,
# а единый 0.50 пропускал бы слишком много слабых DC-сигналов. Так
# при переключении SIGNAL_STRATEGY порог подстраивается сам — менять
# отдельно ничего не нужно.
_AI_MIN_PROB_BY_STRATEGY = {"DC": 0.62, "TOTAL": 0.50}
AI_MIN_PROBABILITY = _AI_MIN_PROB_BY_STRATEGY.get(SIGNAL_STRATEGY, 0.60)
AI_MAX_CALLS_PER_DAY = 40             # защита от неожиданных расходов
AI_TIMEOUT_SECONDS   = 20
AI_DAILY_REVIEW_ENABLED = True        # раз в сутки — разбор signals.csv от Claude

_anthropic_client = None
if AI_CONFIRM_ENABLED and ANTHROPIC_SDK_AVAILABLE and ANTHROPIC_API_KEY:
    _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
ANTHROPIC_AVAILABLE = _anthropic_client is not None

# ═══════════════════════════════════════════════════════════════
# ЛИГИ — DATA-FIX 16 (v15): список собран ПОД ТВОЮ БИРЖУ 7777.md
# (по скриншотам разделов) И под лето — чтобы план не был пустым в
# межсезонье Европы, а матчи из плана реально были на бирже.
#
# Механика матчинга: is_target_competition() матчит лигу, если её
# ID есть в LEAGUE_IDS ИЛИ её название содержит слово из
# LEAGUE_NAME_KEYWORDS. Название — надёжная подстраховка: даже если
# я ошибся в ID, лига всё равно поймается по имени (имена в API
# стабильны, ты их видишь на бирже).
#
# ⚠️ ID части лиг ниже я указал по памяти — если какая-то лига НЕ
# появляется в плане, а на бирже она есть: напиши мне её точное имя
# как его отдаёт API (видно в логах строкой "📋 Лиги: [...]"),
# добавлю по имени — это 100% сработает.
#
# БЮДЖЕТ: 1 live-опрос = 1 запрос и возвращает ВСЕ live-матчи мира
# сразу, так что число лиг само по себе бюджет НЕ множит. Множит
# его разброс матчей по часовым поясам (много непересекающихся
# окон = много серий опросов). Летний набор ниже разбросан по
# зонам (Америка/Азия/Скандинавия) — на насыщенный день ключа 100
# может не хватить. Если увидишь, что ключ рано упирается в 100/100
# — убери Азию (Китай 169) или Скандинавию, строки помечены.
# ═══════════════════════════════════════════════════════════════
LEAGUE_IDS = {
    # ── ЛЕТО + КРУГЛЫЙ ГОД, ЕСТЬ НА 7777.md (главные для июля) ──
    71,   # Brasileirão Série A (Бразилия 7 на бирже)
    72,   # Brasileirão Série B
    128,  # Liga Profesional (Аргентина 9 LIVE)
    253,  # MLS (США 4)
    235,  # Premier League (Россия 4 LIVE)
    262,  # Liga MX (Мексика 1)
    169,  # Chinese Super League (Китай 4)     ← убери, если жалко бюджета (Азия)
    # ── СКАНДИНАВСКИЕ ЛЕТНИЕ ЛИГИ (есть на бирже) ──  ← убери всю группу при нехватке бюджета
    113,  # Allsvenskan (Швеция 3)
    103,  # Eliteserien (Норвегия 2)
    244,  # Veikkausliiga (Финляндия 3)
    # ── ЮЖНОАМЕРИКАНСКИЕ КУБКИ (Международные Клубы на бирже) ──
    13,   # Copa Libertadores
    11,   # Copa Sudamericana
    # ── ЕВРОПА: заработают в августе, когда стартует сезон ──
    39, 140, 135, 78, 61,   # Топ-5: АПЛ, Ла Лига, Серия A, Бундеслига, Лига 1
    2, 3, 848, 5, 531,      # Еврокубки: ЛЧ, ЛЕ, ЛК, Nations League, Суперкубок
}

# Подстраховка по названию (ловит лигу, даже если ID выше неверный).
# Держим ТОЛЬКО отличительные имена, чтобы не тащить лишнее:
LEAGUE_NAME_KEYWORDS = [
    # Топ-5 + еврокубки (для августа)
    "la liga", "bundesliga", "ligue 1",
    "champions league", "europa league", "conference league", "nations league",
    # Летние/круглогодичные — надёжная подстраховка к ID выше
    "brasileir",            # Бразилия A/B
    "liga profesional",     # Аргентина
    "mls", "major league soccer",   # США
    "liga mx",              # Мексика
    "allsvenskan", "eliteserien", "veikkausliiga",   # Скандинавия
    "chinese super",        # Китай
    "libertadores", "sudamericana",   # ЮА кубки
    # ВНИМАНИЕ: "la liga" заодно ловит "Copa De La Liga" (Перу/Чили) —
    # это те матчи, что уже были у тебя на бирже, так что оставляем.
]
# Намеренно НЕ включены "premier league" и "serie a" как ключевые
# слова — иначе цеплялись бы Бутан/Египет/Уэльс/Италия-дубли и жгли
# бюджет на матчи вне биржи. Англия ловится по ID 39, Россия по 235,
# Италия по 135, Бразилия-Серия по 71/72 и слову "brasileir".

# ═══════════════════════════════════════════════════════════════
# ФАЙЛЫ
# ═══════════════════════════════════════════════════════════════
DATA_DIR   = "data"
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
CSV_FILE   = os.path.join(DATA_DIR, "signals.csv")


# ═══════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════

def now_dt() -> datetime:
    return datetime.now().astimezone()

def now_ts() -> float:
    return time.time()

def now_str() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")

def today_str() -> str:
    return now_dt().strftime("%Y-%m-%d")

def utc_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def safe_int(x, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def safe_log(msg: str) -> None:
    print(f"[{now_str()}] {msg}", flush=True)

def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ═══════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════

def _empty_state() -> Dict[str, Any]:
    return {
        "sent_per_match":      {},
        "open_bets":           {},
        "signals_today":       0,
        "signals_today_date":  today_str(),
        "plan_date":           "",
        "plan":                [],
        "plan_retries_today":  0,
        "api_calls_key1":      0,
        "api_calls_key1_date": utc_date_str(),
        "api_calls_key2":      0,
        "api_calls_key2_date": utc_date_str(),
        "active_key_index":    0,
        "_notified_switch_to": -1,
        # NEW: учёт вызовов Claude API и дата последнего дневного разбора
        "ai_calls_today":      0,
        "ai_calls_date":       today_str(),
        "ai_review_date":      "",
        # NEW v13: Telegram-команды, живучесть, итоги дня
        "telegram_last_update_id": 0,
        "last_alive_ts":       0.0,
        "last_error":          "",
        "consecutive_error_count": 0,
        "error_alert_active":  False,
        "daily_summary_date":  "",
        # v16: обновление плана в течение дня (BUG-FIX 17)
        "plan_refreshes_today":  0,
        "plan_last_build_ts":    0.0,
    }

def load_state() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, val in _empty_state().items():
                data.setdefault(key, val)
            return data
        except Exception as e:
            safe_log(f"⚠️ state.json: {e} — создаём новый.")
    return _empty_state()

def save_state(state: Dict[str, Any]) -> None:
    """BUG-FIX 13: атомарная запись — пишем во временный файл и
    заменяем оригинал через os.replace (атомарно на Windows и Linux).
    Если процесс упадёт/обесточится ровно во время записи — старый
    state.json останется целым, вместо того чтобы обрезаться и
    заставить бота "забыть" всю историю при следующем запуске."""
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, STATE_FILE)

def reset_daily_if_needed(state: Dict[str, Any]) -> None:
    today   = today_str()
    changed = False

    # NEW v13: итоги ПРОШЕДШЕГО дня — один раз, до сброса счётчика.
    prev_date = state.get("signals_today_date")
    if prev_date and prev_date != today and state.get("daily_summary_date") != prev_date:
        send_daily_summary(prev_date, state.get("signals_today", 0))
        state["daily_summary_date"] = prev_date
        changed = True

    if state.get("signals_today_date") != today:
        state["signals_today_date"] = today
        state["signals_today"]      = 0
        state["sent_per_match"]     = {}
        changed = True
        safe_log("🔄 Новый день — счётчик сигналов сброшен.")

    if state.get("ai_calls_date") != today:
        state["ai_calls_date"]  = today
        state["ai_calls_today"] = 0
        changed = True

    utc_today = utc_date_str()
    api1_new_day = state.get("api_calls_key1_date") != utc_today
    api2_new_day = state.get("api_calls_key2_date") != utc_today

    if api1_new_day:
        state["api_calls_key1_date"] = utc_today
        state["api_calls_key1"]      = 0
        changed = True
        safe_log("🔄 UTC день — Ключ 1 сброшен (0/100).")
    if api2_new_day:
        state["api_calls_key2_date"] = utc_today
        state["api_calls_key2"]      = 0
        changed = True
        safe_log("🔄 UTC день — Ключ 2 сброшен (0/100).")
    if api1_new_day or api2_new_day:
        state["active_key_index"]    = 0
        state["_notified_switch_to"] = -1
        safe_log("🔄 Новый UTC день — ключ 1 активен, уведомления сброшены.")

    if state.get("plan_date") != today:
        state["plan"]               = []
        state["plan_date"]          = ""
        state["plan_retries_today"] = 0
        # v16: новый день — сбрасываем счётчик обновлений плана
        state["plan_refreshes_today"] = 0
        state["plan_last_build_ts"]   = 0.0
        changed = True
        safe_log("🔄 Новый день — план сброшен.")

    if changed:
        save_state(state)


# ═══════════════════════════════════════════════════════════════
# API — СЧЁТЧИК + HTTP
# ═══════════════════════════════════════════════════════════════

def active_key(state: Optional[Dict[str, Any]] = None) -> str:
    if not API_KEYS:
        return APISPORTS_KEY
    idx = (state.get("active_key_index", 0) if state else 0)
    if idx >= len(API_KEYS):
        idx = 0
    return API_KEYS[idx]

def api_calls_used(state: Dict[str, Any]) -> int:
    idx = state.get("active_key_index", 0)
    key = "api_calls_key1" if idx == 0 else "api_calls_key2"
    return state.get(key, 0)

def api_calls_total(state: Dict[str, Any]) -> int:
    return state.get("api_calls_key1", 0) + state.get("api_calls_key2", 0)

def _inc_api(state: Dict[str, Any], key_index: Optional[int] = None) -> int:
    """Увеличиваем счётчик ключа. По умолчанию — активного, но можно указать
    конкретный индекс (нужно для validate_api_keys, где ключ ещё не 'активен')."""
    idx = key_index if key_index is not None else state.get("active_key_index", 0)
    key = "api_calls_key1" if idx == 0 else "api_calls_key2"
    state[key] = state.get(key, 0) + 1
    return state[key]

def try_switch_key(state: Dict[str, Any]) -> bool:
    current_idx  = state.get("active_key_index", 0)
    current_used = api_calls_used(state)
    next_idx     = current_idx + 1

    if next_idx >= len(API_KEYS):
        return False

    next_key    = "api_calls_key1" if next_idx == 0 else "api_calls_key2"
    next_used   = state.get(next_key, 0)
    if next_used >= API_DAILY_LIMIT:
        return False

    from_num = current_idx + 1
    to_num   = next_idx + 1

    already_sent = (state.get("_notified_switch_to", -1) == next_idx)

    state["active_key_index"]    = next_idx
    state["_notified_switch_to"] = next_idx
    save_state(state)

    safe_log(f"🔑 Ключ {from_num} ({current_used}/{API_SOFT_LIMIT}) → Ключ {to_num} активен")
    if not already_sent:
        tg_send(
            f"🔑 Ключ {from_num} использовал {current_used}/{API_SOFT_LIMIT}\n"
            f"✅ Переключились на Ключ {to_num}\n"
            f"📡 Ключ {to_num}: {next_used}/{API_DAILY_LIMIT} запросов"
        )
    return True

def both_keys_exhausted(state: Dict[str, Any]) -> bool:
    k1_done = state.get("api_calls_key1", 0) >= API_DAILY_LIMIT
    if len(API_KEYS) >= 2:
        k2_done = state.get("api_calls_key2", 0) >= API_DAILY_LIMIT
        return k1_done and k2_done
    return k1_done

def can_call_live(state: Dict[str, Any]) -> bool:
    if api_calls_used(state) >= API_SOFT_LIMIT:
        switched = try_switch_key(state)
        if switched:
            return True
        return False
    return True

def validate_api_keys(state: Dict[str, Any]) -> list:
    """
    Проверяем каждый ключ реальным запросом к API при старте.
    FIX2: теперь /status тоже считается в state (раньше расходился счётчик).
    """
    import requests as _req
    results = []

    raw_keys = [APISPORTS_KEY, APISPORTS_KEY_2]
    for idx, key in enumerate(raw_keys):
        label = f"Ключ {idx + 1}"
        if not _is_valid_key(key):
            results.append({"label": label, "status": "NO_KEY",
                            "msg": "не задан или заглушка"})
            continue
        try:
            r = _req.get(
                f"{BASE_URL}/status",
                headers={"x-apisports-key": key},
                timeout=10
            )
            if r.status_code == 401:
                results.append({"label": label, "status": "INVALID",
                                "msg": "неверный ключ (401 Unauthorized)"})
                continue
            if r.status_code == 429:
                results.append({"label": label, "status": "BLOCKED",
                                "msg": "заблокирован / rate limit (429)"})
                continue
            if r.status_code != 200:
                results.append({"label": label, "status": "INVALID",
                                "msg": f"HTTP {r.status_code}"})
                continue

            # FIX2: считаем этот запрос в бюджет соответствующего ключа
            _inc_api(state, key_index=idx)

            data = r.json().get("response", {})
            used  = data.get("requests", {}).get("current", "?")
            limit = data.get("requests", {}).get("limit_day", 100)
            left  = limit - (used if isinstance(used, int) else 0)
            if isinstance(used, int) and used >= limit:
                results.append({"label": label, "status": "EXHAUSTED",
                                "msg": f"исчерпан ({used}/{limit})",
                                "left": 0})
            else:
                results.append({"label": label, "status": "OK",
                                "msg": f"активен ({used}/{limit}), осталось {left}",
                                "left": left})
        except Exception as e:
            results.append({"label": label, "status": "ERROR",
                            "msg": f"ошибка сети: {e}"})

    save_state(state)
    real_keys_checked = sum(1 for r in results if r["status"] != "NO_KEY")
    if real_keys_checked > 0:
        safe_log(f"⚠️ validate: {real_keys_checked} запрос(а) к /status учтены в счётчике.")
    return results


def http_get(path: str, params: Dict[str, Any],
             state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url          = f"{BASE_URL}{path}"
    max_attempts = 6

    for attempt in range(1, max_attempts + 1):
        headers = {"x-apisports-key": active_key(state)}

        try:
            r = requests.get(url, headers=headers, params=params, timeout=25)

            if r.status_code in (429, 500, 502, 503, 504):
                wait_s = random.randint(30, 60)
                try:
                    ra = r.headers.get("Retry-After")
                    if ra:
                        wait_s = max(30, min(120, int(float(ra))))
                except Exception:
                    pass
                if attempt >= max_attempts:
                    r.raise_for_status()
                if r.status_code == 429 and state:
                    switched = try_switch_key(state)
                    if switched:
                        safe_log(f"429 на ключе → переключились, retry немедленно")
                        continue
                safe_log(f"HTTP {r.status_code} [{path}]. Retry {attempt}/{max_attempts} через {wait_s}с...")
                time.sleep(wait_s)
                continue

            r.raise_for_status()
            data = r.json()

            api_errors = data.get("errors", [])
            if isinstance(api_errors, dict) and api_errors:
                safe_log(f"⚠️ API errors: {api_errors}")
                err_str = " ".join(str(v) for v in api_errors.values()).lower()
                if "plan" in err_str or "access" in err_str or "date" in err_str:
                    if state is not None:
                        cnt = _inc_api(state)
                        save_state(state)
                        safe_log(f"📡 API #{cnt}/{API_DAILY_LIMIT}: {path} (ошибка плана/даты — не ретраим)")
                    raise RuntimeError(f"API ошибка: {api_errors}")
                if attempt >= max_attempts:
                    raise RuntimeError(f"API ошибка: {api_errors}")
                time.sleep(30)
                continue

            if state is not None:
                cnt = _inc_api(state)
                save_state(state)
                safe_log(f"📡 API #{cnt}/{API_DAILY_LIMIT}: {path}")
                if cnt >= API_DAILY_LIMIT:
                    safe_log("🚨 ЛИМИТ 100 ЗАПРОСОВ ИСЧЕРПАН!")
                elif cnt >= API_SOFT_LIMIT:
                    safe_log(f"⚠️ Мягкий лимит ({API_SOFT_LIMIT}) — только результаты.")

            return data

        except requests.exceptions.RequestException as e:
            if attempt >= max_attempts:
                raise
            wait_s = random.randint(30, 60)
            safe_log(f"Сеть [{path}]: {e}. Retry {attempt} через {wait_s}с...")
            time.sleep(wait_s)

    raise RuntimeError(f"http_get failed after {max_attempts}: {path}")


# ═══════════════════════════════════════════════════════════════
# API ОБЁРТКИ
# ═══════════════════════════════════════════════════════════════

def get_fixtures_by_date(date_str: str, state: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        data = http_get("/fixtures", {"date": date_str, "timezone": TIMEZONE}, state)
        return data.get("response", [])
    except RuntimeError as e:
        if "plan" in str(e).lower() or "access" in str(e).lower() or "date" in str(e).lower():
            safe_log(f"⚠️ Дата {date_str} недоступна на free плане — пропускаем.")
            return []
        raise

def get_live_fixtures(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = http_get("/fixtures", {"live": "all"}, state)
    return data.get("response", [])

def get_fixture_by_id(fixture_id: int, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = http_get("/fixtures", {"id": fixture_id}, state)
    resp = data.get("response", [])
    return resp[0] if resp else None


# ═══════════════════════════════════════════════════════════════
# TELEGRAM + CSV
# ═══════════════════════════════════════════════════════════════

def tg_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for attempt in range(1, 4):
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                          timeout=25).raise_for_status()
            return
        except Exception as e:
            safe_log(f"TG ошибка (попытка {attempt}/3): {e}")
            if attempt < 3:
                time.sleep(5)

def ensure_csv() -> None:
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                "bet_id", "time", "fixture_id", "league", "country",
                "home", "away", "minute", "score",
                "bet_type", "line", "notes", "result", "final_score",
                "extras_result", "ai_probability",
                # v19: данные авто-ставки — для анализа истории (просили логи)
                "auto_bet_status", "auto_bet_stake", "auto_bet_odds", "auto_bet_payout",
            ])

def append_csv(row: List[Any]) -> None:
    ensure_csv()
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


# ═══════════════════════════════════════════════════════════════
# ДАННЫЕ ИЗ API
# ═══════════════════════════════════════════════════════════════

# DATA-FIX 19 (v18): молодёжные/резервные лиги (U17-U23) жрали бюджет
# ключа впустую — по логам "Brasileiro U20 A" и "Liga MX U21" попадали
# в план через ключевые слова "brasileir"/"liga mx", хотя это не те
# взрослые лиги, что реально есть на бирже (это могло вытеснять из
# бюджета настоящие ночные матчи MLS/Китая — ключ упирался в 100
# раньше времени). Фильтр ниже отсекает их ДАЖЕ если совпал ID/слово.
YOUTH_LEAGUE_PATTERN = re.compile(
    r"\b(u-?1[6-9]|u-?2[0-3]|youth|junior|juniors|reserves?)\b", re.IGNORECASE
)

def is_youth_or_reserve_league(name: str) -> bool:
    return bool(YOUTH_LEAGUE_PATTERN.search(name or ""))

def is_target_competition(fx: Dict[str, Any]) -> bool:
    league     = fx.get("league") or {}
    league_id  = safe_int(league.get("id"), 0)
    name_raw   = league.get("name") or ""
    name_lower = name_raw.strip().lower()

    # DATA-FIX 19: молодёжку/резерв отсекаем ПЕРВЫМ делом, даже если
    # совпал ID или ключевое слово ниже.
    if is_youth_or_reserve_league(name_raw):
        return False

    if league_id in LEAGUE_IDS:
        return True
    for kw in LEAGUE_NAME_KEYWORDS:
        if kw in name_lower:
            return True
    return False

def parse_fixture_start(fx: Dict[str, Any]) -> Optional[datetime]:
    date_s = (fx.get("fixture") or {}).get("date")
    if not date_s:
        return None
    try:
        return parse_iso(date_s)
    except Exception:
        return None

def get_match_minute(fx: Dict[str, Any]) -> int:
    status = (fx.get("fixture") or {}).get("status") or {}
    return safe_int(status.get("elapsed"), 0)

def get_match_status(fx: Dict[str, Any]) -> str:
    status = (fx.get("fixture") or {}).get("status") or {}
    return str(status.get("short") or "")

def current_score(fx: Dict[str, Any]) -> Tuple[int, int, int]:
    goals = fx.get("goals") or {}
    h = safe_int(goals.get("home"), 0)
    a = safe_int(goals.get("away"), 0)
    return h, a, h + a

def final_score(fx: Dict[str, Any]) -> Tuple[int, int]:
    sc = fx.get("score") or {}
    ft = sc.get("fulltime") or {}
    fh, fa = ft.get("home"), ft.get("away")
    if fh is None or fa is None:
        et = sc.get("extratime") or {}
        fh = et.get("home") if fh is None else fh
        fa = et.get("away") if fa is None else fa
    if fh is None or fa is None:
        goals = fx.get("goals") or {}
        fh = goals.get("home", 0) if fh is None else fh
        fa = goals.get("away", 0) if fa is None else fa
    return safe_int(fh, 0), safe_int(fa, 0)

def get_fixture_id(fx: Dict[str, Any]) -> int:
    return safe_int((fx.get("fixture") or {}).get("id"), 0)

def get_league_info(fx: Dict[str, Any]) -> Tuple[str, str]:
    league = fx.get("league") or {}
    return (league.get("name") or ""), (league.get("country") or "")

def get_team_names(fx: Dict[str, Any]) -> Tuple[str, str]:
    teams = fx.get("teams") or {}
    home  = (teams.get("home") or {}).get("name") or "Home"
    away  = (teams.get("away") or {}).get("name") or "Away"
    return home, away


# ═══════════════════════════════════════════════════════════════
# ЛОГИКА СТАВОК
# ═══════════════════════════════════════════════════════════════

def describe_bet(b: Dict[str, Any], home: str, away: str) -> Tuple[str, str]:
    t = b.get("type", "")
    line = b.get("line", 0.0)
    if t == "TOTAL_OVER":  return "Тотал матча (Full Time)", f"OVER {line:g}"
    if t == "TOTAL_UNDER": return "Тотал матча (Full Time)", f"UNDER {line:g}"
    if t == "BTTS_NO":     return "Обе забьют (BTTS)",       "НЕТ (No)"
    if t == "DC_HOME":     return "Двойной шанс",             f"1X ({home} или ничья)"
    if t == "DC_AWAY":     return "Двойной шанс",             f"X2 ({away} или ничья)"
    return "Ставка", f"{t} {line}"

def eval_bet(b: Dict[str, Any], fh: int, fa: int) -> bool:
    t = b.get("type", "")
    line = float(b.get("line", 0.0))
    tot = fh + fa
    if t == "TOTAL_OVER":  return tot > line
    if t == "TOTAL_UNDER": return tot < line
    if t == "BTTS_NO":     return fh == 0 or fa == 0
    if t == "DC_HOME":     return fh >= fa
    if t == "DC_AWAY":     return fa >= fh
    return False

# NEW: считаем реальный win rate из уже накопленной статистики,
# вместо того чтобы полагаться на придуманные проценты.
def historical_win_rate(bet_type: str, min_samples: int = 8) -> Optional[Tuple[float, int]]:
    """Возвращает (win_rate 0..1, кол-во сэмплов) по данному типу ставки
    из signals.csv, либо None если сэмплов меньше min_samples."""
    if not os.path.exists(CSV_FILE):
        return None
    wins = total = 0
    try:
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("bet_type") != bet_type:
                    continue
                result = row.get("result")
                if result not in ("WIN", "LOSE"):
                    continue
                total += 1
                if result == "WIN":
                    wins += 1
    except Exception as e:
        safe_log(f"⚠️ historical_win_rate: {e}")
        return None
    if total < min_samples:
        return None
    return wins / total, total

def is_low_value_bet(main: Dict[str, Any], h: int, a: int) -> bool:
    """FIX5: помечаем ставки, которые почти наверняка зайдут и поэтому
    у букмекера будут стоить около 1.00-1.10 (без реальной ценности).
    Актуально и для TOTAL_UNDER (глубокая линия), и для DC (v11:
    DC теперь основная ставка и при разрыве в 3+ гола, где 1X/X2
    у букмекера тоже может стоить неприлично мало)."""
    diff = abs(h - a)
    total = h + a
    if main["type"] == "TOTAL_UNDER" and (diff >= 3 or total >= 4 and diff >= 2):
        return True
    if main["type"] in ("DC_HOME", "DC_AWAY") and diff >= 3:
        return True
    return False

def _pick_signal_bundle_total(fx: Dict[str, Any], minute: int) -> Optional[Dict[str, Any]]:
    """
    ═══ DATA-FIX 18 (v17) — ПЕРЕВОРОТ СТРАТЕГИИ ПО РЕАЛЬНОЙ БИРЖЕ ═══
    Разобрали скриншоты 7777.md (сигнал ↔ что реально доступно):

    • Двойной шанс на лидера (1X/X2) — наш бывший «главный» сигнал с
      win rate 98% — на бирже ПОЧТИ ВСЕГДА ЗАБЛОКИРОВАН 🔒 (при
      разрыве 2 гола), либо чистая победа стоит ~1.01. При кэфе 1.01
      98% побед = ТОЧКА БЕЗУБЫТКА, по факту в минус. Букмекер их
      закрывает именно потому, что там нет ценности.
    • BTTS_NO при 0-0 = кэф 1.07 → безубыток при 93.5% win rate, а у
      нас 86.7% → тоже в МИНУС.
    • TOTAL_UNDER на линии «больше голов не будет» (current+0.5) — на
      бирже ОТКРЫТ, кэф реальный ~1.8-2.3, а по нашей истории win rate
      58%. Безубыток при кэфе 2.2 = 45.5%, а у нас 58% → это
      ЕДИНСТВЕННЫЙ и открытый, и ПРИБЫЛЬНЫЙ рынок.

    ВЫВОД: главной ставкой становится TOTAL_UNDER (current+0.5) при
    ЛЮБОМ счёте. DC/победа лидера уходят в текстовую пометку сообщения
    (не как ставка — их обычно нельзя поставить). Формулы eval_bet
    не менялись, только выбор рынка.

    ⚠️ Без /odds API бот НЕ знает точный коэффициент. Поэтому в
    сообщении жёсткое правило: ставить только если кэф ≥ MIN_ODDS.

    ВКЛЮЧАЕТСЯ через SIGNAL_STRATEGY = "TOTAL" (см. настройки выше).
    """
    h, a, total = current_score(fx)
    diff        = abs(h - a)

    # Слишком много голов — под любой открытой линией уже нет ценности.
    if total >= 6:
        return None

    # ── ГЛАВНАЯ СТАВКА: «больше голов не будет» (TOTAL_UNDER current+0.5) ──
    no_more_line = float(total) + 0.5
    main = {"type": "TOTAL_UNDER", "line": no_more_line,
            "why": (f"Счёт {h}-{a} на {minute}' → ставка, что больше голов НЕ будет. "
                    f"Это рынок, который на бирже ОТКРЫТ и с реальным кэфом (~1.8-2.3)")}

    extras = []
    # Доп: более безопасная линия (допускает ещё +1 гол) — кэф ниже,
    # но выше вероятность; бери, если основная линия даёт слишком мало.
    safe_line = float(total) + 1.5
    extras.append({"type": "TOTAL_UNDER", "line": safe_line,
                   "why": "безопаснее: допускает ещё 1 гол (кэф ниже, но надёжнее)"})

    # low_value: если голов уже много (>=4) — даже current+0.5 может
    # стоить дёшево (мало игры осталось для 2 голов) → предупреждаем.
    low_value = (total >= 4)

    return {"main": main, "extras": extras[:3], "low_value": low_value}


def _pick_signal_bundle_dc(fx: Dict[str, Any], minute: int) -> Optional[Dict[str, Any]]:
    """
    СТРАТЕГИЯ ДО v17 (двойной шанс на лидера / BTTS) — ВОССТАНОВЛЕНА в
    v18 по просьбе пользователя: пока не готов переходить на TOTAL,
    работаем как сейчас на бирже. Формулы win/lose в eval_bet верны,
    это подтверждено логами (18/18 побед по этой логике за 13-16 июля).

    ⚠️ НАПОМИНАНИЕ ИЗ АУДИТА v17 (см. _pick_signal_bundle_total выше):
    при разрыве ≥2 гола двойной шанс на лидера на бирже обычно
    ЗАБЛОКИРОВАН 🔒, а чистая победа стоит ~1.01 — ставки той же
    ценности там часто просто нет, даже когда сигнал технически верный.
    Победы 18/18 в логах — это верные ПРОГНОЗЫ, не обязательно
    исполненные ставки. Когда будешь готов — переключи
    SIGNAL_STRATEGY = "TOTAL" одной строкой, код готов.

      0-0:        BTTS_NO как основная
      1-0 / 0-1:  DC для ведущей команды
      1-1/2-2:    TOTAL_UNDER current+1.5
      разрыв ≥2:  DC для лидера (см. DATA-FIX 7)
    """
    h, a, total = current_score(fx)
    diff        = abs(h - a)
    base_line   = float(total) + 0.5

    if total >= 6:
        return None

    if diff == 0 and total == 0:
        main = {"type": "BTTS_NO", "line": 0.0,
                "why": f"0-0 на {minute}'+ → обе команды скорее не забьют (эвристика)"}
    elif diff >= 1 and total >= 1:
        if h > a:
            main = {"type": "DC_HOME", "line": 0.0,
                    "why": f"Хозяева ведут {h}-{a} на {minute}' → 1X (реальный win rate ~98%)"}
        else:
            main = {"type": "DC_AWAY", "line": 0.0,
                    "why": f"Гости ведут {a}-{h} на {minute}' → X2 (реальный win rate ~98%)"}
    else:
        main = {"type": "TOTAL_UNDER", "line": float(base_line + 1.0),
                "why": f"Счёт {h}-{a} на {minute}' → вряд ли 2+ голов за остаток (эвристика)"}

    extras = []
    if main["type"] == "TOTAL_UNDER":
        alt = min(7.5, main["line"] + 1.0)
        if alt != main["line"]:
            extras.append({"type": "TOTAL_UNDER", "line": float(alt),
                           "why": "безопасная линия (+1 гол)"})
    elif main["type"] in ("DC_HOME", "DC_AWAY"):
        if h == 0 or a == 0:
            extras.append({"type": "BTTS_NO", "line": 0.0,
                           "why": f"{'Гости' if a==0 else 'Хозяева'} без гола → BTTS NO"})
        if diff >= 2:
            extras.append({"type": "TOTAL_UNDER", "line": float(min(6.5, base_line)),
                           "why": f"страховка: разрыв {diff} гола → доигрывают (~58%)"})
    elif main["type"] == "BTTS_NO":
        extras.append({"type": "TOTAL_UNDER", "line": float(min(6.5, base_line + 1.0)),
                       "why": f"страховочный UNDER {base_line+1.0:g}"})

    main_type = main["type"]

    def already_in(bet_type):
        return any(e["type"] == bet_type for e in extras)

    if minute >= MIN_MINUTE and (h == 0 or a == 0):
        if main_type != "BTTS_NO" and not already_in("BTTS_NO"):
            extras.append({"type": "BTTS_NO", "line": 0.0,
                           "why": "одна команда без гола → BTTS NO"})

    if minute >= MIN_MINUTE:
        if h > a and main_type != "DC_HOME" and not already_in("DC_HOME"):
            extras.append({"type": "DC_HOME", "line": 0.0,
                           "why": f"хозяева ведут {h}-{a} → 1X"})
        elif a > h and main_type != "DC_AWAY" and not already_in("DC_AWAY"):
            extras.append({"type": "DC_AWAY", "line": 0.0,
                           "why": f"гости ведут {a}-{h} → X2"})

    return {"main": main, "extras": extras[:3], "low_value": is_low_value_bet(main, h, a)}


def pick_signal_bundle(fx: Dict[str, Any], minute: int) -> Optional[Dict[str, Any]]:
    """Диспетчер стратегии — см. SIGNAL_STRATEGY в настройках."""
    if SIGNAL_STRATEGY == "TOTAL":
        return _pick_signal_bundle_total(fx, minute)
    return _pick_signal_bundle_dc(fx, minute)

def build_bundle_message(fx: Dict[str, Any], minute: int, bundle: Dict[str, Any],
                          ai_result: Optional[Dict[str, Any]] = None) -> str:
    league_name, country = get_league_info(fx)
    home, away           = get_team_names(fx)
    h, a, _              = current_score(fx)

    lines = [
        "⚽ LIVE СИГНАЛ PRO",
        f"🏆 Лига:  {league_name} ({country})",
        f"🆚 Матч:  {home} vs {away}",
        f"⏱ Минута: {minute}' | Счёт: {h}-{a}",
        "",
    ]
    main = bundle["main"]
    m_mkt, m_sel = describe_bet(main, home, away)

    hist = historical_win_rate(main["type"])
    if hist:
        rate, n = hist
        confidence_line = f"📊 Реальный win rate по нашей истории: {rate*100:.0f}% (n={n})"
    else:
        confidence_line = "📊 Win rate: пока недостаточно данных — это эвристика, не проверено бэктестом"

    lines += [
        "⭐ ОСНОВНАЯ СТАВКА:",
        f"   ✅ Куда: {m_mkt}",
        f"   ✅ Что:  {m_sel}",
        f"   📝 Почему: {main.get('why','')}",
        f"   {confidence_line}",
        "",
    ]

    if ai_result:
        prob = ai_result.get("probability")
        reason = ai_result.get("reason", "")
        if prob is not None:
            lines.append(f"🤖 Оценка Claude: {prob*100:.0f}% — {reason}")
            lines.append("")

    if bundle.get("low_value"):
        lines.append("⚠️ Голов уже много — даже эта линия может стоить дёшево (≈1.0-1.2).")
        lines.append("   Ставь только если кэф всё же ≥ показанного ниже, иначе пропусти.")
        lines.append("")

    extras = bundle.get("extras", [])
    if extras:
        extras_label = ("➕ ЗАПАСНАЯ ЛИНИЯ (если основная даёт мало кэфа):"
                         if SIGNAL_STRATEGY == "TOTAL" else "➕ ДОПОЛНИТЕЛЬНО:")
        lines.append(extras_label)
        for i, b in enumerate(extras, 1):
            mkt, sel = describe_bet(b, home, away)
            lines.append(f"   Доп {i}: {mkt} — {sel}")
            if b.get("why"):
                lines.append(f"           • {b['why']}")
        lines.append("")

    # DATA-FIX 18: пометка про лидера — только в режиме TOTAL, где
    # именно двойной шанс/победа лидера НЕ являются основной ставкой
    # (в режиме DC это и есть основная ставка — писать про неё
    # отдельной пометкой было бы просто дублированием).
    if SIGNAL_STRATEGY == "TOTAL" and h != a:
        leader_sel = "«1» (победа хозяев)" if h > a else "«2» (победа гостей)"
        lines.append(f"ℹ️ {'Хозяева' if h>a else 'Гости'} ведут — можно и {leader_sel} на 1X2,")
        lines.append("   НО: двойной шанс (1X/X2) биржа обычно блокирует 🔒, а чистая")
        lines.append("   победа при разрыве 2 гола стоит ~1.0 (без ценности). Смотри по кэфу.")
        lines.append("")

    lines += [
        f"🎯 ГЛАВНОЕ ПРАВИЛО: ставь ТОЛЬКО если фактический кэф ≥ {MIN_ODDS:g}.",
        "   Если рынок заблокирован 🔒 или кэф ниже — ПРОПУСТИ, там нет ценности.",
        "💵 Ставка: 5 лей (фикс). Без догонов.",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# АВТО-СТАВКА — ДЕТАЛЬНЫЕ СТАТУСЫ В TELEGRAM (v19)
# ═══════════════════════════════════════════════════════════════

def describe_auto_bet_result(result: Dict[str, Any]) -> str:
    """Превращает статус от auto_bet_7777.place_bet_from_signal() в
    понятное сообщение — как просили: поставлена/не удалось/не нашёл/
    заблокирована/не залогинен/тест-режим — каждый случай отдельно."""
    status = (result or {}).get("status", "ERROR")
    detail = (result or {}).get("detail", "")
    stake  = (result or {}).get("stake") or 5.0
    odds   = (result or {}).get("odds")

    if status == "PLACED":
        odds_txt = f" по кэфу ~{odds:g}" if odds else ""
        return f"✅ Авто-ставка ПОСТАВЛЕНА: {stake:g} лей{odds_txt}."
    if status == "DRY_RUN":
        odds_txt = f" (кэф ~{odds:g})" if odds else ""
        return (f"🧪 [ТЕСТ-РЕЖИМ] Нашёл рынок и поставил бы {stake:g} лей{odds_txt}, "
                f"но реально не ставил (AUTO_BET_DRY_RUN=True). {detail}")
    if status == "NOT_LOGGED_IN":
        return "🚨 Авто-ставка НЕ ПОСТАВЛЕНА — ты вышел из профиля на бирже. Зайди и залогинься."
    if status == "MATCH_NOT_FOUND":
        return f"⚠️ Авто-ставка НЕ ПОСТАВЛЕНА — не нашёл этот матч на бирже. {detail}\nПоставь вручную!"
    if status == "NOT_FOUND":
        return f"⚠️ Авто-ставка НЕ ПОСТАВЛЕНА — не нашёл нужную ставку/линию на бирже. {detail}\nПоставь вручную!"
    if status == "BLOCKED":
        return "🔒 Авто-ставка НЕ ПОСТАВЛЕНА — рынок уже заблокирован (поздно). Ценности в ставке нет, пропусти."
    if status == "FAILED":
        return f"❌ Авто-ставка НЕ ПОЛУЧИЛАСЬ — {detail}\nПоставь вручную!"
    return f"❌ Авто-ставка НЕ ПОЛУЧИЛАСЬ — ошибка: {detail}\nПоставь вручную!"


# ═══════════════════════════════════════════════════════════════
# CLAUDE AI — ПОДТВЕРЖДЕНИЕ СИГНАЛОВ И ЕЖЕДНЕВНЫЙ РАЗБОР
# ═══════════════════════════════════════════════════════════════

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Claude иногда оборачивает JSON в ```json ... ``` — вытаскиваем объект."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None

def _ai_budget_ok(state: Dict[str, Any]) -> bool:
    return state.get("ai_calls_today", 0) < AI_MAX_CALLS_PER_DAY

def ai_confirm_signal(fx: Dict[str, Any], minute: int,
                       bundle: Dict[str, Any], state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Спрашивает у Claude вероятность захода конкретной ставки по
    структурированному контексту матча. Возвращает
    {"probability": float 0..1, "reason": str} или None, если
    ИИ выключен/недоступен/бюджет исчерпан/ошибка сети —
    в этих случаях бот работает как раньше, на чистой эвристике
    (fail-open: недоступность ИИ не должна убивать все сигналы).
    """
    if not (AI_CONFIRM_ENABLED and ANTHROPIC_AVAILABLE):
        return None
    if not _ai_budget_ok(state):
        safe_log("🤖 AI: дневной лимит запросов исчерпан — пропускаем проверку.")
        return None

    league_name, country = get_league_info(fx)
    home, away           = get_team_names(fx)
    h, a, total           = current_score(fx)
    main                  = bundle["main"]

    context = {
        "league": league_name,
        "country": country,
        "minute": minute,
        "home_team": home,
        "away_team": away,
        "score": f"{h}-{a}",
        "bet_type": main["type"],
        "bet_line": main.get("line"),
        "heuristic_reason": main.get("why", ""),
    }

    prompt = (
        "Ты — консервативный аналитик live-ставок на футбол. "
        "У тебя НЕТ доступа к статистике команд сверх того, что дано ниже — "
        "не выдумывай факты о форме/травмах, которых нет в данных. "
        "Оцени вероятность (от 0 до 1), что указанная ставка зайдёт по итогам матча, "
        "опираясь только на переданный контекст (счёт, минуту, тип рынка). "
        "Если контекста недостаточно для уверенной оценки — дай осторожную вероятность "
        "ближе к 0.5 и укажи это в reason. "
        "Ответь СТРОГО валидным JSON без markdown-разметки: "
        '{"probability": <float>, "reason": "<кратко, 1 предложение, по-русски>"}\n\n'
        f"Контекст матча:\n{json.dumps(context, ensure_ascii=False)}"
    )

    try:
        resp = _anthropic_client.messages.create(
            model=AI_MODEL,
            max_tokens=250,
            timeout=AI_TIMEOUT_SECONDS,
            messages=[{"role": "user", "content": prompt}],
        )
        state["ai_calls_today"] = state.get("ai_calls_today", 0) + 1
        save_state(state)

        raw_text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        data = _extract_json(raw_text)
        if not data or "probability" not in data:
            safe_log(f"⚠️ AI: не удалось распарсить ответ: {raw_text[:200]}")
            return None

        prob = float(data["probability"])
        prob = max(0.0, min(1.0, prob))
        return {"probability": prob, "reason": str(data.get("reason", ""))[:200]}

    except Exception as e:
        safe_log(f"⚠️ AI ошибка: {e} — продолжаем без мнения ИИ.")
        return None


def ai_daily_review(state: Dict[str, Any]) -> None:
    """Раз в сутки просим Claude разобрать вчерашнюю статистику из signals.csv
    и прислать краткие выводы в Telegram. Чисто аналитическая задача —
    не предсказание будущего, поэтому это безопасное и полезное применение ИИ."""
    if not (AI_DAILY_REVIEW_ENABLED and ANTHROPIC_AVAILABLE):
        return
    today = today_str()
    if state.get("ai_review_date") == today:
        return
    if not _ai_budget_ok(state):
        return
    if not os.path.exists(CSV_FILE):
        state["ai_review_date"] = today
        save_state(state)
        return

    try:
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        safe_log(f"⚠️ ai_daily_review: не смог прочитать CSV: {e}")
        return

    finished = [r for r in rows if r.get("result") in ("WIN", "LOSE")]
    if len(finished) < 5:
        state["ai_review_date"] = today
        save_state(state)
        return

    recent = finished[-100:]  # не раздуваем промпт бесконечно
    summary_rows = [
        {"bet_type": r["bet_type"], "line": r.get("line"), "result": r["result"],
         "league": r.get("league")}
        for r in recent
    ]

    prompt = (
        "Вот последние результаты live-сигналов по футбольным ставкам (JSON-список). "
        "Посчитай win rate по каждому типу ставки (bet_type), укажи, какие типы "
        "показывают слабый результат (< 55%), и дай 2-3 практических совета по "
        "стратегии одним коротким абзацем на русском. Без markdown-таблиц, обычный текст, "
        "уложись в 120 слов.\n\n"
        f"{json.dumps(summary_rows, ensure_ascii=False)}"
    )

    try:
        resp = _anthropic_client.messages.create(
            model=AI_MODEL,
            max_tokens=400,
            timeout=AI_TIMEOUT_SECONDS,
            messages=[{"role": "user", "content": prompt}],
        )
        state["ai_calls_today"] = state.get("ai_calls_today", 0) + 1
        state["ai_review_date"] = today
        save_state(state)

        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        tg_send(f"🤖 ЕЖЕДНЕВНЫЙ РАЗБОР ОТ CLAUDE\n{'─'*28}\n{text}")
    except Exception as e:
        safe_log(f"⚠️ ai_daily_review ошибка: {e}")
        state["ai_review_date"] = today
        save_state(state)


# ═══════════════════════════════════════════════════════════════
# ПЛАНИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════

def build_24h_plan(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    now   = now_dt()
    today = now.strftime("%Y-%m-%d")
    tmrw  = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    safe_log("📋 Строим план на 24ч (2 API-запроса)...")
    fixtures: List[Dict[str, Any]] = []
    fixtures.extend(get_fixtures_by_date(today, state))
    fixtures.extend(get_fixtures_by_date(tmrw,  state))

    safe_log(f"📥 Всего от API: {len(fixtures)} матчей (до фильтра лиг)")

    leagues_in_api = sorted({
        f"{(fx.get('league') or {}).get('name','')} "
        f"[id={(fx.get('league') or {}).get('id','')}]"
        for fx in fixtures
    })
    safe_log(f"📋 Лиги: {leagues_in_api[:25]}")

    plan = []
    for fx in fixtures:
        if not is_target_competition(fx):
            continue

        start = parse_fixture_start(fx)
        if not start:
            continue

        active_window_end = start + timedelta(minutes=ACTIVE_TO_MIN)
        if active_window_end < now:
            continue

        if start > now + timedelta(hours=24):
            continue

        fid = get_fixture_id(fx)
        if fid <= 0:
            continue

        league_name, country = get_league_info(fx)
        home, away           = get_team_names(fx)

        plan.append({
            "fixture_id": fid,
            "start_iso":  start.isoformat(),
            "league":     league_name,
            "country":    country,
            "home":       home,
            "away":       away,
        })

    plan.sort(key=lambda x: x["start_iso"])
    safe_log(f"✅ В плане {len(plan)} матч(ей) после фильтра.")
    return plan

def send_plan_to_telegram(plan: List[Dict[str, Any]]) -> None:
    """BUG-FIX 12: раньше слал построчный список до 25 матчей —
    много текста каждый день. Теперь одна строка с количеством
    ("сколько команд", как просили) — полный список всегда доступен
    по команде "итоги"/"когда" в Telegram, если понадобятся детали."""
    if not plan:
        tg_send("📅 На сегодня: подходящих матчей не найдено.")
        return
    tg_send(f"📅 План на сегодня: {len(plan)} матч(ей) в отслеживаемых лигах.")


# ═══════════════════════════════════════════════════════════════
# АКТИВНОЕ ОКНО
# ═══════════════════════════════════════════════════════════════

def is_any_match_active_now(plan: List[Dict[str, Any]]) -> bool:
    now = now_dt()
    for p in plan:
        start = parse_iso(p["start_iso"])
        if start + timedelta(minutes=ACTIVE_FROM_MIN) <= now <= start + timedelta(minutes=ACTIVE_TO_MIN):
            return True
    return False

def next_activation_time(plan: List[Dict[str, Any]]) -> Optional[datetime]:
    now = now_dt()
    best: Optional[datetime] = None
    for p in plan:
        start = parse_iso(p["start_iso"])
        af = start + timedelta(minutes=ACTIVE_FROM_MIN)
        at = start + timedelta(minutes=ACTIVE_TO_MIN)
        if now > at:
            continue
        if af <= now:
            return now
        if best is None or af < best:
            best = af
    return best

def build_next_match_text(plan: List[Dict[str, Any]]) -> str:
    now = now_dt()
    upcoming = []
    for p in plan:
        start = parse_iso(p["start_iso"])
        if start + timedelta(minutes=ACTIVE_TO_MIN) > now:
            upcoming.append((start, p))
    if not upcoming:
        return "все матчи дня завершены"
    upcoming.sort(key=lambda x: x[0])
    s, p = upcoming[0]
    status = "🔴 LIVE" if s <= now else s.strftime('%d.%m %H:%M')
    return f"{status} — {p['home']} vs {p['away']} [{p['league']}]"


# ═══════════════════════════════════════════════════════════════
# ПРОВЕРКА РЕЗУЛЬТАТОВ СТАВОК
# ═══════════════════════════════════════════════════════════════

def check_finished_bets(state: Dict[str, Any]) -> None:
    """FIX3: лимит поднят с 1 до RESULT_CHECKS_PER_CYCLE за вызов.
    FIX6: extras теперь тоже сохраняются в CSV.
    Вызывающий код (main) больше не блокирует эту функцию лимитом
    сигналов/пустым планом — см. BUG-FIX 1 в шапке файла."""
    now_t    = now_ts()
    finished = []
    checked  = 0

    for bet_id, bet in list(state["open_bets"].items()):
        if bet.get("result_checked"):
            finished.append(bet_id)
            continue

        start_iso = bet.get("match_start_iso")
        if start_iso:
            try:
                check_after = parse_iso(start_iso) + timedelta(minutes=MATCH_RESULT_CHECK_AFTER_MIN)
                if now_dt() < check_after:
                    remain = int((check_after - now_dt()).total_seconds() / 60)
                    safe_log(f"⏳ {bet.get('home','?')} vs {bet.get('away','?')} — результат через ~{remain} мин")
                    continue
            except Exception:
                pass

        last_check = bet.get("last_check_ts", 0)
        if now_t - last_check < RESULT_RETRY_INTERVAL_MIN * 60 and last_check > 0:
            continue

        if api_calls_used(state) >= API_DAILY_LIMIT:
            safe_log("🚫 API лимит — не проверяем результаты.")
            break

        if checked >= RESULT_CHECKS_PER_CYCLE:
            continue

        checked += 1
        safe_log(f"🔍 Результат: {bet.get('home','?')} vs {bet.get('away','?')}")
        fx = get_fixture_by_id(bet.get("fixture_id", 0), state)
        state["open_bets"][bet_id]["last_check_ts"] = now_t

        if not fx:
            continue

        status = get_match_status(fx)
        if status not in ("FT", "AET", "PEN"):
            safe_log(f"⏳ Матч ещё не завершён (статус: {status})")
            continue

        fh, fa = final_score(fx)
        total  = fh + fa

        home_name = bet.get("home", "?")
        away_name = bet.get("away", "?")

        bets_list = bet.get("bets") or [{"type": bet.get("bet_type",""), "line": bet.get("line",0)}]
        msg_lines = [
            "🏁 МАТЧ ЗАВЕРШЁН",
            f"{home_name} vs {away_name}",
            f"Счёт: {fh}–{fa}  (голов: {total})", "",
            "Результаты:"
        ]
        results = []
        extras_results = []
        for i, b in enumerate(bets_list):
            mkt, sel = describe_bet(b, home_name, away_name)
            ok = eval_bet(b, fh, fa)
            results.append(ok)
            prefix = "⭐ Основная" if i == 0 else f"➕ Доп {i}"
            msg_lines.append(f"  {prefix}: {mkt} — {sel} → {'✅ WIN' if ok else '❌ LOSE'}")
            if i > 0:
                extras_results.append(f"{b.get('type','')}:{b.get('line','')}:{'WIN' if ok else 'LOSE'}")

        main_ok = results[0] if results else False
        result  = "WIN" if main_ok else "LOSE"
        msg_lines += ["", f"Итог ОСНОВНОЙ: {'✅ ЗАШЛА' if main_ok else '❌ НЕ ЗАШЛА'}"]

        # v19: если авто-ставка реально была поставлена и мы знаем кэф —
        # считаем и показываем сумму выигрыша/проигрыша, как просили
        # ("поставил и ставка зашла, наш выигрыш составил...").
        auto_status = bet.get("auto_bet_status")
        auto_stake  = bet.get("auto_bet_stake") or 0
        auto_odds   = bet.get("auto_bet_odds")
        auto_payout = None   # +выигрыш если WIN, -ставка если LOSE, None если неизвестно/не ставили
        if auto_status == "PLACED" and auto_stake and auto_odds:
            if main_ok:
                auto_payout = round(auto_stake * auto_odds - auto_stake, 2)
                msg_lines.append(f"💰 Авто-ставка зашла — выигрыш: +{auto_payout:g} лей "
                                 f"(ставка {auto_stake:g} × кэф {auto_odds:g})")
            else:
                auto_payout = -auto_stake
                msg_lines.append(f"💸 Авто-ставка не зашла — минус {auto_stake:g} лей.")
        elif auto_status == "PLACED":
            msg_lines.append("💰 Авто-ставка была поставлена (сумма выигрыша неизвестна — не сохранён кэф).")

        tg_send("\n".join(msg_lines))

        main_bet = bets_list[0] if bets_list else {}
        append_csv([
            bet_id, bet.get("time",""), bet.get("fixture_id",""),
            bet.get("league",""), bet.get("country",""),
            home_name, away_name, bet.get("minute",""), bet.get("score",""),
            main_bet.get("type", bet.get("bet_type","")),
            main_bet.get("line", bet.get("line",0)),
            bet.get("notes",""), result, f"{fh}-{fa}",
            ";".join(extras_results),
            bet.get("ai_probability", ""),
            auto_status or "", auto_stake or "", auto_odds or "",
            auto_payout if auto_payout is not None else "",
        ])

        state["open_bets"][bet_id]["result_checked"] = True
        finished.append(bet_id)
        save_state(state)

    for bid in finished:
        state["open_bets"].pop(bid, None)
    save_state(state)


# ═══════════════════════════════════════════════════════════════
# HEARTBEAT
# ═══════════════════════════════════════════════════════════════

def send_heartbeat(state: Dict[str, Any], plan: List[Dict[str, Any]]) -> None:
    api_used   = api_calls_used(state)
    mode       = "🔴 АКТИВЕН" if is_any_match_active_now(plan) else "😴 СОН"
    next_match = build_next_match_text(plan)
    pending    = sum(1 for b in state.get("open_bets",{}).values() if not b.get("result_checked"))
    ai_status  = "✅ ВКЛ" if ANTHROPIC_AVAILABLE else ("⬜ выкл" if not AI_CONFIRM_ENABLED else "⚠️ нет ключа")

    tg_send(
        f"🤖 СТАТУС БОТА\n"
        f"{'─'*28}\n"
        f"Режим:    {mode}\n"
        f"🤖 Авто-ставка: {'✅ ВКЛ' if AUTO_BET_ENABLED else '❌ ВЫКЛ'}\n"
        f"🧠 Claude AI:   {ai_status} ({state.get('ai_calls_today',0)}/{AI_MAX_CALLS_PER_DAY} запросов)\n"
        f"📅 План:   {state.get('plan_date','—')} ({len(plan)} матч.)\n"
        f"🎯 Сигналы: {state.get('signals_today',0)}/{MAX_SIGNALS_PER_DAY}\n"
        f"📡 API к1: {state.get('api_calls_key1',0)}/{API_DAILY_LIMIT} | к2: {state.get('api_calls_key2',0)}/{API_DAILY_LIMIT}\n"
        f"🧾 Ставки: {pending} ждут результата\n"
        f"⏭ Матч:   {next_match}\n"
        f"⏱ Окно:   {MIN_MINUTE}–{MAX_MINUTE} мин | Опрос: {POLL_SECONDS_ACTIVE}с"
    )


# ═══════════════════════════════════════════════════════════════
# TELEGRAM-КОМАНДЫ (NEW v13) — спросить бота "жив?"/"когда игра?"
# ═══════════════════════════════════════════════════════════════

def cmd_status_text(state: Dict[str, Any]) -> str:
    plan       = state.get("plan", [])
    mode       = "🔴 АКТИВЕН — сейчас окно сигнала" if is_any_match_active_now(plan) else "😴 СОН — жду следующее окно"
    next_match = build_next_match_text(plan)
    pending    = sum(1 for b in state.get("open_bets", {}).values() if not b.get("result_checked"))
    return (
        "🤖 СТАТУС БОТА\n"
        f"{'─'*28}\n"
        f"Живой: ✅ да, работаю нормально\n"
        f"Режим: {mode}\n"
        f"📅 План: {len(plan)} матч(ей) на сегодня\n"
        f"🎯 Сигналов сегодня: {state.get('signals_today',0)}/{MAX_SIGNALS_PER_DAY}\n"
        f"📡 API: {api_calls_used(state)}/{API_DAILY_LIMIT}\n"
        f"🧾 Ставок ждут результата: {pending}\n"
        f"⏭ {next_match}"
    )

def cmd_next_text(state: Dict[str, Any]) -> str:
    plan = state.get("plan", [])
    if not plan:
        return "📅 План на сегодня пуст — новый построится сам, жди начала следующего дня."
    if is_any_match_active_now(plan):
        return f"🔴 Прямо сейчас идёт окно сигнала: {build_next_match_text(plan)}"
    nxt = next_activation_time(plan)
    if nxt is None:
        return "📅 Все матчи из сегодняшнего плана уже отыграли своё окно."
    mins = max(0, int((nxt - now_dt()).total_seconds() / 60))
    return f"⏭ Следующее окно сигнала — через ~{mins} мин.\n{build_next_match_text(plan)}"

def _count_results_since(date_prefix: str) -> Tuple[int, int]:
    """Считает WIN/LOSE в signals.csv для строк, у которых time начинается с date_prefix."""
    wins = losses = 0
    if not os.path.exists(CSV_FILE):
        return wins, losses
    try:
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not (row.get("time") or "").startswith(date_prefix):
                    continue
                if row.get("result") == "WIN":
                    wins += 1
                elif row.get("result") == "LOSE":
                    losses += 1
    except Exception as e:
        safe_log(f"⚠️ _count_results_since: {e}")
    return wins, losses

def cmd_stats_text(state: Dict[str, Any]) -> str:
    wins, losses = _count_results_since(today_str())
    sent    = state.get("signals_today", 0)
    checked = wins + losses
    pending = max(0, sent - checked)
    rate    = f"{wins/checked*100:.0f}%" if checked else "—"
    return (
        "📊 ИТОГИ СЕГОДНЯ (пока что)\n"
        f"{'─'*28}\n"
        f"Сигналов отправлено: {sent}/{MAX_SIGNALS_PER_DAY}\n"
        f"✅ Побед: {wins}\n"
        f"❌ Поражений: {losses}\n"
        f"⏳ Ещё не проверено: {pending}\n"
        f"Win rate (по проверенным): {rate}"
    )

def cmd_help_text() -> str:
    return (
        "🤖 КОМАНДЫ БОТУ (пиши обычным текстом)\n"
        f"{'─'*28}\n"
        "«жив» / «статус» — работаю или затупил?\n"
        "«когда» / «следующая игра» — через сколько ждать сигнал\n"
        "«итоги» / «сколько» — счёт побед/поражений за сегодня прямо сейчас\n"
        "«помощь» — это сообщение"
    )

def handle_telegram_command(text: str, state: Dict[str, Any]) -> None:
    t = text.strip().lower()
    if not t:
        return
    if any(k in t for k in ("жив", "статус", "status", "работа", "затуп")):
        tg_send(cmd_status_text(state))
    elif any(k in t for k in ("следующ", "когда", "next", "жд")):
        tg_send(cmd_next_text(state))
    elif any(k in t for k in ("итог", "сколько", "результат", "stats", "счёт", "счет")):
        tg_send(cmd_stats_text(state))
    elif any(k in t for k in ("помощь", "команд", "help")):
        tg_send(cmd_help_text())
    else:
        tg_send("🤖 Не понял. Напиши «помощь» — покажу список команд.")

def poll_telegram_commands(state: Dict[str, Any], timeout_sec: int = 3) -> None:
    """Короткий long-poll Telegram getUpdates. НЕ трогает бюджет
    API-Sports — это отдельный лимит Telegram, всегда бесплатный.
    Заодно отмечает, что процесс жив (last_alive_ts) — используется
    при рестарте, чтобы понять, сколько бот реально простаивал."""
    state["last_alive_ts"] = now_ts()
    if not TELEGRAM_COMMANDS_ENABLED:
        save_state(state)
        return
    try:
        offset = state.get("telegram_last_update_id", 0) + 1
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": timeout_sec},
            timeout=timeout_sec + 10,
        )
        r.raise_for_status()
        updates = r.json().get("result", [])
        for upd in updates:
            state["telegram_last_update_id"] = upd.get("update_id", state.get("telegram_last_update_id", 0))
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            text = (msg.get("text") or "").strip()
            if text and chat_id == str(TELEGRAM_CHAT_ID):
                handle_telegram_command(text, state)
    except Exception as e:
        safe_log(f"⚠️ Telegram-опрос команд: {e}")
    finally:
        save_state(state)

def smart_sleep(state: Dict[str, Any], total_seconds: float) -> None:
    """Замена time.sleep() — спит те же total_seconds, но частями по
    TELEGRAM_POLL_CHUNK_SEC, между частями проверяя команды из
    Telegram. Снаружи ведёт себя как обычный sleep (блокирует
    примерно на total_seconds), просто отзывчивый к командам."""
    if not TELEGRAM_COMMANDS_ENABLED:
        time.sleep(max(0.0, total_seconds))
        return
    end = time.time() + max(0.0, total_seconds)
    while True:
        remain = end - time.time()
        if remain <= 0.5:
            break
        poll_telegram_commands(state, timeout_sec=min(TELEGRAM_POLL_CHUNK_SEC, max(1, int(remain))))

def _register_loop_error(state: Dict[str, Any], err_text: str) -> None:
    """NEW v13: копит ошибки подряд, шлёт ОДНО предупреждение в
    Telegram после ERROR_ALERT_THRESHOLD подряд неудач (не на каждую —
    иначе спам при длинном сбое сети/API), запоминает last_error
    для отчёта при следующем рестарте."""
    state["last_error"] = f"{now_str()}: {err_text}"[:300]
    state["consecutive_error_count"] = state.get("consecutive_error_count", 0) + 1
    save_state(state)
    if (state["consecutive_error_count"] >= ERROR_ALERT_THRESHOLD
            and not state.get("error_alert_active")):
        tg_send(
            f"⚠️ У бота проблемы ({state['consecutive_error_count']} ошибок подряд):\n"
            f"{err_text}\n"
            f"Продолжаю пытаться сам. Если не восстановится — сообщу при рестарте."
        )
        state["error_alert_active"] = True
        save_state(state)

def send_daily_summary(date_str: str, signals_sent: int) -> None:
    """NEW v13: итоги ПРОШЕДШЕГО дня, шлётся один раз при смене даты
    (см. reset_daily_if_needed) — «в конце дня сколько сигналов и
    сколько зашло», как просили."""
    wins, losses = _count_results_since(date_str)
    checked = wins + losses
    pending = max(0, signals_sent - checked)
    rate    = f"{wins/checked*100:.0f}%" if checked else "—"
    msg = (
        f"📊 ИТОГИ ДНЯ {date_str}\n"
        f"{'─'*28}\n"
        f"Сигналов: {signals_sent}\n"
        f"✅ Побед: {wins}\n"
        f"❌ Поражений: {losses}\n"
        f"Win rate: {rate}"
    )
    if pending:
        msg += f"\n⏳ Не успело проверится: {pending} (проверю позже, попадёт в статистику завтрашнего дня)"
    tg_send(msg)


# ═══════════════════════════════════════════════════════════════
# ВОССТАНОВЛЕНИЕ ПОСЛЕ СБОЯ / ПЕРЕЗАПУСКА
# ═══════════════════════════════════════════════════════════════

def startup_recovery(state: Dict[str, Any]) -> Dict[str, Any]:
    today   = today_str()
    plan    = state.get("plan", [])
    now     = now_dt()

    result = {
        "is_recovery":        False,
        "currently_active":   [],
        "need_result_check":  [],
        "missed_windows":     [],
        "upcoming":           [],
    }

    if state.get("plan_date") != today or not plan:
        return result

    result["is_recovery"] = True
    sent_ids  = set(state.get("sent_per_match", {}).keys())

    for p in plan:
        start       = parse_iso(p["start_iso"])
        active_from = start + timedelta(minutes=ACTIVE_FROM_MIN)
        active_to   = start + timedelta(minutes=ACTIVE_TO_MIN)
        check_after = start + timedelta(minutes=MATCH_RESULT_CHECK_AFTER_MIN)
        fid_str     = str(p["fixture_id"])

        if now < active_from:
            result["upcoming"].append(p)
        elif active_from <= now <= active_to:
            elapsed = int((now - start).total_seconds() / 60)
            p_copy = dict(p)
            p_copy["_elapsed"] = elapsed
            result["currently_active"].append(p_copy)
        elif active_to < now < check_after:
            if fid_str in sent_ids:
                result["need_result_check"].append(p)
            else:
                result["missed_windows"].append(p)
        else:
            if fid_str in sent_ids:
                result["need_result_check"].append(p)
            else:
                result["missed_windows"].append(p)

    return result


def send_recovery_report(rec: Dict[str, Any], state: Dict[str, Any]) -> None:
    open_bets = state.get("open_bets", {})
    pending   = sum(1 for b in open_bets.values() if not b.get("result_checked"))

    lines = [
        "🔄 БОТ ПЕРЕЗАПУЩЕН",
        f"{'─'*30}",
        f"📡 API использовано: {api_calls_used(state)}/{API_DAILY_LIMIT}",
        f"🧾 Открытых ставок:  {pending}",
        "",
    ]

    # NEW v13: последняя известная ошибка перед остановкой, если была
    last_err = state.get("last_error", "")
    if last_err:
        lines.append(f"⚠️ Последняя ошибка перед остановкой: {last_err}")
        lines.append("")

    if rec["currently_active"]:
        lines.append("⚡ СЕЙЧАС В АКТИВНОМ ОКНЕ (опрашиваем немедленно!):")
        for p in rec["currently_active"]:
            elapsed = p.get("_elapsed", "?")
            lines.append(
                f"  🔴 {p['home']} vs {p['away']}"
                f"  ~{elapsed} мин | [{p['league']}]"
            )
        lines.append("")

    if rec["need_result_check"]:
        lines.append("🔍 НУЖНО ПРОВЕРИТЬ РЕЗУЛЬТАТ:")
        for p in rec["need_result_check"]:
            lines.append(f"  • {p['home']} vs {p['away']}")
        lines.append("")

    if rec["missed_windows"]:
        lines.append("😔 ПРОПУЩЕНО ВО ВРЕМЯ ПРОСТОЯ (сигналов не было):")
        for p in rec["missed_windows"]:
            lines.append(f"  • {p['home']} vs {p['away']} [{p['league']}]")
        lines.append("")

    if rec["upcoming"]:
        lines.append(f"📅 ЕЩЁ ПРЕДСТОИТ: {len(rec['upcoming'])} матч(ей)")
        for p in rec["upcoming"][:5]:
            mins = int((parse_iso(p["start_iso"]) +
                        timedelta(minutes=ACTIVE_FROM_MIN) - now_dt()
                        ).total_seconds() / 60)
            lines.append(f"  ⏰ {p['home']} vs {p['away']}  (окно через {mins} мин)")

    if rec["currently_active"]:
        lines.append("\n⚡ Начинаю опрос live немедленно!")
    elif rec["need_result_check"]:
        lines.append("\n🔍 Проверяю результаты ставок...")
    else:
        lines.append("\n✅ Продолжаю работу в штатном режиме.")

    tg_send("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    safe_log("🚀 Бот запускается — v19 (SIGNAL_STRATEGY=" + SIGNAL_STRATEGY + ")")
    state = load_state()
    ensure_csv()

    # v19: фоновый keep-alive — держит сессию 7777.md живой НЕПРЕРЫВНО,
    # с самого старта бота, а не только в момент сигнала. Отвечает на
    # вопрос "что сделано против авто-разлогина" — этот поток крутится
    # параллельно основному циклу всё время, пока жив бот.
    if AUTO_BET_ENABLED and AUTO_BET_AVAILABLE:
        try:
            start_keepalive()
            safe_log("🟢 Keep-alive поток для 7777.md запущен.")
        except Exception as e:
            safe_log(f"⚠️ Не смог запустить keep-alive: {e}")
        if AUTO_BET_MODULE_DRY_RUN:
            safe_log("🧪 auto_bet_7777: DRY_RUN=True — реальные ставки НЕ выставляются, только лог/Telegram.")

    safe_log("🔑 Проверяем API ключи...")
    key_statuses = validate_api_keys(state)

    valid_keys   = [s for s in key_statuses if s["status"] == "OK"]

    key_report_lines = ["🔑 СТАТУС API КЛЮЧЕЙ"]
    for s in key_statuses:
        icon = {"OK":"✅","NO_KEY":"⬜","INVALID":"❌","BLOCKED":"🚫",
                "EXHAUSTED":"🔴","ERROR":"⚠️"}.get(s["status"],"❓")
        key_report_lines.append(f"  {icon} {s['label']}: {s['msg']}")

    if not valid_keys:
        msg = "\n".join(key_report_lines) + "\n\n❌ НЕТ РАБОЧИХ КЛЮЧЕЙ! Бот остановлен."
        safe_log(msg)
        tg_send(msg)
        return

    exhausted = [s for s in key_statuses if s["status"] == "EXHAUSTED"]
    if exhausted:
        key_report_lines.append("⚠️ Исчерпанные ключи не будут использоваться сегодня.")

    if AI_CONFIRM_ENABLED and not ANTHROPIC_SDK_AVAILABLE:
        key_report_lines.append("⚠️ Claude AI включён в настройках, но пакет 'anthropic' не установлен (pip install anthropic).")
    elif AI_CONFIRM_ENABLED and not ANTHROPIC_API_KEY:
        key_report_lines.append("⚠️ Claude AI включён, но не задан ANTHROPIC_API_KEY — работаем без ИИ-подтверждения.")

    tg_send("\n".join(key_report_lines))
    safe_log(f"🔑 Рабочих ключей: {len(valid_keys)}/{len(key_statuses)}")

    global API_KEYS
    working = []
    all_raw = [APISPORTS_KEY, APISPORTS_KEY_2]
    for idx_k, st in enumerate(key_statuses):
        if st["status"] == "OK" and idx_k < len(all_raw):
            working.append(all_raw[idx_k])
    API_KEYS = working

    if not API_KEYS:
        tg_send("❌ Все ключи исчерпаны или заблокированы. Жду UTC-полночи.")
        now_utc  = datetime.now(timezone.utc)
        midnight = (now_utc + timedelta(days=1)).replace(
                       hour=0, minute=5, second=0, microsecond=0)
        smart_sleep(state, max(60, int((midnight - now_utc).total_seconds())))
        API_KEYS = [k for k in all_raw if _is_valid_key(k)]

    safe_log(f"🔑 Активных ключей в работе: {len(API_KEYS)}")

    # NEW v13: отчёт о простое — НЕЗАВИСИМО от того, есть ли план на
    # сегодня (раньше startup_recovery() молчал, если рестарт пришёлся
    # на смену дня — простой оставался незамеченным).
    prev_alive_ts = state.get("last_alive_ts", 0)
    if prev_alive_ts:
        gap_min = int((now_ts() - prev_alive_ts) / 60)
        if gap_min >= DOWNTIME_ALERT_THRESHOLD_MIN:
            downtime_msg = f"⏱ Бот не отвечал примерно {gap_min} мин."
            last_err = state.get("last_error", "")
            if last_err:
                downtime_msg += f"\nПоследняя известная ошибка: {last_err}"
            tg_send(downtime_msg)

    rec = startup_recovery(state)

    if rec["is_recovery"]:
        safe_log("🔄 Обнаружен перезапуск после сбоя — анализируем состояние...")
        send_recovery_report(rec, state)
        if state.get("open_bets"):
            safe_log("🔍 Немедленная проверка результатов открытых ставок...")
            check_finished_bets(state)
    else:
        commands_line = ("✅ ВКЛ (напиши «жив», «когда» или «итоги»)"
                          if TELEGRAM_COMMANDS_ENABLED else "❌ выкл")
        tg_send(
            f"✅ Бот запущен v19 [{SIGNAL_STRATEGY}]\n"
            f"📡 API: {API_DAILY_LIMIT} запросов/день\n"
            f"⏱ Live: каждые {POLL_SECONDS_ACTIVE}с (только в окне матча!)\n"
            f"🎯 Сигнал: {MIN_MINUTE}–{MAX_MINUTE} мин\n"
            f"🏆 Лиг: {len(LEAGUE_IDS)}+\n"
            f"🤖 Авто-ставка: {'✅ ВКЛ' if AUTO_BET_ENABLED else '❌ ВЫКЛ'}\n"
            f"🧠 Claude AI: {'✅ ВКЛ (' + AI_CONFIRM_MODE + ')' if ANTHROPIC_AVAILABLE else '❌ выкл/не настроен'}\n"
            f"💬 Команды в Telegram: {commands_line}"
        )

    last_result_check = 0.0
    last_heartbeat    = 0.0

    try:
        while True:
            try:
                reset_daily_if_needed(state)
                now = now_dt()

                # NEW v13: успешно дошли до начала итерации — сбрасываем
                # счётчик подряд идущих ошибок; если до этого был активный
                # алерт о проблемах — сообщаем, что восстановился.
                if state.get("consecutive_error_count", 0) > 0:
                    if state.get("error_alert_active"):
                        tg_send("✅ Бот восстановился после сбоя, работаю дальше в штатном режиме.")
                    state["consecutive_error_count"] = 0
                    state["error_alert_active"] = False
                    state["last_error"] = ""
                    save_state(state)

                # ── 1. HEARTBEAT (0 API) ───────────────────────────────
                if HEARTBEAT_ENABLED and now_ts() - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    send_heartbeat(state, state.get("plan", []))
                    last_heartbeat = now_ts()

                # ── 1b. ПРОВЕРКА РЕЗУЛЬТАТОВ (BUG-FIX 1) ───────────────
                # Раньше это было спрятано внутри веток "активное окно"/
                # "сон" и становилось недостижимым при лимите сигналов
                # или пустом плане. Теперь выполняется безусловно, пока
                # есть незакрытые ставки и API-бюджет не исчерпан.
                if now_ts() - last_result_check >= RESULT_RETRY_INTERVAL_MIN * 60:
                    has_pending = any(
                        not b.get("result_checked")
                        for b in state.get("open_bets", {}).values()
                    )
                    if has_pending and api_calls_used(state) < API_DAILY_LIMIT:
                        check_finished_bets(state)
                    last_result_check = now_ts()

                # ── 2. ПЛАН — сборка + обновления в течение дня (BUG-FIX 17) ──
                # Пересобираем когда: новый день ВСЕГДА; иначе — не чаще
                # раза в PLAN_REFRESH_INTERVAL_SEC и не больше
                # PLAN_REFRESH_MAX_PER_DAY раз/день. Единый механизм и для
                # пустого плана (ловит поздно добавленные матчи), и для
                # непустого (дозагружает новые матчи дня). Полная пересборка
                # безопасна: sent_per_match/open_bets отдельно, сигналы не
                # повторяются.
                today          = today_str()
                plan_is_new_day = state.get("plan_date") != today

                should_rebuild_plan = plan_is_new_day
                if not plan_is_new_day:
                    refreshes = state.get("plan_refreshes_today", 0)
                    last_bld  = state.get("plan_last_build_ts", 0)
                    if (refreshes < PLAN_REFRESH_MAX_PER_DAY
                            and now_ts() - last_bld >= PLAN_REFRESH_INTERVAL_SEC):
                        should_rebuild_plan = True

                if should_rebuild_plan:
                    try:
                        was_empty_before = not state.get("plan")
                        plan = build_24h_plan(state)
                        state["plan"]               = plan
                        state["plan_date"]          = today
                        state["plan_retries_today"] = 0
                        state["plan_last_build_ts"] = now_ts()
                        if not plan_is_new_day:
                            state["plan_refreshes_today"] = state.get("plan_refreshes_today", 0) + 1
                        save_state(state)
                        # Telegram: сообщаем при первой сборке дня, либо когда
                        # обновление нашло матчи там, где их не было (полезная
                        # новость). Не спамим "не найдено" при каждом обновлении.
                        if plan_is_new_day or (plan and was_empty_before):
                            send_plan_to_telegram(plan)
                        ai_daily_review(state)
                        # DATA-FIX 10: окно 68-90 при опросе раз в 110с — до ~12
                        # live-запросов на непересекающееся окно (было ~20 при
                        # старом узком окне и опросе раз в 90с).
                        polls_per_window = max(1, (ACTIVE_TO_MIN - ACTIVE_FROM_MIN) * 60 // POLL_SECONDS_ACTIVE)
                        safe_log(
                            f"📊 Бюджет на день (грубая оценка): 2 план + "
                            f"до {polls_per_window} live-запросов на непересекающееся окно + итоги"
                        )
                    except Exception as plan_err:
                        # BUG-FIX 17: если это было ПЕРИОДИЧЕСКОЕ обновление
                        # непустого плана и API дал сбой — НЕ стираем рабочий
                        # план, просто пропускаем это обновление и живём на
                        # старом плане. Wipe допустим только при первой сборке
                        # дня (защищать нечего).
                        if not plan_is_new_day and state.get("plan"):
                            state["plan_last_build_ts"]  = now_ts()  # отложить следующую попытку
                            state["plan_refreshes_today"] = state.get("plan_refreshes_today", 0) + 1
                            save_state(state)
                            safe_log(f"⚠️ Обновление плана не удалось ({plan_err}) — работаю на текущем плане.")
                        else:
                            plan_retries = state.get("plan_retries_today", 0) + 1
                            state["plan_retries_today"] = plan_retries
                            state["plan"] = []
                            state["plan_date"] = ""
                            save_state(state)
                            if plan_retries >= 3:
                                safe_log("❌ 3 неудачи плана → жду нового дня")
                                tg_send("❌ План не строится. Жду новый день.")
                                state["plan_date"] = today
                                save_state(state)
                                smart_sleep(state, SLEEP_CHUNK_SECONDS)
                            else:
                                tg_send(f"⚠️ Ошибка плана ({plan_retries}/3)\nПовтор через 30 мин.")
                                smart_sleep(state, 1800)
                            continue
                        continue

                plan = state.get("plan", [])

                # ── 3. ОБА КЛЮЧА ИСЧЕРПАНЫ → спим до UTC-полночи ────
                if both_keys_exhausted(state):
                    now_utc  = datetime.now(timezone.utc)
                    midnight = (now_utc + timedelta(days=1)).replace(
                                   hour=0, minute=5, second=0, microsecond=0)
                    secs = max(60, int((midnight - now_utc).total_seconds()))
                    k1 = state.get("api_calls_key1", 0)
                    k2 = state.get("api_calls_key2", 0)
                    total_lim = get_total_limit()
                    safe_log(f"🚨 ОБА ключа исчерпаны ({k1}+{k2}/{total_lim}). Сплю до UTC {midnight.strftime('%H:%M')}.")
                    tg_send(f"🚨 Оба ключа ({k1}+{k2}/{total_lim}) исчерпаны. Сплю до UTC-полночи.")
                    smart_sleep(state, secs)
                    continue

                # ── 4. ЛИМИТ СИГНАЛОВ ─────────────────────────────────
                if state.get("signals_today", 0) >= MAX_SIGNALS_PER_DAY:
                    safe_log(f"Лимит {MAX_SIGNALS_PER_DAY} сигналов/день.")
                    smart_sleep(state, SLEEP_CHUNK_SECONDS)
                    continue

                # ── 5. НЕТ ПЛАНА ──────────────────────────────────────
                if not plan:
                    safe_log("План пуст. Ждём 10 мин.")
                    smart_sleep(state, SLEEP_CHUNK_SECONDS)
                    continue

                # ═══════════════════════════════════════════════════════
                # ── 6. АКТИВНОЕ ОКНО? ─────────────────────────────────
                # ═══════════════════════════════════════════════════════
                if is_any_match_active_now(plan):
                    if not can_call_live(state):
                        if both_keys_exhausted(state):
                            continue
                        safe_log(f"⚠️ Переключение не удалось. Пауза.")
                        smart_sleep(state, POLL_SECONDS_ACTIVE)
                        continue

                    k_idx = state.get("active_key_index", 0) + 1
                    safe_log(f"🔴 LIVE [Ключ {k_idx}: {api_calls_used(state)}/{API_DAILY_LIMIT} | Всего: {api_calls_total(state)}/{get_total_limit()}]")
                    live_fixtures = get_live_fixtures(state)
                    plan_ids = {p["fixture_id"] for p in plan}

                    for fx in live_fixtures:
                        if not is_target_competition(fx):
                            continue

                        fid = get_fixture_id(fx)
                        if fid <= 0:
                            continue

                        if fid not in plan_ids:
                            safe_log(f"ℹ️ fixture {fid} не в плане — обрабатываем как fallback")

                        minute = get_match_minute(fx)
                        if minute < MIN_MINUTE or minute > MAX_MINUTE:
                            continue

                        sent_count = state["sent_per_match"].get(str(fid), 0)
                        if sent_count >= MAX_SIGNALS_PER_MATCH:
                            continue

                        bundle = pick_signal_bundle(fx, minute)
                        if not bundle:
                            continue

                        # ── NEW: подтверждение сигнала через Claude ──────
                        ai_result = ai_confirm_signal(fx, minute, bundle, state)
                        if (ai_result is not None
                                and AI_CONFIRM_MODE == "gate"
                                and ai_result["probability"] < AI_MIN_PROBABILITY):
                            safe_log(
                                f"🤖 AI отклонил сигнал {fid} "
                                f"({ai_result['probability']:.2f} < {AI_MIN_PROBABILITY}): {ai_result['reason']}"
                            )
                            continue

                        msg = build_bundle_message(fx, minute, bundle, ai_result)
                        tg_send(msg)

                        home, away         = get_team_names(fx)
                        league_name, cntry = get_league_info(fx)
                        h, a, _            = current_score(fx)
                        score_now          = f"{h}-{a}"

                        # v19: place_bet_from_signal() возвращает подробный
                        # статус (не bool) — детальные сообщения в Telegram,
                        # как просили: поставлена/не удалось/не нашёл/
                        # заблокирована/не залогинен/тест-режим.
                        auto_bet_result = None
                        if AUTO_BET_ENABLED and AUTO_BET_AVAILABLE:
                            try:
                                bundle_with_teams = {**bundle, "home": home, "away": away}
                                auto_bet_result = place_bet_from_signal(bundle_with_teams)
                            except Exception as e:
                                safe_log(f"Авто-ставка: исключение {e}")
                                auto_bet_result = {"status": "ERROR", "detail": str(e),
                                                    "stake": 0.0, "odds": None}
                            tg_send(describe_auto_bet_result(auto_bet_result))
                        else:
                            tg_send("ℹ️ Авто-ставка ВЫКЛЮЧЕНА — поставь вручную!")

                        plan_item = next((p for p in plan if p["fixture_id"] == fid), None)
                        if plan_item:
                            match_start_iso = plan_item["start_iso"]
                        else:
                            fx_start = parse_fixture_start(fx)
                            match_start_iso = fx_start.isoformat() if fx_start else None

                        bet_id = f"{fid}-{int(now_ts())}"
                        state["open_bets"][bet_id] = {
                            "bet_id":          bet_id,
                            "time":            now_str(),
                            "fixture_id":      fid,
                            "league":          league_name,
                            "country":         cntry,
                            "home":            home,
                            "away":            away,
                            "minute":          minute,
                            "score":           score_now,
                            "bet_type":        bundle["main"]["type"],
                            "line":            bundle["main"]["line"],
                            "bets":            [bundle["main"]] + (bundle.get("extras") or []),
                            "notes":           bundle["main"].get("why", ""),
                            "match_start_iso": match_start_iso,
                            "result_checked":  False,
                            "last_check_ts":   0,
                            "ai_probability":  ai_result["probability"] if ai_result else "",
                            # v19: данные авто-ставки — нужны, чтобы при
                            # расчёте результата посчитать реальный выигрыш
                            # (а не просто WIN/LOSE без суммы).
                            "auto_bet_status": (auto_bet_result or {}).get("status", ""),
                            "auto_bet_stake":  (auto_bet_result or {}).get("stake", 0.0),
                            "auto_bet_odds":   (auto_bet_result or {}).get("odds"),
                        }
                        state["sent_per_match"][str(fid)] = sent_count + 1
                        state["signals_today"]            = state.get("signals_today", 0) + 1
                        save_state(state)

                        safe_log(f"✅ Сигнал: {home} vs {away} | {minute}' | {score_now} "
                                 f"[API: {api_calls_used(state)}/{API_DAILY_LIMIT}]")

                        if state["signals_today"] >= MAX_SIGNALS_PER_DAY:
                            break

                    smart_sleep(state, POLL_SECONDS_ACTIVE)

                else:
                    # ═══════════════════════════════════════════════════
                    # ── 7. НЕТ АКТИВНОГО ОКНА → УМНЫЙ СОН (0 API!) ───
                    # ═══════════════════════════════════════════════════
                    nxt = next_activation_time(plan)

                    if nxt is None:
                        safe_log("📭 Все окна завершены. Сон 10 мин.")
                        smart_sleep(state, SLEEP_CHUNK_SECONDS)
                        continue

                    secs_to_next = max(5, int((nxt - now_dt()).total_seconds()))
                    sleep_secs = min(secs_to_next, RESULT_RETRY_INTERVAL_MIN * 60)

                    if secs_to_next > 60:
                        wake_at = now + timedelta(seconds=sleep_secs)
                        safe_log(
                            f"😴 Сон {sleep_secs//60}м до "
                            f"{wake_at.strftime('%H:%M:%S')} "
                            f"(следующее окно через {secs_to_next//60}м) "
                            f"[API: {api_calls_used(state)}/{API_DAILY_LIMIT}]"
                        )
                    smart_sleep(state, sleep_secs)

            except requests.HTTPError as e:
                safe_log(f"HTTPError: {e}")
                _register_loop_error(state, str(e))
                smart_sleep(state, POLL_SECONDS_ACTIVE)
            except Exception as e:
                safe_log(f"ERROR: {e}")
                import traceback
                traceback.print_exc()
                _register_loop_error(state, str(e))
                if both_keys_exhausted(state):
                    now_utc  = datetime.now(timezone.utc)
                    midnight = (now_utc + timedelta(days=1)).replace(
                                   hour=0, minute=5, second=0, microsecond=0)
                    secs = max(60, int((midnight - now_utc).total_seconds()))
                    smart_sleep(state, secs)
                elif api_calls_used(state) >= API_DAILY_LIMIT:
                    try_switch_key(state)
                else:
                    smart_sleep(state, POLL_SECONDS_ACTIVE)

    finally:
        safe_log("🔴 Бот останавливается...")
        try:
            stop_keepalive()
        except Exception:
            pass
        try:
            close_driver()
        except Exception:
            pass


if __name__ == "__main__":
    main()
