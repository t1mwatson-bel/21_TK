import os
import json

from datetime import datetime

from config import (
    BANK_FILE,
    START_BALANCE,
    START_BET,
    MOSCOW_TZ,
)

from coefs import get_dealer_cf


# =====================================================================
# КОНСТАНТЫ ЛОГИКИ СТАВОК
# =====================================================================

# Шаг внутри цикла догонов: Д0=50, Д1=75, Д2=100, Д3=125
DOGON_STEP = 25.0

# Шаг между циклами: если все 4 проиграли, база растёт на 100
# (50 → 150 → 250 → ...)
CYCLE_STEP = 100.0

# Количество догонов в одном цикле (Д0 + 3)
DOGONS_PER_CYCLE = 4


# =====================================================================
# СОСТОЯНИЕ БАНКА (в памяти)
# =====================================================================

bank_state = {
    "balance": START_BALANCE,
    "current_bet": START_BET,
    "started_at": None,
    "history": [],
}


# =====================================================================
# ЗАГРУЗКА / СОХРАНЕНИЕ
# =====================================================================

def load_bank():
    """Загружает состояние банка из файла."""

    global bank_state

    try:
        if not os.path.exists(BANK_FILE):
            bank_state = {
                "balance": START_BALANCE,
                "current_bet": START_BET,
                "started_at": datetime.now(MOSCOW_TZ).isoformat(),
                "history": [],
            }
            save_bank()
            return

        with open(BANK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        bank_state = {
            "balance": float(data.get("balance", START_BALANCE)),
            "current_bet": float(data.get("current_bet", START_BET)),
            "started_at": data.get("started_at"),
            "history": data.get("history", []),
        }

    except Exception as e:
        print(f"⚠️ Ошибка чтения банка: {e}", flush=True)
        bank_state = {
            "balance": START_BALANCE,
            "current_bet": START_BET,
            "started_at": datetime.now(MOSCOW_TZ).isoformat(),
            "history": [],
        }


def save_bank():
    """Сохраняет состояние банка в файл."""

    try:
        tmp = BANK_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bank_state, f, ensure_ascii=False, indent=2)

        os.replace(tmp, BANK_FILE)

    except Exception as e:
        print(f"⚠️ Ошибка сохранения банка: {e}", flush=True)


# =====================================================================
# ГЕТТЕРЫ
# =====================================================================

def get_balance():
    return float(bank_state["balance"])


def get_current_bet():
    return float(bank_state["current_bet"])


# =====================================================================
# ЛОГИКА СТАВОК
# =====================================================================

def bet_for_dogon(base_bet, dogon_index):
    """
    Считает ставку для конкретного догона.

    dogon_index: 0 = Д0, 1 = Д1, 2 = Д2, 3 = Д3.

    Логика: base_bet + dogon_index * 25
    Пример при base_bet = 50:
        Д0 = 50
        Д1 = 75
        Д2 = 100
        Д3 = 125
    """

    return round(base_bet + dogon_index * DOGON_STEP, 2)


def next_cycle_base(current_base):
    """
    Считает базу для следующего цикла (после проигрыша всех 4 игр).

    Логика: current_base + 100
    Пример:
        50 → 150
        150 → 250
        250 → 350
    """

    return round(current_base + CYCLE_STEP, 2)


# =====================================================================
# ПРИМЕНЕНИЕ РЕЗУЛЬТАТА ПРОГНОЗА
# =====================================================================

def apply_win(prediction, dogon_index, bet_amount, cf=None):
    """
    Прогноз выиграл.

    cf — коэффициент на карту (если не передан — берём из coefs.py по карте).

    Списываем все проигранные догоны до win, начисляем выплату,
    сбрасываем current_bet на START_BET.
    """

    if cf is None:
        cf = get_dealer_cf(prediction.get("predicted_card", ""))

    payout = round(bet_amount * cf, 2)

    # ---------------------------------------------------------------
    # Считаем потери на предыдущих догонах (Д0..Д(dogon_index-1))
    # ---------------------------------------------------------------

    total_lost = 0.0

    for i in range(dogon_index):
        total_lost += bet_for_dogon(
            prediction.get("base_bet", bank_state["current_bet"]),
            i,
        )

    # ---------------------------------------------------------------
    # Обновляем баланс
    # ---------------------------------------------------------------

    bank_state["balance"] = round(
        bank_state["balance"] + payout - bet_amount - total_lost,
        2,
    )

    # После захода — сбрасываем на стартовую ставку
    bank_state["current_bet"] = START_BET

    profit = round(payout - bet_amount - total_lost, 2)

    record = {
        "type": "win",
        "game_number": prediction.get("target_number"),
        "predicted_card": prediction.get("predicted_card"),
        "dogon": dogon_index,
        "bet": bet_amount,
        "cf": cf,
        "payout": payout,
        "total_lost": total_lost,
        "profit": profit,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💰 WIN: {prediction.get('predicted_card')} × {cf} | "
        f"ставка {bet_amount} ₽ → выплата {payout} ₽ "
        f"(профит {profit} ₽) | баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


def apply_lose(prediction, bet_amount):
    """
    Прогноз проиграл (все 4 игры).

    Списываем сумму всех 4 ставок с баланса,
    увеличиваем базу следующего цикла на +100.
    """

    base_bet = prediction.get("base_bet", bank_state["current_bet"])

    # ---------------------------------------------------------------
    # Считаем сумму всех 4 ставок цикла
    # ---------------------------------------------------------------

    total_lost = 0.0

    for i in range(DOGONS_PER_CYCLE):
        total_lost += bet_for_dogon(base_bet, i)

    bank_state["balance"] = round(
        bank_state["balance"] - total_lost,
        2,
    )

    # ---------------------------------------------------------------
    # База следующего цикла: +100
    # ---------------------------------------------------------------

    new_base = next_cycle_base(base_bet)

    bank_state["current_bet"] = new_base

    record = {
        "type": "lose",
        "game_number": prediction.get("target_number"),
        "bet": base_bet,
        "total_lost": total_lost,
        "next_base": new_base,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"❌ LOSE: потеряно {total_lost} ₽ | "
        f"следующая база {new_base} ₽ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


def apply_return(prediction, bet_amount):
    """
    Возврат ♻️ (таймаут >30 мин).

    Ставка возвращается, баланс не меняется,
    current_bet сбрасывается на START_BET.
    """

    bank_state["current_bet"] = START_BET

    record = {
        "type": "return",
        "game_number": prediction.get("target_number"),
        "bet": bet_amount,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"♻️ ВОЗВРАТ: ставка сброшена на {START_BET} ₽ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


# =====================================================================
# СТАТИСТИКА БАНКА (для сайта)
# =====================================================================

def get_bank_summary():
    """Возвращает сводку по банку для сайта."""

    balance = bank_state["balance"]
    profit = round(balance - START_BALANCE, 2)
    roi = round((profit / START_BALANCE) * 100, 2) if START_BALANCE else 0.0

    return {
        "balance": balance,
        "start_balance": START_BALANCE,
        "profit": profit,
        "roi": roi,
        "current_bet": bank_state["current_bet"],
        "started_at": bank_state.get("started_at"),
    }


# =====================================================================
# ИСТОРИЯ (для сайта)
# =====================================================================

def get_bank_history(limit=100):
    """Возвращает последние N записей истории."""

    history = bank_state.get("history", [])
    return history[-limit:]