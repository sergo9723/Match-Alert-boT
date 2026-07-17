# auto_bet_7777.py — АВТО-СТАВКА НА 7777.md
# ═══════════════════════════════════════════════════════════════
# ⚠️ ЧЕСТНО О СТАТУСЕ ЭТОГО ФАЙЛА:
#
# По умолчанию AUTO_BET_DRY_RUN = True — код НИЧЕГО не кликает и не
# ставит реальными деньгами. Он ищет матч и рынок, ЛОГИРУЕТ и ШЛЁТ
# В TELEGRAM, что бы сделал — и на этом останавливается.
#
# Почему: точные селекторы кнопки исхода, поля суммы и кнопки
# подтверждения в купоне — НЕ откалиброваны (нужен вывод
# inspect_7777.py --mode discover С ОТКРЫТЫМ КУПОНОМ, который мне
# ещё не прислали). Без этого включать реальные клики реальными
# деньгами — безответственно, поэтому логика клика ниже — лучшее
# приближение по тому, что мы уже разведали (market-vis-type-name,
# data-widget="betslip"), но НЕ проверена вживую.
#
# ПУТЬ К ВКЛЮЧЕНИЮ РЕАЛЬНЫХ СТАВОК:
#   1. Прогони inspect_7777.py --mode discover с открытым купоном
#      (кликни коэффициент руками перед запуском).
#   2. Пришли discover_*.json и discover_*.txt — я вставлю точные
#      селекторы вместо эвристик ниже (см. МЕТКИ "TODO-CALIBRATE").
#   3. Пришли результат inspect_7777.py --mode keepalive (30+ мин) —
#      подтвердить, что сессия реально не разлогинивается сама.
#   4. Только после этого — AUTO_BET_DRY_RUN = False.
#
# ЧТО УЖЕ РАБОТАЕТ И ПРОТЕСТИРОВАНО (см. отчёт после кода):
#   - определение "залогинен / вышел" по тексту страницы
#   - фоновый keep-alive поток (держит сессию, не блокирует бота)
#   - нечёткий поиск матча по именам команд
#   - разбор bundle-сигнала в текст рынка/исхода для поиска на бирже
#   - вся структура статусов и Telegram-сообщений
#   - логирование в файл для последующего анализа
# ═══════════════════════════════════════════════════════════════

import difflib
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

try:
    import requests
except ImportError:
    requests = None

# ═══════════════════════════════════════════════════════════════
# НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════════

# ⚠️ ГЛАВНЫЙ ПРЕДОХРАНИТЕЛЬ. Не трогай, пока не откалибровано (см. шапку).
AUTO_BET_DRY_RUN = True

STAKE_AMOUNT   = 5.0          # лей на ставку, фикс
CHROME_PORT    = 9222
LIVE_LIST_URL  = "https://7777.md/sport_live/#live"   # общий live-список для поиска матча

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(DATA_DIR, "auto_bet.log")

# Telegram — берём из тех же переменных окружения, что и sport_bot,
# чтобы модуль был самодостаточным и при отдельном запуске/тесте.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# Keep-alive: как часто делать микро-действие, сбрасывающее таймер
# бездействия сайта. Должно быть заметно меньше типичного таймаута
# (обычно 5-15 минут у большинства букмекеров).
KEEPALIVE_INTERVAL_SEC = 45

# Порог уверенности нечёткого поиска команд (0..1). Было 0.55 на
# среднем — после фикса безопасности (см. _name_similarity, тест
# поймал ложное сближение "Manchester United"/"Manchester City")
# используется MIN(совпадение хозяев, гостей), метрика строже —
# подняли порог до 0.65: лучше сказать "матч не найден" и пропустить
# ставку, чем поставить на другой матч с похожим названием.
MIN_MATCH_MATCH_RATIO = 0.65

LOGGED_IN_MARKERS = [
    "пополнить", "депозит", "вывод", "выход", "мой профиль",
    "личный кабинет", "баланс", "mdl", "кабинет", "мои ставки",
]
LOGGED_OUT_MARKERS = [
    "войти", "вход", "регистрация", "log in", "sign in", "авторизация",
]

# ═══════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════

_logger = logging.getLogger("auto_bet_7777")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(sh)


def log(msg: str) -> None:
    _logger.info(msg)


def tg_send(text: str) -> None:
    """Независимый от sport_bot Telegram-отправитель — модуль должен
    уметь слать статусы, даже если его тестируют отдельно."""
    if not (requests and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        log(f"TG (не отправлено, нет токена/requests): {text}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for attempt in range(1, 4):
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=25).raise_for_status()
            return
        except Exception as e:
            log(f"TG ошибка (попытка {attempt}/3): {e}")
            if attempt < 3:
                time.sleep(5)


# ═══════════════════════════════════════════════════════════════
# ПОДКЛЮЧЕНИЕ К БРАУЗЕРУ
# ═══════════════════════════════════════════════════════════════

_driver = None
_driver_lock = threading.Lock()


def get_driver():
    """Подключаемся к уже открытому и залогиненному Chrome (порт 9222).
    Не логинимся сами, пароль никогда не касается кода."""
    global _driver
    with _driver_lock:
        if _driver is not None:
            return _driver
        if not SELENIUM_AVAILABLE:
            raise RuntimeError("selenium не установлен (pip install selenium)")
        opts = Options()
        opts.debugger_address = f"127.0.0.1:{CHROME_PORT}"
        _driver = webdriver.Chrome(options=opts)
        log(f"Подключился к Chrome на порту {CHROME_PORT}")
        return _driver


def close_driver() -> None:
    """Вызывается ботом при остановке — НЕ закрывает реальное окно
    Chrome (это было бы разрушительно для твоей сессии), просто
    отсоединяется от WebDriver-сессии."""
    global _driver
    with _driver_lock:
        if _driver is not None:
            try:
                _driver.quit()
            except Exception:
                pass
            # quit() у attach-сессии (debugger_address) НЕ закрывает
            # реальное окно Chrome — оно только освобождает WebDriver.
            # Это поведение Selenium, специально используем.
            _driver = None
            log("Отсоединился от Chrome (окно осталось открытым).")


# ═══════════════════════════════════════════════════════════════
# СОСТОЯНИЕ ЛОГИНА / KEEP-ALIVE
# ═══════════════════════════════════════════════════════════════

def detect_login_state(driver) -> str:
    """'IN' | 'OUT' | 'UNKNOWN' по видимому тексту страницы.
    ⚠️ TODO-CALIBRATE: эвристика по словам, не по надёжному DOM-маркеру
    (например data-testid="user-balance") — уточнить после discover."""
    try:
        text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception as e:
        log(f"⚠️ Не смог прочитать страницу для проверки логина: {e}")
        return "UNKNOWN"
    out_hit = any(m in text for m in LOGGED_OUT_MARKERS)
    in_hit = any(m in text for m in LOGGED_IN_MARKERS)
    if in_hit and not out_hit:
        return "IN"
    if out_hit and not in_hit:
        return "OUT"
    return "UNKNOWN"


def _keepalive_tick(driver) -> None:
    try:
        ActionChains(driver).move_by_offset(2, 0).move_by_offset(-2, 0).perform()
    except Exception:
        pass
    try:
        driver.execute_script("window.scrollBy(0,2); setTimeout(function(){window.scrollBy(0,-2);}, 60);")
    except Exception:
        pass


class KeepAliveThread(threading.Thread):
    """Фоновый поток: держит сессию 7777.md живой (микро-действия каждые
    KEEPALIVE_INTERVAL_SEC), и следит за состоянием логина. При выходе
    из профиля — шлёт ОДНО громкое предупреждение в Telegram (не спамит
    на каждую проверку), и ещё одно, когда залогинишься заново.
    Отвечает на вопрос "что сделано против авто-разлогина" — этот
    поток крутится непрерывно, пока жив главный бот, независимо от
    того, идёт сейчас сигнал или нет."""

    def __init__(self, interval_sec: int = KEEPALIVE_INTERVAL_SEC):
        super().__init__(daemon=True)
        self.interval_sec = interval_sec
        self._stop_flag = threading.Event()
        self._last_state: Optional[str] = None

    def stop(self) -> None:
        self._stop_flag.set()

    def run(self) -> None:
        log(f"🟢 Keep-alive поток запущен (проверка каждые {self.interval_sec}с)")
        while not self._stop_flag.is_set():
            try:
                driver = get_driver()
                _keepalive_tick(driver)
                state = detect_login_state(driver)
                if state != self._last_state:
                    if state == "OUT":
                        log("🚨 ВЫШЕЛ ИЗ ПРОФИЛЯ на 7777.md")
                        tg_send(
                            "🚨 БИРЖА: ты вышел из профиля на 7777.md!\n"
                            "Авто-ставки НЕ будут проходить, пока не зайдёшь заново.\n"
                            "Открой окно Chrome (порт 9222) и залогинься."
                        )
                    elif state == "IN" and self._last_state == "OUT":
                        log("✅ Залогинен снова.")
                        tg_send("✅ Снова залогинен на 7777.md — авто-ставки возобновлены.")
                    self._last_state = state
            except Exception as e:
                log(f"⚠️ Keep-alive ошибка цикла: {e}")
            self._stop_flag.wait(self.interval_sec)
        log("🔴 Keep-alive поток остановлен.")


_keepalive_thread: Optional[KeepAliveThread] = None


def start_keepalive() -> None:
    global _keepalive_thread
    if _keepalive_thread is None or not _keepalive_thread.is_alive():
        _keepalive_thread = KeepAliveThread()
        _keepalive_thread.start()


def stop_keepalive() -> None:
    global _keepalive_thread
    if _keepalive_thread is not None:
        _keepalive_thread.stop()
        _keepalive_thread = None


# ═══════════════════════════════════════════════════════════════
# ПОИСК МАТЧА ПО ИМЕНАМ КОМАНД (нечёткий, т.к. написание отличается
# от API-Sports — эту проблему разбирали ещё в начале работы)
# ═══════════════════════════════════════════════════════════════

def _normalize_team_name(name: str) -> str:
    name = name.lower().strip()
    # Дефисы/точки/подчёркивания -> ПРОБЕЛ, а не просто удаление —
    # тест поймал баг: "Ararat-Armenia" схлопывалось в одно слово
    # "araratarmenia" и переставало совпадать с "Ararat Armenia".
    name = re.sub(r"[-_.]", " ", name)
    name = re.sub(r"\b(fc|sc|afc|cf|u1[6-9]|u2[0-3])\b", "", name)
    name = re.sub(r"[^a-zа-яё0-9 ]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _name_similarity(a: str, b: str) -> float:
    """
    0..1, где 1 = совпадение. НАМЕРЕННО НЕ обычный SequenceMatcher по
    всей строке — тест это поймал: 'Manchester United' vs 'Manchester
    City' даёт 0.81 у "наивного" SequenceMatcher (общий префикс
    'manchester' перевешивает разное окончание) — с реальными деньгами
    это значило бы риск поставить на другой матч. Здесь — по словам:
    точное совпадение слов ИЛИ вхождение слов одного имени в другое
    (для сокращений вроде 'Sabah'/'Sabah FA') → высокий балл; иначе —
    доля общих слов (Jaccard), что справедливо режет 'Manchester
    United' vs 'Manchester City' (общее только 'manchester' из 3 слов).
    """
    na, nb = _normalize_team_name(a), _normalize_team_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    set_a, set_b = set(na.split()), set(nb.split())
    if set_a and set_b and (set_a <= set_b or set_b <= set_a):
        return 0.9
    return len(set_a & set_b) / len(set_a | set_b) if (set_a and set_b) else 0.0


def _team_in_text(team_name: str, text: str) -> float:
    """Насколько ПОЛНО слова team_name встречаются внутри text (карточка
    матча содержит доп. слова: 'vs', 'LIVE', счёт, минуту и т.п. —
    поэтому здесь не Jaccard/симметричное сравнение, а доля слов
    команды, найденных в тексте карточки)."""
    team_words = set(_normalize_team_name(team_name).split())
    text_words = set(_normalize_team_name(text).split())
    if not team_words:
        return 0.0
    return len(team_words & text_words) / len(team_words)


def find_match_by_teams(driver, home: str, away: str) -> Dict[str, Any]:
    """
    Ищет матч по именам команд на общей live-странице 7777.md.
    ⚠️ TODO-CALIBRATE: пока это ОБЩИЙ текстовый поиск по всей странице
    (ищем блок, где рядом встречаются оба имени), а не точный селектор
    карточки матча — потому что у меня нет discover-данных именно
    страницы списка live-матчей. Работает как лучшее приближение,
    нужно проверить вживую.

    БЕЗОПАСНОСТЬ: используется MIN(совпадение хозяев, совпадение
    гостей), а не среднее — совпадения только ОДНОЙ из двух команд
    недостаточно, чтобы посчитать матч найденным (иначе легко
    промахнуться на другой матч с похожим названием одной из команд).

    Возвращает {"found": bool, "confidence": float, "element": WebElement|None}
    """
    try:
        driver.get(LIVE_LIST_URL)
        time.sleep(4)
    except Exception as e:
        log(f"⚠️ Не смог открыть список live-матчей: {e}")
        return {"found": False, "confidence": 0.0, "element": None}

    try:
        candidates = driver.find_elements(By.XPATH, "//*[not(self::script) and not(self::style)]")
    except Exception as e:
        log(f"⚠️ Не смог собрать элементы списка: {e}")
        return {"found": False, "confidence": 0.0, "element": None}

    best = {"found": False, "confidence": 0.0, "element": None}

    for el in candidates:
        try:
            txt = (el.text or "").strip()
        except Exception:
            continue
        if not txt or len(txt) > 200:
            continue
        sim_home = _team_in_text(home, txt)
        sim_away = _team_in_text(away, txt)
        conf = min(sim_home, sim_away)   # обе команды должны найтись, не одна
        if conf > best["confidence"]:
            best = {"found": conf >= MIN_MATCH_MATCH_RATIO, "confidence": conf, "element": el}

    log(f"Поиск матча '{home}' vs '{away}': confidence={best['confidence']:.2f} "
        f"found={best['found']}")
    return best


# ═══════════════════════════════════════════════════════════════
# РАЗБОР BUNDLE → ТЕКСТ РЫНКА/ИСХОДА ДЛЯ ПОИСКА НА БИРЖЕ
# ═══════════════════════════════════════════════════════════════

# Подтверждено реальным разбором HTML 7777.md (market-vis-type-name).
MARKET_NAME_BY_TYPE = {
    "DC_HOME":     "Двойной Шанс",
    "DC_AWAY":     "Двойной Шанс",
    "BTTS_NO":     "Обе Команды Забьют",
    "TOTAL_OVER":  "Тотал",
    "TOTAL_UNDER": "Тотал",
}


# ═══════════════════════════════════════════════════════════════
# КАЛИБРОВКА 17.07 (discover_20260717_142740, реальный матч с открытым
# купоном): исправлены поисковые термы Двойного Шанса — реальный текст
# кнопок "1 или Ничья" / "Ничья или 2", а НЕ "1X"/"X2" (эти буквенные
# коды на сайте не используются вообще — старые термы никогда бы не
# сматчились, т.е. авто-ставка на DC был бы гарантированным NOT_FOUND).
# Для Тотала строка рынка вида "Тотал\nБольше\n2.70\n7.5\nМеньше\n1.40"
# показывает линию (7.5) ОДИН раз в середине — не при каждой кнопке —
# поэтому термы теперь просто "больше"/"меньше", а нужная линия
# сверяется отдельно через _parse_total_line_from_block_text().
# ═══════════════════════════════════════════════════════════════

def _outcome_search_terms(bet_type: str, line: float, home: str, away: str) -> List[str]:
    """Слова, по которым ищем нужную кнопку исхода внутри рынка."""
    if bet_type == "DC_HOME":
        return ["1 или ничья"]
    if bet_type == "DC_AWAY":
        return ["ничья или 2"]
    if bet_type == "BTTS_NO":
        return ["нет"]
    if bet_type == "TOTAL_OVER":
        return ["больше"]
    if bet_type == "TOTAL_UNDER":
        return ["меньше"]
    return []


def _extract_odds_from_text(text: str) -> Optional[float]:
    """Пытается вытащить число-коэффициент из текста кнопки, напр.
    'меньше 2.5 1.83' -> 1.83 (последнее число с точкой)."""
    nums = re.findall(r"\d+\.\d+", text)
    if not nums:
        return None
    try:
        return float(nums[-1])
    except Exception:
        return None


def _parse_total_line_from_block_text(text: str) -> Optional[Tuple[float, float, float]]:
    """
    Разбирает объединённый текст блока рынка "Тотал"
    (пример реальных данных: 'Тотал Больше 2.70 7.5 Меньше 1.40')
    на (коэф_больше, линия, коэф_меньше).

    Линия показывается один раз, между коэффициентом "больше" и
    словом "меньше" — берём её отсюда, а НЕ из текста самой кнопки
    (там линии нет).
    """
    norm = re.sub(r"\s+", " ", (text or "").strip())
    m = re.search(
        r"больше\s+([\d.]+)\s+([\d.]+)\s+меньше\s+([\d.]+)",
        norm, re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return float(m.group(1)), float(m.group(2)), float(m.group(3))
    except ValueError:
        return None


def find_and_click_outcome(driver, bet_type: str, line: float) -> Dict[str, Any]:
    """
    Ищет рынок (market-vis-type-name) и внутри него — кнопку исхода
    по тексту. НЕ кликает в DRY_RUN режиме.

    Для Тотала — дополнительно сверяет, что линия на бирже прямо
    сейчас совпадает с ожидаемой (line), иначе НЕ кликает (защита от
    ставки на не ту линию, если она успела сдвинуться).

    Возвращает {"status": "FOUND_OPEN"|"BLOCKED"|"NOT_FOUND",
                 "element": ..., "odds_text": str, "odds": float|None}
    """
    market_name = MARKET_NAME_BY_TYPE.get(bet_type)
    if not market_name:
        return {"status": "NOT_FOUND", "element": None, "odds_text": "", "odds": None}

    try:
        market_blocks = driver.find_elements(
            By.XPATH, f"//*[@market-vis-type-name='{market_name}']"
        )
    except Exception as e:
        log(f"⚠️ Ошибка поиска рынка '{market_name}': {e}")
        return {"status": "NOT_FOUND", "element": None, "odds_text": "", "odds": None}

    if not market_blocks:
        log(f"Рынок '{market_name}' не найден на странице.")
        return {"status": "NOT_FOUND", "element": None, "odds_text": "", "odds": None}

    terms = [t.lower() for t in _outcome_search_terms(bet_type, line, "", "")]
    is_total = bet_type in ("TOTAL_OVER", "TOTAL_UNDER")

    for block in market_blocks:
        parsed_odds: Optional[float] = None

        if is_total:
            try:
                block_text = block.text
            except Exception:
                block_text = ""
            parsed = _parse_total_line_from_block_text(block_text)
            if parsed is None:
                log(f"Тотал: не смог распарсить линию из текста рынка: {block_text!r}")
                continue
            odds_over, actual_line, odds_under = parsed
            if abs(actual_line - line) > 0.01:
                log(f"Тотал: ожидали линию {line}, на бирже сейчас {actual_line} — "
                    f"пропускаю (защита от ставки не на ту линию).")
                continue
            parsed_odds = odds_over if bet_type == "TOTAL_OVER" else odds_under

        try:
            inner_elements = block.find_elements(By.XPATH, ".//*")
        except Exception:
            continue
        for el in inner_elements:
            try:
                txt = (el.text or "").strip().lower()
            except Exception:
                continue
            if not txt or len(txt) > 60:
                continue
            if any(term in txt for term in terms):
                disabled = False
                try:
                    disabled = (el.get_attribute("disabled") is not None
                                or "lock" in (el.get_attribute("class") or "").lower()
                                or "disabled" in (el.get_attribute("class") or "").lower())
                except Exception:
                    pass

                # Доп. защита: у ОТКРЫТОГО исхода рядом всегда есть цифра
                # коэффициента. Если её нет (ни в самом элементе, ни в
                # родителе) — считаем исход заблокированным, даже если
                # class-эвристика выше не сработала (частый случай для
                # 1X/X2 в Двойном Шансе — они у 7777.md почти всегда 🔒).
                own_odds = _extract_odds_from_text(txt)
                if own_odds is None and not is_total:
                    try:
                        parent_txt = (el.find_element(By.XPATH, "..").text or "").lower()
                        own_odds = _extract_odds_from_text(parent_txt)
                    except Exception:
                        pass
                if is_total:
                    own_odds = parsed_odds

                if disabled or own_odds is None:
                    log(f"Исход '{txt}' найден, но ЗАБЛОКИРОВАН/без коэф. (🔒).")
                    return {"status": "BLOCKED", "element": el, "odds_text": txt, "odds": None}

                log(f"Исход '{txt}' найден и открыт, коэф≈{own_odds}.")
                return {"status": "FOUND_OPEN", "element": el, "odds_text": txt, "odds": own_odds}

    log(f"Рынок '{market_name}' есть, но нужный исход (line={line}) не найден.")
    return {"status": "NOT_FOUND", "element": None, "odds_text": "", "odds": None}


# ═══════════════════════════════════════════════════════════════
# КУПОН: РАЗВОРОТ + СУММА + ПОДТВЕРЖДЕНИЕ (только НЕ в dry-run)
# ═══════════════════════════════════════════════════════════════
#
# КАЛИБРОВКА 17.07 (discover_20260717_142740, дамп с реально открытым
# купоном на 5 лей-подобной ставке "Меньше 7.5"):
#   • data-widget="betslip" В РЕАЛЬНОСТИ НЕ СУЩЕСТВУЕТ — старый селектор
#     контейнера был ошибочным и НИКОГДА бы не сработал. Купон ищем
#     теперь по классам конкретных элементов внутри него.
#   • Поле суммы: <input class="... sp-selection-input ...">.
#   • Заголовок-шапка купона: <div class="sp-bg-bgr-betslip-selection ...">
#     с текстом "N Выбор / Итог. коэф: X" — по скриншотам именно клик
#     по этой шапке разворачивает/сворачивает купон.
#   • Кнопки +100/+500/+1000 — НАДБАВКИ к сумме, не пресеты, не трогаем.
#   • Кнопка ставки: <button class="... sp-bg-btn-betslip-betbtn ...">
#     с текстом "Сделать ставку" (disabled, пока сумма не введена).
#     "Принять изменения" — отдельная кнопка, появляется, только если
#     коэффициент успел измениться между выбором и подтверждением —
#     проверяем оба варианта текста.

BETSLIP_STAKE_INPUT_CLASS = "sp-selection-input"
BETSLIP_HEADER_CLASS = "sp-bg-bgr-betslip-selection"
BETSLIP_CONFIRM_CLASS_HINT = "betbtn"


def _expand_betslip_if_collapsed(driver) -> bool:
    """
    Разворачивает свёрнутый купон кликом по шапке ("N Выбор / Итог.
    коэф: X"), если поле суммы ещё не видно. Возвращает True, если
    купон в развёрнутом виде (было изначально или удалось развернуть).
    """
    try:
        if driver.find_elements(By.XPATH, f"//input[contains(@class,'{BETSLIP_STAKE_INPUT_CLASS}')]"):
            return True  # уже развёрнут
    except Exception:
        pass

    try:
        headers = driver.find_elements(By.XPATH, f"//*[contains(@class,'{BETSLIP_HEADER_CLASS}')]")
        if not headers:
            headers = driver.find_elements(By.XPATH, "//*[contains(.,'Итог. коэф')]")
        for h in headers:
            try:
                h.click()
                time.sleep(1)
                if driver.find_elements(By.XPATH, f"//input[contains(@class,'{BETSLIP_STAKE_INPUT_CLASS}')]"):
                    log("Купон развёрнут кликом по шапке.")
                    return True
            except Exception:
                continue
    except Exception:
        pass

    log("⚠️ Не удалось развернуть купон (шапка не найдена/не сработала).")
    return False


def set_stake_and_confirm(driver, amount: float) -> Dict[str, Any]:
    """
    Разворачивает купон, находит поле суммы (класс sp-selection-input —
    НЕ кнопки +100/+500/+1000, это надбавки, а не пресеты), вписывает
    сумму вручную и жмёт кнопку ставки.

    Возвращает {"status": "CONFIRMED"|"FAILED", "detail": str}
    """
    if not _expand_betslip_if_collapsed(driver):
        return {"status": "FAILED", "detail": "купон свёрнут, не смог развернуть"}

    try:
        stake_input = driver.find_element(
            By.XPATH, f"//input[contains(@class,'{BETSLIP_STAKE_INPUT_CLASS}')]"
        )
        stake_input.click()
        # .clear() часто не триггерит onChange у React-инпутов —
        # выделяем всё и удаляем через клавиатуру, потом вводим заново.
        stake_input.send_keys(Keys.CONTROL, "a")
        stake_input.send_keys(Keys.DELETE)
        stake_input.send_keys(str(amount))
        time.sleep(0.5)
    except Exception as e:
        return {"status": "FAILED", "detail": f"поле суммы (sp-selection-input) не найдено/не ввёл: {e}"}

    try:
        buttons = driver.find_elements(By.XPATH, ".//button")
        confirm_words = ["сделать ставку", "принять изменения", "подтвердить",
                          "поставить", "confirm", "place bet", "apply", "ok"]
        for btn in buttons:
            txt = (btn.text or "").strip().lower()
            cls = (btn.get_attribute("class") or "").lower()
            if any(w in txt for w in confirm_words) or BETSLIP_CONFIRM_CLASS_HINT in cls:
                disabled = btn.get_attribute("disabled") is not None
                if disabled:
                    log(f"Кнопка ставки '{txt}' есть, но пока DISABLED — жду и пробую снова через 1с.")
                    time.sleep(1)
                    disabled = btn.get_attribute("disabled") is not None
                if disabled:
                    return {"status": "FAILED", "detail": f"кнопка '{txt}' осталась disabled после ввода суммы"}
                btn.click()
                return {"status": "CONFIRMED", "detail": f"нажал кнопку '{txt}'"}
        return {"status": "FAILED", "detail": "кнопка 'Сделать ставку' не найдена"}
    except Exception as e:
        return {"status": "FAILED", "detail": f"ошибка подтверждения: {e}"}


# ═══════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ — вызывается из sport_bot
# ═══════════════════════════════════════════════════════════════

def place_bet_from_signal(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """
    Возвращает подробный статус (НЕ просто True/False — раньше было
    так, теперь по запросу пользователя статусы детальные):
      {"status": "PLACED"|"DRY_RUN"|"FAILED"|"NOT_FOUND"|"BLOCKED"|
                  "NOT_LOGGED_IN"|"MATCH_NOT_FOUND"|"ERROR",
       "detail": str, "stake": float, "odds": float|None}
    sport_bot (v19) читает это поле и решает, что писать в Telegram
    и что сохранять в open_bets для последующего расчёта выигрыша.
    """
    main = bundle.get("main", {})
    bet_type = main.get("type", "")
    line = float(main.get("line", 0.0))
    home = bundle.get("home", "")
    away = bundle.get("away", "")

    log(f"═══ place_bet_from_signal: {home} vs {away} | {bet_type} {line} "
        f"| DRY_RUN={AUTO_BET_DRY_RUN} ═══")

    if not SELENIUM_AVAILABLE:
        log("❌ selenium не установлен.")
        return {"status": "ERROR", "detail": "selenium не установлен", "stake": STAKE_AMOUNT, "odds": None}

    try:
        driver = get_driver()
    except Exception as e:
        log(f"❌ Не смог подключиться к Chrome: {e}")
        return {"status": "ERROR", "detail": f"нет подключения к Chrome: {e}", "stake": STAKE_AMOUNT, "odds": None}

    login_state = detect_login_state(driver)
    if login_state == "OUT":
        log("❌ Не залогинен — ставка пропущена.")
        return {"status": "NOT_LOGGED_IN", "detail": "вышел из профиля", "stake": STAKE_AMOUNT, "odds": None}

    match = find_match_by_teams(driver, home, away)
    if not match["found"]:
        log(f"❌ Матч '{home}' vs '{away}' не найден на бирже (confidence={match['confidence']:.2f}).")
        return {"status": "MATCH_NOT_FOUND",
                "detail": f"матч не найден на бирже (уверенность {match['confidence']*100:.0f}%)",
                "stake": STAKE_AMOUNT, "odds": None}

    try:
        match["element"].click()
        time.sleep(3)
    except Exception as e:
        log(f"❌ Не смог открыть страницу матча: {e}")
        return {"status": "ERROR", "detail": f"не открылась страница матча: {e}", "stake": STAKE_AMOUNT, "odds": None}

    outcome = find_and_click_outcome(driver, bet_type, line)

    if outcome["status"] == "NOT_FOUND":
        return {"status": "NOT_FOUND", "detail": "рынок/линия не найдены на бирже",
                "stake": STAKE_AMOUNT, "odds": None}

    if outcome["status"] == "BLOCKED":
        return {"status": "BLOCKED", "detail": "рынок заблокирован 🔒 (уже поздно)",
                "stake": STAKE_AMOUNT, "odds": None}

    # FOUND_OPEN
    odds = outcome.get("odds")
    if odds is None:
        odds = _extract_odds_from_text(outcome["odds_text"])

    if AUTO_BET_DRY_RUN:
        log(f"🧪 DRY-RUN: нашёл открытый исход '{outcome['odds_text']}', НЕ кликаю.")
        return {"status": "DRY_RUN",
                "detail": f"нашёл бы и поставил '{outcome['odds_text']}'",
                "stake": STAKE_AMOUNT, "odds": odds}

    try:
        outcome["element"].click()
        time.sleep(2)
    except Exception as e:
        log(f"❌ Клик по исходу не удался: {e}")
        return {"status": "FAILED", "detail": f"клик по исходу не удался: {e}",
                "stake": STAKE_AMOUNT, "odds": odds}

    confirm = set_stake_and_confirm(driver, STAKE_AMOUNT)
    if confirm["status"] != "CONFIRMED":
        log(f"❌ Подтверждение не удалось: {confirm['detail']}")
        return {"status": "FAILED", "detail": confirm["detail"], "stake": STAKE_AMOUNT, "odds": odds}

    log(f"✅ Ставка поставлена: {home} vs {away} | {bet_type} {line} | "
        f"{STAKE_AMOUNT} лей | кэф≈{odds}")
    return {"status": "PLACED", "detail": confirm["detail"], "stake": STAKE_AMOUNT, "odds": odds}


if __name__ == "__main__":
    print("Это библиотечный модуль — импортируется из sport_bot.")
    print("Для ручного теста см. inspect_7777.py --mode discover / keepalive.")
