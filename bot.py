# -*- coding: utf-8 -*-
import time
import math
import json
import logging
from typing import Optional, Tuple, Dict, Any
from decimal import Decimal, ROUND_DOWN

# pybit v5 (5.13.x): правильный импорт
from pybit.unified_trading import HTTP  # docs/examples use unified_trading HTTP

# ================== НАСТРОЙКИ ==================
API_KEY = ""
API_SECRET = ""

symbol = "TRXUSDT"

TESTNET = True

BALANCE_CAP_USDT = 20.0      # лимит (не тратим больше этого)
ORDER_COUNT = 2              # строго 2 BUY ордера
USDT_PER_ORDER = 5.2         # каждый BUY примерно на 5.2 USDT

GRID_STEP_PERCENT = 0.5      # шаг сетки (в %)
STOP_LOSS_PERCENT = 35       # глобальный стоп (от стартовой цены)
GRID_REBUILD_THRESHOLD = 2.0 # пересборка сетки, если цена ушла на X%

CHECK_DELAY = 2              # секунд

LOG_FILE = "bybit_grid_bot.log"

# ================== ЛОГИ ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def log(msg: str):
    logging.info(msg)

# ================== КЛИЕНТ ==================
client = HTTP(
    api_key=API_KEY,
    api_secret=API_SECRET,
    testnet=TESTNET
)

# ================== УТИЛИТЫ ОКРУГЛЕНИЯ ==================
def _dec(x) -> Decimal:
    return Decimal(str(x))

def floor_to_step(value: float, step: float) -> float:
    """
    Округление ВНИЗ по step (важно для qtyStep и tickSize).
    """
    v = _dec(value)
    s = _dec(step)
    if s <= 0:
        return float(v)
    q = (v / s).to_integral_value(rounding=ROUND_DOWN) * s
    return float(q)

def fmt_by_step(value: float, step: float) -> str:
    """
    Форматируем число строкой без лишних хвостов, строго под шаг.
    """
    v = _dec(floor_to_step(value, step))
    s = _dec(step)
    places = max(0, -s.as_tuple().exponent)
    return f"{v:.{places}f}"

# ================== MARKET / FILTERS ==================
def get_filters():
    """
    Берём tickSize и qtyStep для символа (Spot instruments-info).
    """
    data = client.get_instruments_info(category="spot", symbol=symbol)
    lst = data.get("result", {}).get("list", [])
    if not lst:
        raise RuntimeError("Не смог получить instruments-info (пусто).")
    info = lst[0]

    price_filter = info.get("priceFilter", {}) or {}
    lot_filter = info.get("lotSizeFilter", {}) or {}

    tick_size = float(price_filter.get("tickSize") or 0.00001)
    qty_step = float(lot_filter.get("qtyStep") or 0.01)

    min_qty = float(lot_filter.get("minOrderQty") or 0)
    min_amt = float(lot_filter.get("minOrderAmt") or 0)

    return tick_size, qty_step, min_qty, min_amt

def get_price() -> float:
    """
    Берём lastPrice.
    """
    data = client.get_tickers(category="spot", symbol=symbol)
    lst = data.get("result", {}).get("list", [])
    if not lst:
        return 0.0
    return float(lst[0].get("lastPrice") or 0.0)

# ================== BALANCE (UNIFIED) ==================
def get_coin_balance(coin: str) -> float:
    """
    Баланс из UNIFIED кошелька.
    """
    data = client.get_wallet_balance(accountType="UNIFIED", coin=coin)
    lst = data.get("result", {}).get("list", [])
    if not lst:
        return 0.0
    coins = lst[0].get("coin", [])
    for c in coins:
        if c.get("coin") == coin:
            for key in ("availableToWithdraw", "availableBalance", "walletBalance", "free"):
                if c.get(key) is not None:
                    try:
                        v = c.get(key)
                        if v == "" or v is None:
                            continue
                        return float(v)
                    except:
                        pass
            try:
                return float(c.get("walletBalance") or 0.0)
            except:
                return 0.0
    return 0.0

def get_usdt_balance() -> float:
    return get_coin_balance("USDT")

def get_trx_balance() -> float:
    return get_coin_balance("TRX")

# ===================== БЛОК B1 (НОВОЕ): зарезервировано TRX в SELL =====================
def get_reserved_trx_in_open_sells(qty_step: float) -> float:
    """
    Сколько TRX уже занято в открытых SELL ордерах.
    """
    try:
        res = client.get_open_orders(category="spot", symbol=symbol)
        orders = res.get("result", {}).get("list", []) or []
        reserved = 0.0
        for o in orders:
            if (o.get("side") or "").lower() == "sell":
                try:
                    q = float(o.get("qty") or 0.0)
                except:
                    q = 0.0
                reserved += q
        return floor_to_step(reserved, qty_step)
    except Exception as e:
        log(f"⚠️ ERROR reserved calc: {e}")
        return 0.0
# ========================================================================

# ================== ORDERS ==================
def cancel_all_open_orders():
    log("🧹 Clearing old orders...")
    try:
        res = client.get_open_orders(category="spot", symbol=symbol)
        orders = res.get("result", {}).get("list", []) or []
        for o in orders:
            oid = o.get("orderId")
            if oid:
                client.cancel_order(category="spot", symbol=symbol, orderId=oid)
        log("🧹 Old orders cleared")
    except Exception as e:
        log(f"⚠️ ERROR while clearing orders: {e}")

def place_limit_buy(price: float, usdt_amount: float, tick_size: float, qty_step: float, min_qty: float, min_amt: float):
    """
    BUY на ~usdt_amount по цене price.
    """
    if price <= 0:
        return None

    price_rounded = floor_to_step(price, tick_size)
    if price_rounded <= 0:
        return None

    qty = usdt_amount / price_rounded
    qty_rounded = floor_to_step(qty, qty_step)

    if min_qty > 0 and qty_rounded < min_qty:
        qty_rounded = floor_to_step(min_qty, qty_step)

    notional = qty_rounded * price_rounded
    if min_amt > 0 and notional < min_amt:
        qty_need = (min_amt / price_rounded)
        qty_rounded = floor_to_step(qty_need, qty_step)

    if qty_rounded <= 0:
        return None

    if get_usdt_balance() < usdt_amount:
        log("⚠️ Not enough USDT")
        return None

    try:
        client.place_order(
            category="spot",
            symbol=symbol,
            side="Buy",
            orderType="Limit",
            timeInForce="GTC",
            qty=fmt_by_step(qty_rounded, qty_step),
            price=fmt_by_step(price_rounded, tick_size)
        )
        log(f"🟢 BUY placed @ {fmt_by_step(price_rounded, tick_size)} | ~{usdt_amount} USDT")
        return True
    except Exception as e:
        log(f"⚠️ ERROR BUY: {e}")
        return None

def place_limit_sell_from_fill(buy_price: float, qty: float, tick_size: float, qty_step: float):
    """
    SELL (TP) после BUY fill.
    """
    if buy_price <= 0 or qty <= 0:
        return False

    sell_price = buy_price * (1 + GRID_STEP_PERCENT / 100.0)
    sell_price = floor_to_step(sell_price, tick_size)
    qty = floor_to_step(qty, qty_step)

    if sell_price <= 0 or qty <= 0:
        return False

    # ===================== БЛОК B2 (НОВОЕ): продаём только СВОБОДНЫЙ TRX =====================
    trx_total = get_trx_balance()
    trx_reserved = get_reserved_trx_in_open_sells(qty_step)
    trx_free = floor_to_step(max(0.0, trx_total - trx_reserved), qty_step)

    if trx_free <= 0:
        return False

    if qty > trx_free:
        qty = trx_free  # продаём сколько реально свободно
    # ========================================================================

    try:
        client.place_order(
            category="spot",
            symbol=symbol,
            side="Sell",
            orderType="Limit",
            timeInForce="GTC",
            qty=fmt_by_step(qty, qty_step),
            price=fmt_by_step(sell_price, tick_size)
        )
        log(f"🔄 BUY filled @ {fmt_by_step(buy_price, tick_size)} → SELL @ {fmt_by_step(sell_price, tick_size)}")
        return True
    except Exception as e:
        log(f"⚠️ ERROR SELL: {e}")
        return False

# ================== GRID ==================
grid_prices = []

def build_grid(base_price: float, tick_size: float, qty_step: float, min_qty: float, min_amt: float):
    """
    Строим ровно 2 BUY ордера с шагом GRID_STEP_PERCENT, каждый на 5.2 USDT.
    """
    global grid_prices
    grid_prices = []

    for i in range(ORDER_COUNT):
        p = base_price * (1 - (GRID_STEP_PERCENT / 100.0) * i)
        p = floor_to_step(p, tick_size)
        if p <= 0:
            continue

        if get_usdt_balance() < USDT_PER_ORDER:
            log("⚠️ Not enough USDT")
            break

        ok = place_limit_buy(p, USDT_PER_ORDER, tick_size, qty_step, min_qty, min_amt)
        if ok:
            grid_prices.append(p)

# ================== ОСНОВНОЙ ЗАПУСК ==================
def main():
    tick_size, qty_step, min_qty, min_amt = get_filters()

    # ===================== БЛОК A (НОВОЕ): отметка времени старта =====================
    bot_start_ms = int(time.time() * 1000)
    # ===============================================================================

    cancel_all_open_orders()

    start_price = get_price()
    if start_price <= 0:
        log("⚠️ Price is 0 — stop")
        return

    stop_price = start_price * (1 - STOP_LOSS_PERCENT / 100.0)
    stop_price = floor_to_step(stop_price, tick_size)

    log("🚀 GRID BOT STARTED (BYBIT TESTNET)" if TESTNET else "🚀 GRID BOT STARTED (BYBIT REAL)")
    log(f"📉 Stop price: {fmt_by_step(stop_price, tick_size)}")
    log(f"🧾 Balance cap: {BALANCE_CAP_USDT} USDT | Orders: {ORDER_COUNT} | Per order: {USDT_PER_ORDER} USDT")
    log(f"🔧 Filters: tickSize={tick_size} qtyStep={qty_step}")

    build_grid(start_price, tick_size, qty_step, min_qty, min_amt)

    processed_fills = set()
    processed_buys = set()  # защита: SELL ставим 1 раз на BUY fill

    while True:
        try:
            current_price = get_price()
            if current_price <= 0:
                log("⚠️ Price is 0 — wait")
                time.sleep(CHECK_DELAY)
                continue

            if current_price <= stop_price:
                log("🛑 STOP LOSS HIT → cancel all and exit")
                cancel_all_open_orders()
                break

            if grid_prices:
                mid = grid_prices[0]
                change = abs(current_price - mid) / mid * 100.0
                if change >= GRID_REBUILD_THRESHOLD:
                    log(f"🔄 Price moved {change:.2f}% → Rebuilding grid")
                    cancel_all_open_orders()
                    build_grid(current_price, tick_size, qty_step, min_qty, min_amt)

            new_activity = False

            hist = client.get_order_history(category="spot", symbol=symbol, limit=50)
            orders = hist.get("result", {}).get("list", []) or []

            for o in orders:
                oid = o.get("orderId")
                status = o.get("orderStatus") or o.get("status")
                side = o.get("side")

                # ===================== БЛОК A2 (НОВОЕ): игнорируем историю ДО запуска =====================
                try:
                    created_ms = int(o.get("createdTime") or 0)
                except:
                    created_ms = 0
                if created_ms and created_ms < bot_start_ms:
                    continue
                # ========================================================================

                if not oid or oid in processed_fills:
                    continue
                if status not in ("Filled", "FILLED"):
                    continue

                processed_fills.add(oid)
                new_activity = True

                if side == "Buy":
                    if oid in processed_buys:
                        continue
                    processed_buys.add(oid)

                    try:
                        fill_price = float(o.get("avgPrice") or o.get("price") or 0.0)
                    except:
                        fill_price = 0.0

                    try:
                        fill_qty = float(o.get("cumExecQty") or o.get("executedQty") or o.get("qty") or 0.0)
                    except:
                        fill_qty = 0.0

                    if fill_price <= 0 or fill_qty <= 0:
                        continue

                    ok = place_limit_sell_from_fill(fill_price, fill_qty, tick_size, qty_step)
                    if not ok:
                        log("⚠️ Could not place SELL after BUY fill")

                elif side == "Sell":
                    try:
                        fill_price = float(o.get("avgPrice") or o.get("price") or 0.0)
                    except:
                        fill_price = 0.0
                    try:
                        fill_qty = float(o.get("cumExecQty") or o.get("executedQty") or o.get("qty") or 0.0)
                    except:
                        fill_qty = 0.0
                    log(f"💰 SELL filled @ {fmt_by_step(fill_price, tick_size)} | qty={fmt_by_step(fill_qty, qty_step)}")

            if not new_activity:
                log("⏳ Waiting for next order...")

            time.sleep(CHECK_DELAY)

        except KeyboardInterrupt:
            log("🧠 Bot stopped manually.")
            break
        except Exception as e:
            log(f"⚠️ ERROR: {e}")
            time.sleep(5)

    log("🧠 BOT FINISHED")

if __name__ == "__main__":
    main()
