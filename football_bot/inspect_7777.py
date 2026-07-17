# inspect_7777.py — РАЗВЕДЧИК интерфейса ставок 7777.md (read-only)
# ═══════════════════════════════════════════════════════════════
# Делает 3 вещи, НИЧЕГО не ставит и пароль не трогает:
#
#   1) --mode discover  — выгружает ВСЕ кнопки/рынки/исходы с их
#      селекторами, текстом и координатами в JSON + ищет минимальную
#      ставку. Из этого файла я потом построю auto_bet_7777.py.
#
#   2) --mode keepalive — держит сессию живой: каждые несколько
#      секунд делает лёгкое действие (микро-движение мыши + скролл),
#      чтобы сайт НЕ выкинул из профиля по бездействию. Каждый цикл
#      проверяет, залогинен ли ты ещё, и пишет статус. Если выкинуло —
#      громко сообщает (а не молча ломается, как сейчас у авто-ставки).
#
#   3) --mode both — keepalive + периодические снимки discover.
#
# ЗАПУСК (Chrome с портом 9222 уже открыт и залогинен, как раньше):
#   pip install selenium
#   # разведка интерфейса (один прогон):
#   python inspect_7777.py --mode discover --url "URL_ЛЮБОГО_LIVE_МАТЧА"
#   # держать сессию живой + следить за выходом (например 30 мин):
#   python inspect_7777.py --mode keepalive --url "URL_МАТЧА" --minutes 30
#
# ⚠️ Про минимальную ставку: она обычно видна ТОЛЬКО в купоне,
# когда в него добавлен хотя бы один исход. Поэтому для точного
# захвата мин.ставки: РУКАМИ кликни любой коэффициент (чтобы купон
# открылся), и только потом запусти --mode discover — скрипт
# выгрузит и содержимое купона тоже.
# ═══════════════════════════════════════════════════════════════

import argparse
import json
import os
import time
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains

OUT_DIR = os.path.join("data", "inspect")
os.makedirs(OUT_DIR, exist_ok=True)

# Слова, по которым угадываем состояние логина (по видимому тексту).
# Если ты ЗАЛОГИНЕН — обычно виден баланс/пополнить/выход/кабинет.
LOGGED_IN_MARKERS = [
    "пополнить", "депозит", "вывод", "выход", "мой профиль",
    "личный кабинет", "баланс", "mdl", "кабинет", "мои ставки",
]
# Если ВЫШЕЛ — обычно видна кнопка входа/регистрации.
LOGGED_OUT_MARKERS = [
    "войти", "вход", "регистрация", "log in", "sign in", "авторизация",
]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def attach(port: int = 9222):
    opts = Options()
    opts.debugger_address = f"127.0.0.1:{port}"
    return webdriver.Chrome(options=opts)


def detect_login_state(text_lower: str) -> str:
    """Возвращает 'IN' | 'OUT' | 'UNKNOWN' по видимому тексту страницы."""
    out_hit = any(m in text_lower for m in LOGGED_OUT_MARKERS)
    in_hit = any(m in text_lower for m in LOGGED_IN_MARKERS)
    if in_hit and not out_hit:
        return "IN"
    if out_hit and not in_hit:
        return "OUT"
    if in_hit and out_hit:
        # оба есть — обычно это залогинен (кнопка выхода + баланс),
        # но помечаем как неоднозначно, чтобы ты глазами проверил
        return "UNKNOWN"
    return "UNKNOWN"


def keep_alive_action(driver) -> None:
    """Лёгкое действие, сбрасывающее таймер бездействия сайта:
    микро-движение мыши + микроскролл. Не кликает ничего важного."""
    try:
        ActionChains(driver).move_by_offset(2, 0).move_by_offset(-2, 0).perform()
    except Exception:
        pass
    try:
        driver.execute_script(
            "window.scrollBy(0,2); setTimeout(function(){window.scrollBy(0,-2);}, 60);"
        )
    except Exception:
        pass


# ── JS, собирающий все интерактивные элементы страницы ──────────
_JS_COLLECT = r"""
const out = [];
const sel = 'button, a[role="button"], [role="button"], [market-vis-type-name], '
          + '[data-widget], [class*="selection"], [class*="odd"], [class*="outcome"]';
const els = document.querySelectorAll(sel);
els.forEach(e => {
  const r = e.getBoundingClientRect();
  // берём только видимые элементы
  if (r.width === 0 || r.height === 0) return;
  out.push({
    tag: e.tagName,
    text: (e.innerText || '').trim().slice(0, 80),
    cls: (typeof e.className === 'string' ? e.className : '').slice(0, 160),
    id: e.id || '',
    market: e.getAttribute('market-vis-type-name') || '',
    dataWidget: e.getAttribute('data-widget') || '',
    disabled: e.disabled === true || e.getAttribute('disabled') !== null,
    x: Math.round(r.x), y: Math.round(r.y),
    w: Math.round(r.width), h: Math.round(r.height)
  });
});
return out;
"""


def cmd_discover(driver, url: str) -> None:
    if url:
        driver.get(url)
        time.sleep(6)  # дать виджетам догрузиться

    text = driver.find_element("tag name", "body").text
    login_state = detect_login_state(text.lower())

    try:
        elements = driver.execute_script(_JS_COLLECT)
    except Exception as e:
        elements = []
        print(f"⚠️ Не смог собрать элементы: {e}")

    # Группируем рынки по market-vis-type-name
    markets = {}
    for el in elements:
        m = el.get("market")
        if m:
            markets.setdefault(m, 0)
            markets[m] += 1

    tag = now_str()
    json_path = os.path.join(OUT_DIR, f"discover_{tag}.json")
    txt_path = os.path.join(OUT_DIR, f"discover_{tag}.txt")

    payload = {
        "url": url,
        "login_state": login_state,
        "markets_found": markets,
        "elements_count": len(elements),
        "elements": elements,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Человекочитаемая краткая сводка
    lines = [
        f"URL: {url}",
        f"Состояние логина: {login_state}",
        f"Найдено интерактивных элементов: {len(elements)}",
        "",
        "РЫНКИ (market-vis-type-name):",
    ]
    for m, cnt in sorted(markets.items()):
        lines.append(f"  • {m}  (элементов: {cnt})")
    lines.append("")
    lines.append("КНОПКИ/ИСХОДЫ С ТЕКСТОМ (первые 60):")
    shown = 0
    for el in elements:
        if el["text"] and shown < 60:
            flag = " [DISABLED]" if el["disabled"] else ""
            lines.append(f"  [{el['tag']}] '{el['text']}' "
                         f"(market='{el['market']}' widget='{el['dataWidget']}'){flag}")
            shown += 1
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ Готово.\n  JSON: {json_path}\n  Сводка: {txt_path}")
    print(f"  Состояние логина: {login_state}")
    print(f"  Рынки: {list(markets.keys())}")
    print("\nПришли мне ОБА файла (перетащи в чат) — по ним соберу авто-ставку.")


def cmd_keepalive(driver, url: str, minutes: int, interval: int) -> None:
    if url:
        driver.get(url)
        time.sleep(5)

    tag = now_str()
    log_path = os.path.join(OUT_DIR, f"keepalive_{tag}.csv")
    end = time.time() + minutes * 60
    tick = 0
    prev_state = None

    print(f"Держу сессию живой {minutes} мин, действие каждые {interval}с.")
    print(f"Лог: {log_path}")

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("timestamp,login_state\n")
        while time.time() < end:
            tick += 1
            keep_alive_action(driver)
            try:
                text = driver.find_element("tag name", "body").text
                state = detect_login_state(text.lower())
            except Exception as e:
                state = f"ERROR:{e}"

            f.write(f"{now_str()},{state}\n")
            f.flush()

            # Сообщаем громко только при СМЕНЕ состояния
            if state != prev_state:
                if state == "OUT":
                    print(f"[{now_str()}] 🚨 ВЫШЕЛ ИЗ ПРОФИЛЯ — нужно перелогиниться!")
                elif state == "IN":
                    print(f"[{now_str()}] ✅ Залогинен, сессия живая.")
                else:
                    print(f"[{now_str()}] ❓ Состояние неоднозначное ({state}) — проверь глазами.")
                prev_state = state

            time.sleep(interval)

    print(f"\nЗавершено. Лог: {log_path}")
    print("Пришли мне этот CSV — увидим, помог ли keep-alive держать сессию,")
    print("и через сколько именно сайт выкидывает, если всё же выкинул.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["discover", "keepalive", "both"], required=True)
    ap.add_argument("--url", default="", help="URL live-матча (или пусто — работать на текущей странице)")
    ap.add_argument("--minutes", type=int, default=30, help="Сколько минут держать сессию (keepalive)")
    ap.add_argument("--interval", type=int, default=45,
                     help="Каждые сколько секунд делать keep-alive действие (меньше 10-мин таймаута сайта!)")
    ap.add_argument("--port", type=int, default=9222)
    args = ap.parse_args()

    driver = attach(args.port)

    if args.mode == "discover":
        cmd_discover(driver, args.url)
    elif args.mode == "keepalive":
        cmd_keepalive(driver, args.url, args.minutes, args.interval)
    else:  # both
        cmd_discover(driver, args.url)
        cmd_keepalive(driver, "", args.minutes, args.interval)


if __name__ == "__main__":
    main()
